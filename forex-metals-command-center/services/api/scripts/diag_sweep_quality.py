"""Measure how decisive the engine's sweeps actually are.

The rule is CLOSE_BACK_INSIDE_SAME_CANDLE with no magnitude requirement, so this
reports the distribution of penetration depth and rejection distance for every
SWEEP the real engine emitted. Read-only measurement of the engine's own output.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.enums import LiquidityEventType, Timeframe
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.liquidity.service import LiquidityService
from app.services.replay.engine import mean_true_range

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, Clock(end))
    liquidity = LiquidityService(candles)

    analysis = await liquidity.analyze(SYMBOL, Timeframe.M15, 1000)
    series = await candles.load_series(SYMBOL, Timeframe.M15, 1000, end)
    closed = [c for c in series.candles if c.is_closed]
    atr = mean_true_range(closed, 14) if closed else None
    if not atr:
        print(f"{label}: no ATR available")
        return

    penetration: list[float] = []
    rejection: list[float] = []

    for ev in analysis.events:
        if ev.type is not LiquidityEventType.SWEEP or not (start <= ev.time < end):
            continue
        penetration.append(abs(ev.extreme - ev.price) / atr)
        rejection.append(abs(ev.price - ev.close) / atr)

    n = len(penetration)
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"sweeps emitted: {n}   (ATR {atr:.2f})")
    if not n:
        return

    print("\nhow far price pushed BEYOND the level (ATR):")
    print(f"  p25 {_pct(penetration, .25):.3f}   median {statistics.median(penetration):.3f}"
          f"   p75 {_pct(penetration, .75):.3f}   max {max(penetration):.3f}")
    print("\nhow far it closed back INSIDE the level (ATR):")
    print(f"  p25 {_pct(rejection, .25):.3f}   median {statistics.median(rejection):.3f}"
          f"   p75 {_pct(rejection, .75):.3f}   max {max(rejection):.3f}")

    for thresh in (0.05, 0.10, 0.25):
        weak = sum(1 for p in penetration if p < thresh)
        print(f"\npenetration < {thresh:.2f} ATR : {weak:>3}/{n} ({weak / n:.0%})")
    for thresh in (0.10, 0.25):
        weak = sum(1 for r in rejection if r < thresh)
        print(f"close-back  < {thresh:.2f} ATR : {weak:>3}/{n} ({weak / n:.0%})")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
