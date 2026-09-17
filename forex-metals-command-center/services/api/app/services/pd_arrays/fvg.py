"""FVG / IFVG engine (deterministic, candle-sequential, no lookahead).

FVG (classic 3-candle, known at the close of candle i):
    bullish  low[i]  > high[i-2]  -> zone [high[i-2], low[i]]   (support; mitigated from above)
    bearish  high[i] < low[i-2]   -> zone [high[i], low[i-2]]   (resistance; mitigated from below)
  Gaps smaller than `fvg.minSizeAtr` x ATR_before(i-2) are ignored ("tiny rejection").
  `displacement_grade` = strongest same-direction displacement event on candles i-2..i.

Mitigation, evaluated from the candle after creation (wick penetration from the entry edge):
    fill% = penetration / size * 100 (monotonic max)
    TOUCHED (edge reached within tolerance, fill < touchMaxFillPct) -> PARTIAL -> HALF (>= halfFillPct)
    -> FULL (>= 100)
  A wick may fully mitigate (FULL keeps the zone valid). Structural invalidation needs a CLOSE beyond the far
  edge -> INVALIDATED (terminal).

IFVG (no automatic conversion):
  An FVG invalidated at candle j spawns POTENTIAL_IFVG in the opposite direction on the same zone. Within
  `ifvg.confirmWindowBars` it becomes CONFIRMED_IFVG when either
    (a) a displacement of grade >= `ifvg.displacementMinGrade` in the IFVG direction exists on candles
        j-(maxLegCandles-1)..k (the inversion move itself), or
    (b) `ifvg.acceptanceCloses` consecutive closes beyond the zone (counting j) with the latest at least
        `ifvg.acceptanceExtensionAtr` x ATR beyond the edge.
  It becomes FAILED_IFVG on a close back through the far edge ("reclaimed") or when the window expires
  ("expired"). A CONFIRMED_IFVG is then mitigated/invalidated like an FVG from its own side.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    DisplacementGrade,
    IfvgStatus,
    PdArrayEventType,
    PdArrayState,
    PdArrayType,
    TrendDirection,
)
from app.services.pd_arrays.displacement import atr_before
from app.services.pd_arrays.models import DisplacementEvent, PdArrayConfig, PdArrayEvent, PdArrayZone
from app.services.structure.swings import atr_at, true_ranges

NOT_TRACKED = 1 << 62  # potential IFVGs are not mitigated until confirmed

_FILL_STATES = [
    PdArrayState.FRESH,
    PdArrayState.TOUCHED,
    PdArrayState.PARTIAL,
    PdArrayState.HALF,
    PdArrayState.FULL,
]
_FILL_EVENT = {
    PdArrayState.TOUCHED: PdArrayEventType.TOUCHED,
    PdArrayState.PARTIAL: PdArrayEventType.PARTIAL_FILL,
    PdArrayState.HALF: PdArrayEventType.HALF_FILL,
    PdArrayState.FULL: PdArrayEventType.FULL_FILL,
}


@dataclass
class _Zone:
    id: str
    type: PdArrayType
    direction: Direction
    top: float
    bottom: float
    source_times: list[datetime]
    created_index: int
    parent_id: str | None = None
    displacement_grade: DisplacementGrade | None = None
    state: PdArrayState = PdArrayState.FRESH
    fill_pct: float = 0.0
    ifvg_status: IfvgStatus | None = None
    state_index: int | None = None
    invalidated_index: int | None = None
    tracking_from: int = 0  # first candle index evaluated for mitigation
    accept_streak: int = 0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def valid(self) -> bool:
        if self.state is PdArrayState.INVALIDATED:
            return False
        return self.type is PdArrayType.FVG or self.ifvg_status is IfvgStatus.CONFIRMED_IFVG


@dataclass(frozen=True)
class FvgResult:
    zones: list[PdArrayZone]
    events: list[PdArrayEvent]


Emit = Callable[[_Zone, PdArrayEventType, int, float, str], None]


def detect_fvgs(
    candles: Sequence[Candle],
    displacements: Sequence[DisplacementEvent],
    trend: TrendDirection,
    cfg: PdArrayConfig,
) -> FvgResult:
    n = len(candles)
    trs = true_ranges(candles)
    index = {c.open_time: i for i, c in enumerate(candles)}
    disp_by_index: dict[int, list[DisplacementEvent]] = {}
    for d in displacements:
        if d.time in index:
            disp_by_index.setdefault(index[d.time], []).append(d)

    zones: list[_Zone] = []
    events: list[PdArrayEvent] = []

    def emit(z: _Zone, kind: PdArrayEventType, i: int, price: float, detail: str) -> None:
        z.state_index = i
        events.append(
            PdArrayEvent(
                id=f"{z.id}:{kind.value}:{candles[i].open_time.isoformat()}",
                zone_id=z.id,
                zone_type=z.type,
                direction=z.direction,
                type=kind,
                time=candles[i].open_time,
                price=price,
                detail=detail,
            )
        )

    def strongest(direction: Direction, lo: int, hi: int) -> DisplacementGrade | None:
        best: DisplacementGrade | None = None
        for k in range(max(lo, 0), hi + 1):
            for d in disp_by_index.get(k, []):
                if d.direction is direction and (best is None or d.grade.rank > best.rank):
                    best = d.grade
        return best

    for i, c in enumerate(candles):
        atr_now = atr_at(trs, i, cfg.atr_period)
        spawned: list[_Zone] = []
        for z in zones:
            if z.type is PdArrayType.IFVG and z.ifvg_status is IfvgStatus.POTENTIAL_IFVG:
                _advance_ifvg(z, c, i, atr_now, strongest, cfg, emit)
            if z.valid and i >= z.tracking_from:
                inverted = _mitigate(z, c, i, atr_now, cfg, emit)
                if inverted is not None:
                    spawned.append(inverted)
        for ifvg in spawned:
            zones.append(ifvg)
            emit(ifvg, PdArrayEventType.IFVG_POTENTIAL, i, c.close, "parent FVG closed through")
            # The inversion candle itself may already confirm (e.g. it is a displacement candle).
            _advance_ifvg(ifvg, c, i, atr_now, strongest, cfg, emit)

        if i >= 2:
            created = _create_fvg(candles, i, trs, cfg, strongest)
            if created is not None:
                zones.append(created)
                emit(created, PdArrayEventType.CREATED, i, created.midpoint, f"size {created.size:.5g}")
                created.state_index = None  # creation is not a mitigation state change

    atr_last = atr_at(trs, n - 1, cfg.atr_period) if n else 0.0
    return FvgResult(zones=[_snapshot(z, candles, atr_last, trend, cfg) for z in zones], events=events)


def _create_fvg(
    candles: Sequence[Candle],
    i: int,
    trs: Sequence[float],
    cfg: PdArrayConfig,
    strongest: Callable[[Direction, int, int], DisplacementGrade | None],
) -> _Zone | None:
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    atr = atr_before(trs, i - 2, cfg.atr_period)
    if not atr:
        return None
    if c.low > a.high:
        direction, bottom, top = Direction.BULLISH, a.high, c.low
    elif c.high < a.low:
        direction, bottom, top = Direction.BEARISH, c.high, a.low
    else:
        return None
    if top - bottom < cfg.fvg_min_size_atr * atr:
        return None
    return _Zone(
        id=f"FVG:{direction.value}:{c.open_time.isoformat()}",
        type=PdArrayType.FVG,
        direction=direction,
        top=top,
        bottom=bottom,
        source_times=[a.open_time, b.open_time, c.open_time],
        created_index=i,
        displacement_grade=strongest(direction, i - 2, i),
        tracking_from=i + 1,
    )


def _mitigate(z: _Zone, c: Candle, i: int, atr: float, cfg: PdArrayConfig, emit: Emit) -> _Zone | None:
    bullish = z.direction is Direction.BULLISH
    penetration = (z.top - c.low) if bullish else (c.high - z.bottom)
    closed_through = c.close < z.bottom if bullish else c.close > z.top
    if closed_through:
        z.fill_pct = 100.0
        z.state = PdArrayState.INVALIDATED
        z.invalidated_index = i
        emit(z, PdArrayEventType.INVALIDATED, i, c.close, "close beyond far edge")
        if z.type is PdArrayType.FVG:
            return _Zone(
                id=f"IFVG:{z.id}:{c.open_time.isoformat()}",
                type=PdArrayType.IFVG,
                direction=Direction.BEARISH if bullish else Direction.BULLISH,
                top=z.top,
                bottom=z.bottom,
                source_times=list(z.source_times),
                created_index=i,
                parent_id=z.id,
                ifvg_status=IfvgStatus.POTENTIAL_IFVG,
                tracking_from=NOT_TRACKED,
            )
        return None
    if penetration < -cfg.fvg_touch_tolerance_atr * atr or z.size <= 0:
        return None
    fill = max(0.0, min(100.0, penetration / z.size * 100))
    z.fill_pct = max(z.fill_pct, round(fill, 2))
    if z.fill_pct >= 100:
        target = PdArrayState.FULL
    elif z.fill_pct >= cfg.fvg_half_fill_pct:
        target = PdArrayState.HALF
    elif z.fill_pct >= cfg.fvg_touch_max_fill_pct:
        target = PdArrayState.PARTIAL
    else:
        target = PdArrayState.TOUCHED
    if _FILL_STATES.index(target) > _FILL_STATES.index(z.state):
        z.state = target
        emit(z, _FILL_EVENT[target], i, c.low if bullish else c.high, f"fill {z.fill_pct:.1f}%")
    return None


def _advance_ifvg(
    z: _Zone,
    c: Candle,
    i: int,
    atr: float,
    strongest: Callable[[Direction, int, int], DisplacementGrade | None],
    cfg: PdArrayConfig,
    emit: Emit,
) -> None:
    bearish = z.direction is Direction.BEARISH  # parent was bullish, price broke down
    if i - z.created_index > cfg.ifvg_confirm_window_bars:
        z.ifvg_status = IfvgStatus.FAILED_IFVG
        emit(z, PdArrayEventType.IFVG_FAILED, i, c.close, "expired")
        return
    if i > z.created_index and (c.close > z.top if bearish else c.close < z.bottom):
        z.ifvg_status = IfvgStatus.FAILED_IFVG
        emit(z, PdArrayEventType.IFVG_FAILED, i, c.close, "reclaimed")
        return
    beyond = c.close < z.bottom if bearish else c.close > z.top
    z.accept_streak = z.accept_streak + 1 if beyond else 0
    grade = strongest(z.direction, z.created_index - cfg.max_leg_candles + 1, i)
    by_displacement = grade is not None and grade.rank >= cfg.ifvg_displacement_min_grade.rank
    extension = (z.bottom - c.close) if bearish else (c.close - z.top)
    by_acceptance = (
        z.accept_streak >= cfg.ifvg_acceptance_closes and extension >= cfg.ifvg_acceptance_extension_atr * atr
    )
    if by_displacement or by_acceptance:
        z.ifvg_status = IfvgStatus.CONFIRMED_IFVG
        z.displacement_grade = grade if by_displacement else None
        z.tracking_from = i + 1
        emit(
            z,
            PdArrayEventType.IFVG_CONFIRMED,
            i,
            c.close,
            "displacement" if by_displacement else "acceptance",
        )


def _snapshot(
    z: _Zone, candles: Sequence[Candle], atr: float, trend: TrendDirection, cfg: PdArrayConfig
) -> PdArrayZone:
    n = len(candles)
    active = z.valid and z.state is not PdArrayState.FULL
    size_atr = z.size / atr if atr > 0 else 0.0
    quality: float | None = None
    if active:
        size_points = min(cfg.quality_size_max, cfg.quality_size_max * size_atr / cfg.quality_size_full_atr)
        disp_points = cfg.quality_displacement.get(
            z.displacement_grade.value if z.displacement_grade else "NONE", 0.0
        )
        fresh_points = cfg.quality_freshness.get(z.state.value, 0.0)
        aligned = (z.direction is Direction.BULLISH and trend is TrendDirection.BULLISH) or (
            z.direction is Direction.BEARISH and trend is TrendDirection.BEARISH
        )
        total = size_points + disp_points + fresh_points + (cfg.quality_trend_alignment if aligned else 0.0)
        quality = round(max(0.0, min(100.0, total)), 1)
    return PdArrayZone(
        id=z.id,
        type=z.type,
        direction=z.direction,
        top=z.top,
        bottom=z.bottom,
        midpoint=(z.top + z.bottom) / 2,
        size_atr=round(size_atr, 3),
        source_times=list(z.source_times),
        created_at=candles[z.created_index].open_time,
        known_at=candles[z.created_index].close_time,
        state=z.state,
        fill_pct=z.fill_pct,
        ifvg_status=z.ifvg_status,
        parent_id=z.parent_id,
        displacement_grade=z.displacement_grade,
        state_changed_at=candles[z.state_index].open_time if z.state_index is not None else None,
        invalidated_at=candles[z.invalidated_index].open_time if z.invalidated_index is not None else None,
        age_bars=n - 1 - z.created_index,
        active=active,
        quality_score=quality,
    )
