"""Is the do-not-chase rule firing because the TARGET IS TOO CLOSE?

target() picks the NEAREST untaken pool beyond price; armed() then kills the setup
if that pool is reached before an entry confirms. This measures the distance from
the MSS to the target, and how many bars elapse before the target is taken, to test
whether the setup is being asked to retrace in a window price closes in minutes.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import UTC, datetime

from app.config import get_settings
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.setup_state.models import SetupConfig
from app.services.setup_state.service import SetupService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]
STRIDE = 4


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    cfg = SetupConfig.from_spec()
    svc = SetupService(candles, cfg=cfg)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    bars = [c for c in series.candles if c.is_closed]
    steps = [c.close_time for c in bars if start < c.close_time <= end][::STRIDE]

    # Distance from price to its chosen target, at the moment each setup armed.
    dist_abs: list[float] = []
    dist_atr: list[float] = []
    seen: set[str] = set()
    chased = 0
    armed_total = 0

    close_by_time = {c.close_time: c.close for c in bars}

    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            if s.mss is None or s.target is None or s.id in seen:
                continue
            seen.add(s.id)
            armed_total += 1
            price = close_by_time.get(t)
            if price is None:
                continue
            d = abs(s.target.price - price)
            dist_abs.append(d)
            if s.reason and "do not chase" in s.reason:
                chased += 1

    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"armed setups seen: {armed_total}")
    if dist_abs:
        o = sorted(dist_abs)
        print("\ndistance from price to TARGET at arming (USD):")
        print(
            f"  min {o[0]:.2f}   p25 {o[len(o) // 4]:.2f}   median {statistics.median(o):.2f}"
            f"   p75 {o[3 * len(o) // 4]:.2f}   max {o[-1]:.2f}"
        )
        for t_usd in (1.0, 2.0, 5.0, 10.0):
            n = sum(1 for d in dist_abs if d <= t_usd)
            print(f"  within ${t_usd:>5.2f} : {n:>4}/{len(dist_abs)} ({n / len(dist_abs):.0%})")
    if dist_atr:
        print(f"\nmedian distance in ATR: {statistics.median(dist_atr):.2f}")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
