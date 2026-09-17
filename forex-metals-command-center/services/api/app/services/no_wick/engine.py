"""No Wick Architecture V1 engine: one sequential pass, facts at or before each candle only.

At candle i: (1) evaluate existing rebalance zones against candle i, (2) classify candle i from its features,
(3) score it from facts known at i's close, (4) create a rebalance zone for MEANINGFUL+ events
(tracked from i+1).
Upstream sources are optional: when an engine's output is unavailable the matching context component is
NOT_EVALUATED. No Wick never authorizes LONG/SHORT on its own.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    LiquidityEventType,
    LiquiditySide,
    NoWickZoneEventType,
    NoWickZoneState,
    PdArrayEventType,
    PdArrayType,
    ScoreComponentStatus,
    SessionQuality,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
)
from app.services.liquidity.models import LiquidityEvent
from app.services.no_wick.features import classify, compute_features
from app.services.no_wick.models import (
    CandleFeatures,
    NoWickConfig,
    NoWickEvent,
    NoWickZone,
    NoWickZoneEvent,
    ScoreComponent,
)
from app.services.no_wick.scoring import (
    F,
    candle_quality,
    component,
    context_total,
    not_evaluated,
    relevance,
)
from app.services.no_wick.zones import S, ZoneRuntime, evaluate_zone
from app.services.pd_arrays.displacement import atr_before
from app.services.pd_arrays.models import DisplacementEvent, PdArrayEvent, PdArrayZone
from app.services.structure.models import StructureEvent
from app.services.structure.swings import true_ranges

ACTIVE_ZONE_STATES = frozenset({S.FRESH, S.APPROACHING, S.TOUCHED, S.PARTIAL, S.HALF_REBALANCED})


def _by_index[T](
    index: dict[datetime, int], items: Iterable[T], time_of: Callable[[T], datetime]
) -> dict[int, list[T]]:
    grouped: dict[int, list[T]] = defaultdict(list)
    for item in items:
        t = time_of(item)
        if t in index:
            grouped[index[t]].append(item)
    return grouped


@dataclass(frozen=True)
class NoWickInputs:
    structure_events: Sequence[StructureEvent] | None
    liquidity_events: Sequence[LiquidityEvent] | None
    displacements: Sequence[DisplacementEvent] | None
    pd_events: Sequence[PdArrayEvent] | None
    pd_zones: Sequence[PdArrayZone] | None
    # Phase 6: time quality of a candle's open time (time only, no market data). None -> NOT_EVALUATED.
    session_quality: Callable[[datetime], SessionQuality] | None = None


@dataclass(frozen=True)
class NoWickResult:
    features: list[CandleFeatures]
    events: list[NoWickEvent]
    zones: list[NoWickZone]
    zone_events: list[NoWickZoneEvent]


def analyze_no_wick(candles: Sequence[Candle], inputs: NoWickInputs, cfg: NoWickConfig) -> NoWickResult:
    n = len(candles)
    features = compute_features(candles, cfg)
    trs = true_ranges(candles)
    index = {c.open_time: i for i, c in enumerate(candles)}

    structure_at = _by_index(
        index,
        [e for e in inputs.structure_events or [] if e.status is StructureEventStatus.CONFIRMED],
        lambda e: e.time,
    )
    liquidity_at = _by_index(index, inputs.liquidity_events or [], lambda e: e.time)
    displacement_at = _by_index(index, inputs.displacements or [], lambda d: d.time)
    fvg_created_at = _by_index(
        index,
        [
            e
            for e in inputs.pd_events or []
            if e.type is PdArrayEventType.CREATED and e.zone_type is PdArrayType.FVG
        ],
        lambda e: e.time,
    )

    zones: list[ZoneRuntime] = []
    zone_events: list[NoWickZoneEvent] = []
    events: list[NoWickEvent] = []
    trend: Direction | None = None

    def emit(z: ZoneRuntime, kind: NoWickZoneEventType, i: int, price: float, detail: str) -> None:
        z.state_index = i
        zone_events.append(
            NoWickZoneEvent(
                id=f"{z.id}:{kind.value}:{candles[i].open_time.isoformat()}",
                zone_id=z.id,
                direction=z.direction,
                type=kind,
                time=candles[i].open_time,
                price=price,
                detail=detail,
            )
        )

    for i, c in enumerate(candles):
        atr_now = atr_before(trs, i, cfg.atr_period) or 0.0
        for z in zones:
            evaluate_zone(
                z,
                c,
                i,
                cfg.zone_touch_tolerance_atr * z.atr_at_creation,
                cfg.zone_reaction_atr * z.atr_at_creation,
                cfg.zone_reaction_window_bars,
                emit,
            )

        # Trend as of candle i: last confirmed external BOS/MSS up to and including i.
        for e in structure_at.get(i, []):
            if e.level is StructureLevel.EXTERNAL and e.type in (
                StructureEventType.BOS,
                StructureEventType.MSS,
            ):
                trend = e.direction

        f = features[i]
        cls = classify(c, f, cfg)
        if cls is None or f.body_atr is None:
            continue
        inside_bar = i > 0 and c.high <= candles[i - 1].high and c.low >= candles[i - 1].low
        components = _context(
            i,
            c,
            cls.direction,
            trend,
            inputs,
            structure_at,
            liquidity_at,
            displacement_at,
            fvg_created_at,
            cfg,
        )
        quality = candle_quality(cls, f, cfg)
        context = context_total(components)
        rel = relevance(quality, context, cls.strength, inside_bar, cfg)
        event_id = f"NW:{cls.direction.value}:{c.open_time.isoformat()}"
        zone_id: str | None = None
        if cls.strength.rank >= 1 and atr_now > 0:  # MEANINGFUL+
            zone_id = f"NWZ:{cls.direction.value}:{c.open_time.isoformat()}"
            bullish = cls.direction is Direction.BULLISH
            zones.append(
                ZoneRuntime(
                    id=zone_id,
                    event_id=event_id,
                    direction=cls.direction,
                    close_level=c.close,
                    open_level=c.open,
                    origin_extreme=c.low if bullish else c.high,
                    created_index=i,
                    atr_at_creation=atr_now,
                    relevance_score=rel,
                    fvg_overlap_ids=_fvg_overlaps(c, i, inputs.pd_zones, index),
                )
            )
        events.append(
            NoWickEvent(
                id=event_id,
                direction=cls.direction,
                shape=cls.shape,
                classification=cls.classification,
                tags=cls.tags,
                strength=cls.strength,
                time=c.open_time,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                body_pct=round(f.body_pct or 0.0, 4),
                upper_wick_pct=round(f.upper_wick_pct or 0.0, 4),
                lower_wick_pct=round(f.lower_wick_pct or 0.0, 4),
                body_atr=round(f.body_atr, 3),
                close_location_pct=round(f.close_location_pct or 0.0, 2),
                inside_bar=inside_bar,
                candle_quality_score=quality,
                context_score=context,
                relevance_score=rel,
                context_components=components,
                zone_id=zone_id,
            )
        )

    last_close = candles[-1].close if n else 0.0
    last_atr = (atr_before(trs, n, cfg.atr_period) or 0.0) if n else 0.0
    return NoWickResult(
        features=features,
        events=events,
        zones=[_snapshot(z, candles, last_close, last_atr, cfg) for z in zones],
        zone_events=zone_events,
    )


def _context(
    i: int,
    candle: Candle,
    direction: Direction,
    trend: Direction | None,
    inputs: NoWickInputs,
    structure_at: dict[int, list[StructureEvent]],
    liquidity_at: dict[int, list[LiquidityEvent]],
    displacement_at: dict[int, list[DisplacementEvent]],
    fvg_created_at: dict[int, list[PdArrayEvent]],
    cfg: NoWickConfig,
) -> list[ScoreComponent]:
    comps = []
    if inputs.displacements is None:
        comps.append(not_evaluated(F.DISPLACEMENT, "displacement engine unavailable"))
    else:
        grades = [d.grade for d in displacement_at.get(i, []) if d.direction is direction]
        best = max(grades, key=lambda g: g.rank) if grades else None
        comps.append(
            component(
                F.DISPLACEMENT,
                cfg.c_displacement[best.value] if best else 0.0,
                max(cfg.c_displacement.values()),
                best.value if best else "none on this candle",
            )
        )

    if inputs.structure_events is None:
        comps.append(not_evaluated(F.STRUCTURE, "structure unavailable"))
        comps.append(not_evaluated(F.TREND, "structure unavailable"))
    else:
        types = [e.type.value for e in structure_at.get(i, []) if e.direction is direction]
        best_type = max(types, key=lambda t: cfg.c_structure[t]) if types else None
        comps.append(
            component(
                F.STRUCTURE,
                cfg.c_structure[best_type] if best_type else 0.0,
                max(cfg.c_structure.values()),
                best_type or "no break on this candle",
            )
        )
        aligned = trend is direction
        comps.append(
            component(
                F.TREND,
                cfg.c_trend if aligned else 0.0,
                cfg.c_trend,
                f"trend as of candle: {trend.value if trend else 'NONE'}",
            )
        )

    if inputs.liquidity_events is None:
        comps.append(not_evaluated(F.LIQUIDITY, "liquidity engine unavailable"))
    else:
        needed = LiquiditySide.SSL if direction is Direction.BULLISH else LiquiditySide.BSL
        taken = any(
            e.side is needed and e.type in (LiquidityEventType.SWEEP, LiquidityEventType.RECLAIM)
            for j in range(max(0, i - cfg.c_liquidity_lookback_bars), i + 1)
            for e in liquidity_at.get(j, [])
        )
        comps.append(
            component(
                F.LIQUIDITY,
                cfg.c_liquidity if taken else 0.0,
                cfg.c_liquidity,
                f"{needed.value} taken within {cfg.c_liquidity_lookback_bars} bars" if taken else "none",
            )
        )

    if inputs.pd_events is None:
        comps.append(not_evaluated(F.FVG, "FVG engine unavailable"))
    else:
        created = any(e.direction is direction for e in fvg_created_at.get(i, []))
        comps.append(
            component(
                F.FVG,
                cfg.c_fvg if created else 0.0,
                cfg.c_fvg,
                "created on this candle" if created else "none completed by this candle",
            )
        )

    if inputs.session_quality is None:
        comps.append(not_evaluated(F.SESSION, "session clock unavailable"))
    else:
        quality = inputs.session_quality(candle.open_time)
        comps.append(
            component(
                F.SESSION,
                cfg.c_session.get(quality.value, 0.0),
                max(cfg.c_session.values()),
                f"time quality {quality.value}",
            )
        )
    comps.append(not_evaluated(F.NEWS, "The economic calendar is not wired into No Wick context"))
    return comps


def _fvg_overlaps(
    c: Candle, i: int, pd_zones: Sequence[PdArrayZone] | None, index: dict[datetime, int]
) -> list[str]:
    if not pd_zones:
        return []
    lo, hi = min(c.open, c.close), max(c.open, c.close)
    out = []
    for z in pd_zones:
        created = index.get(z.created_at)
        if created is None or created > i:  # FVG not known yet at this candle's close
            continue
        if z.invalidated_at is not None and index.get(z.invalidated_at, i + 1) <= i:
            continue
        if z.bottom < hi and z.top > lo:
            out.append(z.id)
    return out


def _snapshot(
    z: ZoneRuntime, candles: Sequence[Candle], last_close: float, atr: float, cfg: NoWickConfig
) -> NoWickZone:
    n = len(candles)
    state = z.state
    if (
        state is S.FRESH
        and atr > 0
        and abs(last_close - z.close_level) <= cfg.zone_approach_distance_atr * atr
    ):
        state = NoWickZoneState.APPROACHING
    return NoWickZone(
        id=z.id,
        event_id=z.event_id,
        direction=z.direction,
        close_level=z.close_level,
        level_25=z.level(25),
        level_50=z.level(50),
        level_75=z.level(75),
        open_level=z.open_level,
        origin_extreme=z.origin_extreme,
        fvg_overlap_ids=list(z.fvg_overlap_ids),
        ob_overlap=ScoreComponentStatus.NOT_EVALUATED,
        state=state,
        rebalance_pct=z.rebalance_pct,
        created_at=candles[z.created_index].open_time,
        known_at=candles[z.created_index].close_time,
        state_changed_at=candles[z.state_index].open_time if z.state_index is not None else None,
        age_bars=n - 1 - z.created_index,
        active=state in ACTIVE_ZONE_STATES,
        relevance_score=z.relevance_score,
    )
