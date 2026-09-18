"""How far past SETUP_ARMED does anything actually get?

Earlier diagnostics classified TERMINAL reasons. This instead records the FURTHEST
state each setup ever reached, which exposes where the post-armed path stalls:
  SETUP_ARMED -> WAITING_FOR_RETRACEMENT -> ENTRY_ZONE_APPROACHING
              -> ENTRY_ZONE_TOUCHED -> CONFIRMED
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.enums import SetupState
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

# Progress order through the post-armed path.
ORDER = [
    SetupState.DISCOVERED,
    SetupState.WATCH,
    SetupState.LIQUIDITY_EVENT,
    SetupState.WAITING_FOR_MSS,
    SetupState.SETUP_FORMING,
    SetupState.SETUP_ARMED,
    SetupState.WAITING_FOR_RETRACEMENT,
    SetupState.ENTRY_ZONE_APPROACHING,
    SetupState.ENTRY_ZONE_TOUCHED,
    SetupState.WAITING_FOR_CONFIRMATION,
    SetupState.LONG_READY,
    SetupState.SHORT_READY,
]
RANK = {s: i for i, s in enumerate(ORDER)}
# LONG_READY and SHORT_READY are the same rung: direction differs, progress does not.
RANK[SetupState.SHORT_READY] = RANK[SetupState.LONG_READY]


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
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end][::STRIDE]

    furthest: dict[str, int] = {}
    plans = 0
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            r = RANK.get(s.state)
            if r is not None:
                furthest[s.id] = max(furthest.get(s.id, -1), r)
            if s.entry_plan is not None:
                plans += 1

    counts: Counter[int] = Counter(furthest.values())
    total = len(furthest)
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"retracementWindowBars {cfg.retracement_window_bars}   approachAtr {cfg.retracement_approach_atr}")
    print(f"setups {total}   entry plans {plans}\n")
    print("furthest state reached:")
    running = total
    for i, state in enumerate(ORDER):
        if state is SetupState.SHORT_READY:
            continue  # same rung as LONG_READY
        n = counts.get(i, 0)
        if n == 0 and i < RANK[SetupState.SETUP_ARMED]:
            continue
        label_txt = "LONG/SHORT_READY" if state is SetupState.LONG_READY else state.value
        print(f"  {label_txt:<26} stalled here {n:>4}   reached {running:>4}")
        running -= n


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
