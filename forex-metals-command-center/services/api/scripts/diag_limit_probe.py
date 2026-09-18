"""Would a resting LIMIT at the FVG harvest free winners, or just add losers?

Both the reactive-entry diagnosis and the outside review converge on one empirical
question: for the setups that currently die "target reached before a confirmed entry"
(all after touching the zone), does a limit order resting in the zone catch clean
winners, or does it fill into failures the confirmation models exist to filter?

LIMIT_RESEARCH already exists (a limit at the FVG edge) but is gated to AGGRESSIVE mode,
the touch candle only, and research_only. This runs AGGRESSIVE mode - which includes
LIMIT_RESEARCH alongside the reactive models - across many windows through the REAL
backtest engine, isolates the LIMIT-filled trades, and reports their MFE/MAE/time-to-
target/win distribution. That is the adjudicator:

  - high MFE, near-zero MAE, wins  -> limit harvests free winners; build it
  - mixed / high MAE / losses      -> confirmation is doing its job; limit adds losers

No strategy change: measurement only. The paper sim already fills on M5 and pins the
intrabar path (adverse-first; on a bar where a LIMIT filled intrabar only the stop can
trigger), so this is a conservative estimate of the limit's benefit.
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
from app.domain.enums import BacktestTradeStatus, EntryMode, Timeframe, TradeResult
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


async def run_window(candles: CandleService, cfg: SetupConfig, start: datetime, end: datetime) -> list[dict]:
    tf = cfg.setup_timeframe
    step_series = await candles.load_series(SYMBOL, tf, 1000, end)
    step_bars = [c for c in step_series.candles if c.is_closed and c.open_time >= start]
    exec_bars = await load_range(candles, Timeframe.M5, start, end)
    if not step_bars or not exec_bars:
        return []

    # AGGRESSIVE mode: includes LIMIT_RESEARCH alongside the reactive models.
    setups = SetupService(candles, entry_cfg=EntryConfig.from_spec(EntryMode.AGGRESSIVE))

    async def analyze(t: datetime) -> SetupAnalysis:
        return await setups.analyze(SYMBOL, t)

    paper = load_spec("paper")
    c = paper["assumedCosts"][SYMBOL]
    costs = AssumedCosts(
        spread=float(c["spread"]), slippage=float(c["slippage"]), commission=float(c["commission"])
    )
    _, trades = await run_variant(
        BacktestVariant(name="AGG", entry_mode=EntryMode.AGGRESSIVE),
        analyze,
        step_bars,
        exec_bars,
        tf,
        costs,
        pending_expiry_bars=int(paper.get("pendingExpiryBars", 12)),
        end=end,
    )
    rows = []
    for t in trades:
        if t.status is not BacktestTradeStatus.CLOSED:
            continue
        tt = None
        if t.filled_at and t.exited_at:
            tt = (t.exited_at - t.filled_at).total_seconds() / 60.0
        rows.append({
            "window": start.date().isoformat(),
            "model": t.model,
            "result": t.result.value if t.result else None,
            "net_r": t.net_r_multiple,
            "mfe_r": t.mfe_r,
            "mae_r": t.mae_r,
            "minutes_to_exit": tt,
            "win": t.result in WINS,
            "is_limit": t.model == "LIMIT_RESEARCH",
        })
    return rows


def report(rows: list[dict], title: str) -> None:
    if not rows:
        print(f"\n{title}: no trades")
        return
    rs = [r["net_r"] for r in rows if r["net_r"] is not None]
    mfe = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
    mae = [r["mae_r"] for r in rows if r["mae_r"] is not None]
    tt = [r["minutes_to_exit"] for r in rows if r["minutes_to_exit"] is not None]
    wins = sum(1 for r in rows if r["win"])
    print(f"\n{title}: {len(rows)} trades")
    print(f"  win_rate {wins / len(rows):.0%}  total_net_R {sum(rs):+.2f}  avg_R {sum(rs) / len(rs):+.3f}")
    if mfe:
        print(f"  MFE_R  median {statistics.median(mfe):+.2f}  p75 {sorted(mfe)[3 * len(mfe) // 4]:+.2f}")
    if mae:
        lo = sorted(mae)[len(mae) // 4]
        print(f"  MAE_R  median {statistics.median(mae):+.2f}  p25 {lo:+.2f}  (0 = never went against)")
    if tt:
        print(f"  minutes_to_exit  median {statistics.median(tt):.0f}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--weeks", type=int, default=2)
    ap.add_argument("--out", default="/opt/data/limit_probe.jsonl")
    args = ap.parse_args()

    out_path = Path(args.out)
    done: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["window"])

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    final = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    step = timedelta(weeks=args.weeks)

    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    # Pin the frequency flags so setups reach the zone; AGGRESSIVE mode adds the limit.
    pinned = {"chaseGuardAfterTouchOnly": True, "confirmBeforeChaseGuard": True, "minTargetAtr": 1.0}
    settings = get_settings()
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        cfg = SetupConfig.from_spec()
        print(f"pinned: {pinned}   mode: AGGRESSIVE   resuming past {len(done)} windows", flush=True)
        cursor = start
        n = 0
        while cursor < final:
            wend = cursor + step
            wkey = cursor.date().isoformat()
            if wkey in done or overlaps_gap(cursor, wend):
                cursor = wend
                continue
            clock = Clock(wend)
            provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
            candles = CandleService(provider, clock)
            rows = await run_window(candles, cfg, cursor, wend)
            with out_path.open("a") as fh:
                if rows:
                    for r in rows:
                        fh.write(json.dumps(r) + "\n")
                else:
                    fh.write(json.dumps({"window": wkey, "empty": True}) + "\n")
            n += 1
            if n % 5 == 0:
                print(f"  {wkey}: {n} windows done", flush=True)
            cursor = wend
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()

    all_rows = [
        r
        for x in out_path.read_text().splitlines()
        if x.strip() and "result" in (r := json.loads(x))
    ]
    limit_rows = [r for r in all_rows if r.get("is_limit")]
    react_rows = [r for r in all_rows if not r.get("is_limit")]
    print("\n" + "=" * 60)
    report(limit_rows, "LIMIT_RESEARCH trades (the ones a resting order catches)")
    report(react_rows, "REACTIVE-model trades (M15_CLOSE / LTF / no-wick)")
    print("\nverdict guide: if LIMIT rows show high MFE, near-zero MAE, and net-positive R,")
    print("a resting limit harvests winners. If mixed/negative, confirmation earns its keep.")
    print(f"\nfile: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
