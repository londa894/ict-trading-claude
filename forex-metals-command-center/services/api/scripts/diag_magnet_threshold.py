"""What would a magnet-score threshold actually filter out?

Reports the distribution of magnet scores for the pools that were SWEPT, so a
threshold can be chosen from evidence rather than guessed.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import Counter
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.enums import LiquidityEventType, Timeframe
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.liquidity.service import LiquidityService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]
THRESHOLDS = (20.0, 30.0, 40.0, 50.0, 60.0)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, Clock(end))
    analysis = await LiquidityService(candles).analyze(SYMBOL, Timeframe.M15, 1000)

    pool_by_id = {p.id: p for p in analysis.pools}
    scores: list[float] = []
    unscored = 0
    by_type: Counter[str] = Counter()

    for ev in analysis.events:
        if ev.type is not LiquidityEventType.SWEEP or not (start <= ev.time < end):
            continue
        pool = pool_by_id.get(ev.pool_id)
        by_type[str(ev.pool_type.value)] += 1
        if pool is None or pool.magnet_score is None:
            unscored += 1
            continue
        scores.append(pool.magnet_score)

    total = len(scores) + unscored
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"sweeps: {total}   scored: {len(scores)}   unscored: {unscored}")
    print("\nswept pool types:")
    for name, n in by_type.most_common():
        print(f"  {name:<18} {n:>4}")
    if not scores:
        print("\nno scored pools: a magnet threshold would remove every setup")
        return

    ordered = sorted(scores)
    print("\nmagnet score of swept pools:")
    print(f"  min {ordered[0]:.1f}   p25 {ordered[len(ordered) // 4]:.1f}"
          f"   median {statistics.median(ordered):.1f}"
          f"   p75 {ordered[3 * len(ordered) // 4]:.1f}   max {ordered[-1]:.1f}")

    print("\nsweeps surviving each threshold (of all sweeps, unscored dropped):")
    for t in THRESHOLDS:
        kept = sum(1 for s in scores if s >= t)
        print(f"  >= {t:>4.0f} : {kept:>4}/{total} ({kept / total:.0%})")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
