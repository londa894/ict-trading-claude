"""Liquidity qualifier for structure events (fills the field Phase 2 left NOT_EVALUATED).

A structure event is liquidity-qualified (PRESENT) when opposite-side liquidity was taken shortly before
or on the breaking candle: a bearish break after a BSL SWEEP/RECLAIM, a bullish break after an SSL
SWEEP/RECLAIM, within `qualifierLookbackBars` candles up to and including the event candle.
Otherwise ABSENT. Only past/same-candle liquidity events are used (no lookahead).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.enums import Direction, LiquidityEventType, LiquiditySide, QualifierStatus
from app.services.liquidity.models import LiquidityEvent
from app.services.structure.models import LevelStructure

TAKING_EVENTS = frozenset({LiquidityEventType.SWEEP, LiquidityEventType.RECLAIM})


def qualify_level(
    level: LevelStructure | None,
    liquidity_events: Sequence[LiquidityEvent],
    candle_times: Sequence[datetime],
    lookback_bars: int,
) -> LevelStructure | None:
    if level is None:
        return None
    index = {t: i for i, t in enumerate(candle_times)}
    takings: dict[LiquiditySide, list[int]] = {LiquiditySide.BSL: [], LiquiditySide.SSL: []}
    for ev in liquidity_events:
        if ev.type in TAKING_EVENTS and ev.time in index:
            takings[ev.side].append(index[ev.time])

    events = []
    for e in level.events:
        i = index.get(e.time)
        if i is None:
            events.append(e)
            continue
        needed = LiquiditySide.BSL if e.direction is Direction.BEARISH else LiquiditySide.SSL
        present = any(i - lookback_bars <= j <= i for j in takings[needed])
        events.append(
            e.model_copy(
                update={"liquidity_qualifier": QualifierStatus.PRESENT if present else QualifierStatus.ABSENT}
            )
        )
    return level.model_copy(update={"events": events})
