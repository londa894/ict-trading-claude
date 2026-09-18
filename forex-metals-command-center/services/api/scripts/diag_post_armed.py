"""How far past SETUP_ARMED does anything actually get?

Progress is derived from MILESTONE FIELDS, not from the sampled `state`. Terminal
setups (INVALIDATED/EXPIRED) keep their milestones, so a setup that armed and later
died is still counted as having armed. Reading `state` instead undercounts badly:
a setup first observed already-terminal has no rank at all.

  target -> liquidity_event -> mss (= armed) -> zone_ids -> touched_zone_id -> entry_plan
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime

from app.config import get_settings
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.setup_state.models import Setup, SetupConfig
from app.services.setup_state.service import SetupService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]
STRIDE = 4

MILESTONES = [
    ("discovered", lambda s: True),
    ("target chosen", lambda s: s.target is not None),
    ("liquidity swept", lambda s: s.liquidity_event is not None),
    ("ARMED (mss)", lambda s: s.mss is not None),
    ("zone formed", lambda s: bool(s.zone_ids)),
    ("zone touched", lambda s: s.touched_zone_id is not None),
    ("entry plan", lambda s: s.entry_plan is not None),
]


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def depth(s: Setup) -> int:
    """Index of the furthest milestone this setup satisfies."""
    reached = 0
    for i, (_, test) in enumerate(MILESTONES):
        if test(s):
            reached = i
    return reached


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
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end][::STRIDE]

    best: dict[str, int] = {}
    armed_reasons: Counter[str] = Counter()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            d = depth(s)
            best[s.id] = max(best.get(s.id, 0), d)
            if s.mss is not None and s.terminal and s.reason:
                armed_reasons[s.reason[:60]] += 1

    total = len(best)
    counts: Counter[int] = Counter(best.values())
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"retracementWindowBars {cfg.retracement_window_bars}   zoneGraceBars {cfg.zone_grace_bars}")
    print(f"setups {total}\n")
    print(f"{'milestone':<20}{'reached':>9}{'share':>8}{'stalled':>9}")
    running = total
    for i, (name, _) in enumerate(MILESTONES):
        n = counts.get(i, 0)
        share = f"{running / total:.0%}" if total else "-"
        print(f"  {name:<18}{running:>9}{share:>8}{n:>9}")
        running -= n

    if armed_reasons:
        print("\nhow ARMED setups died:")
        for reason, n in armed_reasons.most_common(8):
            print(f"  {n:>4}  {reason}")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
