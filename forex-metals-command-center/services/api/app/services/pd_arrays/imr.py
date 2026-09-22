"""IMR (Immediate Rebalance) detector — the inverse of an FVG (reversal-revamp Phase A, spec section 2).

A 3-candle sequence (a=i-2, b=i-1, c=i) carrying a same-direction displacement (expansion) whose gap is
IMMEDIATELY rebalanced: candle c's wick overlaps candle a's wick, so NO FVG forms. Stored as its own
PD-array type (``PdArrayType.IMR``), never a failed/null FVG.

Bullish IMR (mirror for bearish):
  - a BULLISH displacement of grade >= imr_min_grade on the impulse candle b (i-1)  (the "expansion" gate)
  - c.close > a.high   (the impulse held: candle 3 closed above candle 1's high)
  - c.low  <= a.high   (candle 3 wicked back into candle 1's range -> no FVG gap = immediate rebalance)
  Zone = [a.high, c.close]; structural entry reference = c.close (candle 3's close, documented per spec).

Expansion is enforced by the displacement requirement — there are no IMRs in consolidation. Detection is
candle-sequential, known only at candle i's close, and never looks ahead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    DisplacementGrade,
    PdArrayEventType,
    PdArrayState,
    PdArrayType,
    TrendDirection,
)
from app.services.pd_arrays.models import DisplacementEvent, PdArrayConfig, PdArrayEvent, PdArrayZone
from app.services.structure.swings import atr_at, true_ranges


@dataclass(frozen=True)
class ImrResult:
    zones: list[PdArrayZone]
    events: list[PdArrayEvent]


def detect_imrs(
    candles: Sequence[Candle],
    displacements: Sequence[DisplacementEvent],
    trend: TrendDirection,
    cfg: PdArrayConfig,
) -> ImrResult:
    n = len(candles)
    trs = true_ranges(candles)
    index = {c.open_time: i for i, c in enumerate(candles)}
    disp_by_index: dict[int, list[DisplacementEvent]] = {}
    for d in displacements:
        if d.time in index:
            disp_by_index.setdefault(index[d.time], []).append(d)

    def strongest(direction: Direction, lo: int, hi: int) -> DisplacementGrade | None:
        best: DisplacementGrade | None = None
        for k in range(max(lo, 0), hi + 1):
            for d in disp_by_index.get(k, []):
                if d.direction is direction and (best is None or d.grade.rank > best.rank):
                    best = d.grade
        return best

    zones: list[PdArrayZone] = []
    events: list[PdArrayEvent] = []
    atr_last = atr_at(trs, n - 1, cfg.atr_period) if n else 0.0
    for i in range(2, n):
        a, c = candles[i - 2], candles[i]
        for direction in (Direction.BULLISH, Direction.BEARISH):
            # The impulse must be the MIDDLE candle b (i-1); c (i) is the separate rebalancing candle.
            grade = strongest(direction, i - 1, i - 1)
            if grade is None or grade.rank < cfg.imr_min_grade.rank:
                continue
            if direction is Direction.BULLISH:
                if not (c.close > a.high and c.low <= a.high):
                    continue
                bottom, top = a.high, c.close
            else:
                if not (c.close < a.low and c.high >= a.low):
                    continue
                bottom, top = c.close, a.low
            zone = _make_zone(i, candles, direction, top, bottom, grade, atr_last, trend, cfg, n)
            zones.append(zone)
            events.append(
                PdArrayEvent(
                    id=f"{zone.id}:{PdArrayEventType.IMR_CREATED.value}:{c.open_time.isoformat()}",
                    zone_id=zone.id,
                    zone_type=PdArrayType.IMR,
                    direction=direction,
                    type=PdArrayEventType.IMR_CREATED,
                    time=c.open_time,
                    price=c.close,
                    detail=f"immediate rebalance, {grade.value} displacement",
                )
            )
            break  # a candle is at most one IMR
    return ImrResult(zones=zones, events=events)


def _make_zone(
    i: int,
    candles: Sequence[Candle],
    direction: Direction,
    top: float,
    bottom: float,
    grade: DisplacementGrade,
    atr: float,
    trend: TrendDirection,
    cfg: PdArrayConfig,
    n: int,
) -> PdArrayZone:
    size = max(0.0, top - bottom)
    size_atr = size / atr if atr > 0 else 0.0
    size_points = min(cfg.quality_size_max, cfg.quality_size_max * size_atr / cfg.quality_size_full_atr)
    disp_points = cfg.quality_displacement.get(grade.value, 0.0)
    fresh_points = cfg.quality_freshness.get(PdArrayState.FRESH.value, 0.0)
    aligned = (direction is Direction.BULLISH and trend is TrendDirection.BULLISH) or (
        direction is Direction.BEARISH and trend is TrendDirection.BEARISH
    )
    total = size_points + disp_points + fresh_points + (cfg.quality_trend_alignment if aligned else 0.0)
    c = candles[i]
    return PdArrayZone(
        id=f"IMR:{direction.value}:{c.open_time.isoformat()}",
        type=PdArrayType.IMR,
        direction=direction,
        top=top,
        bottom=bottom,
        midpoint=(top + bottom) / 2,
        size_atr=round(size_atr, 3),
        source_times=[candles[i - 2].open_time, candles[i - 1].open_time, c.open_time],
        created_at=c.open_time,
        known_at=c.close_time,
        state=PdArrayState.FRESH,
        fill_pct=0.0,
        ifvg_status=None,
        parent_id=None,
        displacement_grade=grade,
        state_changed_at=None,
        invalidated_at=None,
        age_bars=n - 1 - i,
        active=True,
        quality_score=round(max(0.0, min(100.0, total)), 1),
    )
