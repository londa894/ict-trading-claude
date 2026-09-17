"""As-of HTF bias timeline for the setup engine: confirmed EXTERNAL BOS/MSS per bias timeframe."""

from __future__ import annotations

from app.domain.enums import StructureEventStatus, StructureEventType, StructureLevel
from app.services.setup_state.models import BiasPoint
from app.services.structure.models import StructureAnalysis

TREND_EVENTS = frozenset({StructureEventType.BOS, StructureEventType.MSS})


def bias_points(analysis: StructureAnalysis) -> list[BiasPoint]:
    """A break is known at the close of its breaking candle (open time + timeframe)."""
    if analysis.external is None:
        return []
    return [
        BiasPoint(
            timeframe=analysis.timeframe,
            direction=e.direction,
            known_at=e.time + analysis.timeframe.duration,
            event_id=e.id,
        )
        for e in analysis.external.events
        if e.level is StructureLevel.EXTERNAL
        and e.status is StructureEventStatus.CONFIRMED
        and e.type in TREND_EVENTS
    ]
