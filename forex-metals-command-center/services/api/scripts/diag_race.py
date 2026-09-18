"""Who wins the race: the retracement, or the target?

An armed setup needs price to retrace into the leg's FVG BEFORE the target pool is
taken. This measures both legs of that race directly:
  - how far price must retrace to reach the zone edge (in ATR)
  - how far price must travel to take the target (in ATR)
If the target is consistently nearer in ATR terms than the zone, do-not-chase will
always fire first and no setup can ever confirm.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import UTC, datetime

from app.config import get_settings
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.pd_arrays.service import PdArrayService
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


def pct(vals: list[float], p: float) -> float:
    o = sorted(vals)
    return o[min(len(o) - 1, int(len(o) * p))]


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    cfg = SetupConfig.from_spec()
    svc = SetupService(candles, cfg=cfg)
    pd_svc = PdArrayService(candles)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    bars = [c for c in series.candles if c.is_closed]
    close_by_time = {c.close_time: c.close for c in bars}
    steps = [c.close_time for c in bars if start < c.close_time <= end][::STRIDE]

    zones_by_id = {}
    pd_analysis = await pd_svc.analyze(SYMBOL, tf, 1000)
    for z in pd_analysis.zones:
        zones_by_id[z.id] = z

    to_zone: list[float] = []
    to_target: list[float] = []
    target_nearer = 0
    seen: set[str] = set()

    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            if s.mss is None or s.target is None or not s.zone_ids or s.id in seen:
                continue
            price = close_by_time.get(t)
            if price is None:
                continue
            live = [zones_by_id[z] for z in s.zone_ids if z in zones_by_id]
            if not live:
                continue
            seen.add(s.id)
            bullish = s.direction.value == "LONG"
            edge = max(z.top for z in live) if bullish else min(z.bottom for z in live)
            dz = abs(price - edge)
            dt = abs(s.target.price - price)
            to_zone.append(dz)
            to_target.append(dt)
            if dt < dz:
                target_nearer += 1

    n = len(to_zone)
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"armed setups with a zone: {n}")
    if not n:
        return
    print("\ndistance price must travel (USD):")
    print(
        f"  to ZONE edge   median {statistics.median(to_zone):>7.2f}"
        f"   p25 {pct(to_zone, 0.25):>7.2f}   p75 {pct(to_zone, 0.75):>7.2f}"
    )
    print(
        f"  to TARGET      median {statistics.median(to_target):>7.2f}"
        f"   p25 {pct(to_target, 0.25):>7.2f}   p75 {pct(to_target, 0.75):>7.2f}"
    )
    print(f"\ntarget is NEARER than the zone: {target_nearer}/{n} ({target_nearer / n:.0%})")
    print("  (when the target is nearer, do-not-chase fires before any retracement can complete)")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
