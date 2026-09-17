"""Displacement qualifier for structure events (fills the field Phase 2 left NOT_EVALUATED).

A structure event is displacement-qualified (PRESENT) when a same-direction displacement of grade >=
`qualifierMinGrade` was emitted within `qualifierLookbackBars` candles up to and including the event candle.
Otherwise ABSENT. Only displacement known by the event candle's close is used (no lookahead).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.enums import DisplacementGrade, QualifierStatus
from app.services.pd_arrays.models import DisplacementEvent
from app.services.structure.models import LevelStructure


def qualify_displacement(
    level: LevelStructure | None,
    displacements: Sequence[DisplacementEvent],
    candle_times: Sequence[datetime],
    lookback_bars: int,
    min_grade: DisplacementGrade,
) -> LevelStructure | None:
    if level is None:
        return None
    index = {t: i for i, t in enumerate(candle_times)}
    strong = [
        (index[d.time], d.direction)
        for d in displacements
        if d.time in index and d.grade.rank >= min_grade.rank
    ]
    events = []
    for e in level.events:
        i = index.get(e.time)
        if i is None:
            events.append(e)
            continue
        present = any(i - lookback_bars <= j <= i and direction is e.direction for j, direction in strong)
        events.append(
            e.model_copy(
                update={
                    "displacement_qualifier": QualifierStatus.PRESENT if present else QualifierStatus.ABSENT
                }
            )
        )
    return level.model_copy(update={"events": events})
