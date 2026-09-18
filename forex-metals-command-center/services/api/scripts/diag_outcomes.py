"""Do the confirmed plans actually WIN?

diag_rr proved plans confirm once chaseGuardAfterTouchOnly + minTargetAtr are on.
This drives the REAL backtest engine (run_variant: limit fill at plan entry, plan
stop, TP1, assumed costs from paper.json, one position at a time) to closed outcomes,
then reports win rate and R. It is the same fill/exit code the paper engine uses.

Pins the two flags in the setup spec, restores it byte-for-byte in a finally. Research
only: risk, news and verdict-authority gates are not applied (matches backtest design).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
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

WINS = (TradeResult.FULL_WIN, TradeResult.PARTIAL_WIN)
LOSSES = (TradeResult.FULL_LOSS, TradeResult.PARTIAL_LOSS)

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-07-01", "2024-07-15", "summer"),
    ("2024-12-02", "2024-12-16", "consolidation"),
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]
SPEC = Path(__file__).resolve().parents[3] / "packages" / "strategy-spec" / "setup.json"


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def _load_range(candles: CandleService, tf: Timeframe, start: datetime, end: datetime) -> list:
    """Closed candles in [start, end], loaded backward in chunks (mirrors BacktestService._load)."""
    by_open: dict = {}
    chunk_end = end
    for _ in range(200):
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


async def run_window(start_s: str, end_s: str, label: str) -> list:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    setup_cfg = SetupConfig.from_spec()

    tf = setup_cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    step_series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    exec_bars = await _load_range(candles, Timeframe.M5, start, end)

    setups = SetupService(candles, entry_cfg=EntryConfig.from_spec())

    async def analyze(t: datetime) -> SetupAnalysis:
        return await setups.analyze(SYMBOL, t)

    paper = load_spec("paper")
    sym_costs = paper["assumedCosts"][SYMBOL]
    base_costs = AssumedCosts(
        spread=float(sym_costs["spread"]),
        slippage=float(sym_costs["slippage"]),
        commission=float(sym_costs["commission"]),
    )
    funnel, trades = await run_variant(
        BacktestVariant(name="A"),
        analyze,
        step_series.candles,
        exec_bars,
        tf,
        base_costs,
        pending_expiry_bars=int(paper.get("pendingExpiryBars", 12)),
        end=end,
    )

    closed = [t for t in trades if t.status is BacktestTradeStatus.CLOSED]
    wins = [t for t in closed if t.result in WINS]
    losses = [t for t in closed if t.result in LOSSES]
    rs = [t.net_r_multiple for t in closed if t.net_r_multiple is not None]
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(
        f"confirmed {funnel.plans_confirmed}   fills {funnel.fills}   "
        f"closed {len(closed)}   open_at_end {funnel.open_at_end}   "
        f"skipped {funnel.plans_skipped_overlap}"
    )
    if closed:
        wr = len(wins) / len(closed)
        tot = sum(rs)
        print(
            f"  wins {len(wins)}  losses {len(losses)}  win_rate {wr:.0%}  "
            f"total_net_R {tot:+.2f}  avg_R {tot / len(rs):+.2f}"
        )
    return closed


async def main() -> None:
    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    pinned = {
        "chaseGuardAfterTouchOnly": True,
        "minTargetAtr": float(os.environ.get("MIN_TARGET_ATR", "1.0")),
    }
    all_closed: list = []
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        print(f"pinned: {pinned}")
        for start_s, end_s, label in WINDOWS:
            all_closed += await run_window(start_s, end_s, label)
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()

    print("\n===== POOLED =====")
    rs = [t.net_r_multiple for t in all_closed if t.net_r_multiple is not None]
    wins = sum(1 for t in all_closed if t.result in WINS)
    if all_closed:
        print(f"  closed {len(all_closed)}  wins {wins}  win_rate {wins / len(all_closed):.0%}  "
              f"total_net_R {sum(rs):+.2f}")
    else:
        print("  no closed trades")
    print("\nspec restored")


if __name__ == "__main__":
    asyncio.run(main())
