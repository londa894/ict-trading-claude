"""Batch backtest: sweep many windows across the dataset, pool every closed trade.

Single 2-week windows give n<=1, which cannot support any statistical claim. This
generates consecutive windows across a date range, runs each through the REAL backtest
engine (run_variant), and APPENDS every closed trade to a JSONL file as it goes, so a
long sweep survives interruption. Re-running resumes: windows already in the file are
skipped.

Both fix-flags are pinned on. Windows overlapping the two known large data gaps are
skipped. Research only: risk/news/verdict gates are not applied (backtest design).

Usage:
  MARKET_DATA_PROVIDER=historical_file DATA_ROOT=... \\
  python -m scripts.batch_backtest 2018-01-01 2024-12-31 [--weeks 2] [--out FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.contracts import load_spec
from app.domain.enums import BacktestTradeStatus, Timeframe, TradeResult
from app.providers.registry import default_registry
from app.services.backtest.engine import run_variant
from app.services.backtest.models import BacktestVariant
from app.services.candles.service import CandleService
from app.services.entry.models import EntryConfig
from app.services.paper.models import AssumedCosts
from app.services.setup_state.models import SetupAnalysis, SetupConfig
from app.services.setup_state.service import SetupService

SYMBOL = "XAUUSD"
SPEC = Path(__file__).resolve().parents[3] / "packages" / "strategy-spec" / "setup.json"
WINS = (TradeResult.FULL_WIN, TradeResult.PARTIAL_WIN)

# Large data gaps (M15 longGaps > 200h); any window overlapping these is skipped.
GAPS = [
    (datetime(2025, 9, 12, tzinfo=UTC), datetime(2025, 10, 15, tzinfo=UTC)),
    (datetime(2026, 1, 13, tzinfo=UTC), datetime(2026, 1, 22, tzinfo=UTC)),
]


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def overlaps_gap(start: datetime, end: datetime) -> bool:
    return any(start < g1 and end > g0 for g0, g1 in GAPS)


async def load_range(candles: CandleService, tf: Timeframe, start: datetime, end: datetime) -> list:
    by_open: dict = {}
    chunk_end = end
    for _ in range(400):
        series = await candles.load_series(SYMBOL, tf, 1000, chunk_end)
        fresh = [c for c in series.candles if c.is_closed and c.close_time <= end and c.open_time >= start]
        new = [c for c in fresh if c.open_time not in by_open]
        for c in new:
            by_open[c.open_time] = c
        if not series.candles or not new:
            break
        chunk_end = min(c.open_time for c in series.candles)
        if chunk_end <= start:
            break
    return sorted(by_open.values(), key=lambda c: c.open_time)


async def run_window(
    candles: CandleService, setup_cfg: SetupConfig, start: datetime, end: datetime
) -> list[dict]:
    tf = setup_cfg.setup_timeframe
    step_series = await candles.load_series(SYMBOL, tf, 1000, end)
    step_bars = [c for c in step_series.candles if c.is_closed and c.open_time >= start]
    exec_bars = await load_range(candles, Timeframe.M5, start, end)
    if not step_bars or not exec_bars:
        return []

    setups = SetupService(candles, entry_cfg=EntryConfig.from_spec())

    async def analyze(t: datetime) -> SetupAnalysis:
        return await setups.analyze(SYMBOL, t)

    paper = load_spec("paper")
    c = paper["assumedCosts"][SYMBOL]
    costs = AssumedCosts(
        spread=float(c["spread"]), slippage=float(c["slippage"]), commission=float(c["commission"])
    )
    _, trades = await run_variant(
        BacktestVariant(name="A"),
        analyze,
        step_bars,
        exec_bars,
        tf,
        costs,
        pending_expiry_bars=int(paper.get("pendingExpiryBars", 12)),
        end=end,
    )
    out = []
    for t in trades:
        if t.status is not BacktestTradeStatus.CLOSED:
            continue
        out.append({
            "window": start.date().isoformat(),
            "setup_id": t.setup_id,
            "direction": t.direction.value,
            "model": t.model,
            "confirmed_at": t.confirmed_at.isoformat(),
            "result": t.result.value if t.result else None,
            "net_r": t.net_r_multiple,
            "win": t.result in WINS,
        })
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--weeks", type=int, default=2)
    ap.add_argument("--out", default="/opt/data/batch_trades.jsonl")
    ap.add_argument("--min-target-atr", type=float, default=1.0)
    args = ap.parse_args()

    out_path = Path(args.out)
    done_windows: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done_windows.add(json.loads(line)["window"])

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    final = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    step = timedelta(weeks=args.weeks)

    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    pinned = {"chaseGuardAfterTouchOnly": True, "minTargetAtr": args.min_target_atr}
    settings = get_settings()

    windows = 0
    skipped_gap = 0
    trades_written = 0
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        setup_cfg = SetupConfig.from_spec()
        print(f"pinned: {pinned}   resuming past {len(done_windows)} completed windows", flush=True)

        cursor = start
        while cursor < final:
            wend = cursor + step
            wkey = cursor.date().isoformat()
            if wkey in done_windows:
                cursor = wend
                continue
            if overlaps_gap(cursor, wend):
                skipped_gap += 1
                cursor = wend
                continue
            clock = Clock(wend)
            provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
            candles = CandleService(provider, clock)
            rows = await run_window(candles, setup_cfg, cursor, wend)
            with out_path.open("a") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
                # marker so an empty window is not re-run on resume
                if not rows:
                    marker = {
                        "window": wkey,
                        "empty": True,
                        "net_r": None,
                        "win": False,
                        "result": None,
                    }
                    fh.write(json.dumps(marker) + "\n")
            trades_written += len([r for r in rows if r.get("result")])
            windows += 1
            if windows % 5 == 0:
                print(f"  {wkey}: {windows} windows, {trades_written} trades so far", flush=True)
            cursor = wend
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()

    # Pool everything in the file (this run + prior resumes).
    all_rows = [json.loads(x) for x in out_path.read_text().splitlines() if x.strip()]
    closed = [r for r in all_rows if r.get("result")]
    rs = [r["net_r"] for r in closed if r["net_r"] is not None]
    wins = sum(1 for r in closed if r["win"])
    print("\n===== BATCH COMPLETE =====")
    print(f"windows this run: {windows}   skipped (gap): {skipped_gap}")
    print(f"POOLED closed trades: {len(closed)}")
    if closed:
        wr = wins / len(closed)
        tot = sum(rs)
        print(f"  wins {wins}  losses {len(closed) - wins}  win_rate {wr:.1%}")
        print(f"  total_net_R {tot:+.2f}  avg_R {tot / len(rs):+.3f}  median_R {statistics.median(rs):+.3f}")
        gross_w = sum(r for r in rs if r > 0)
        gross_l = -sum(r for r in rs if r < 0)
        if gross_l <= 0:
            print("  profit_factor n/a (no losing R)")
        else:
            print(f"  profit_factor {gross_w / gross_l:.2f}")
    print(f"\ntrades file: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
