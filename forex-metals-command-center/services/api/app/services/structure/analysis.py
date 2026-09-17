"""Pure structure analysis over a loaded series (no I/O). Shared by the structure and liquidity services."""

from __future__ import annotations

from app.contracts import strategy_version
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    HtfBias,
    MtfAlignment,
    StructureLevel,
    StructureState,
    Timeframe,
)
from app.services.candles.service import LoadedSeries
from app.services.structure.engine import analyze_level
from app.services.structure.models import LevelStructure, StructureAnalysis, StructureConfig, StructureEvent

_LEVEL_ORDER = {StructureLevel.INTERNAL: 0, StructureLevel.EXTERNAL: 1}


def _ineligibility(
    series: LoadedSeries, closed_count: int, cfg: StructureConfig
) -> list[AnalysisIneligibility]:
    reasons: list[AnalysisIneligibility] = []
    if series.is_synthetic:
        reasons.append(AnalysisIneligibility.DATA_SYNTHETIC)
    if series.quality is DataQuality.STALE:
        reasons.append(AnalysisIneligibility.DATA_STALE)
    elif series.quality is DataQuality.INVALID:
        reasons.append(AnalysisIneligibility.DATA_INVALID)
    elif series.quality is DataQuality.DISCONNECTED:
        reasons.append(AnalysisIneligibility.DATA_DISCONNECTED)
    if closed_count < cfg.min_candles:
        reasons.append(AnalysisIneligibility.INSUFFICIENT_CANDLES)
    return reasons


def merged_events(internal: LevelStructure | None, external: LevelStructure | None) -> list[StructureEvent]:
    """Chronological event log of both structure levels."""
    events: list[StructureEvent] = []
    for lvl in (internal, external):
        if lvl is not None:
            events.extend(lvl.events)
    events.sort(key=lambda e: (e.time, _LEVEL_ORDER[e.level], e.id))
    return events


def analyze_series(series: LoadedSeries, cfg: StructureConfig) -> StructureAnalysis:
    closed = [c for c in series.candles if c.is_closed]
    reasons = _ineligibility(series, len(closed), cfg)
    withheld = series.quality in (DataQuality.INVALID, DataQuality.DISCONNECTED)
    internal = None if withheld else analyze_level(closed, StructureLevel.INTERNAL, cfg)
    external = None if withheld else analyze_level(closed, StructureLevel.EXTERNAL, cfg)
    events = merged_events(internal, external)
    return StructureAnalysis(
        symbol=series.symbol,
        timeframe=series.timeframe,
        as_of=closed[-1].close_time if closed and not withheld else None,
        candle_count=len(closed),
        quality=series.quality,
        is_synthetic=series.is_synthetic,
        eligible_for_decision=not reasons,
        ineligibility=reasons,
        internal=internal,
        external=external,
        events=events,
        provider_error=series.provider_error,
        strategy_version=strategy_version(),
        generated_at=series.now,
    )


def classify_alignment(states: list[StructureState | None]) -> MtfAlignment:
    if not states or any(s in (None, StructureState.UNCLEAR) for s in states):
        return MtfAlignment.UNCLEAR
    if all(s is StructureState.BULLISH for s in states):
        return MtfAlignment.ALIGNED_BULLISH
    if all(s is StructureState.BEARISH for s in states):
        return MtfAlignment.ALIGNED_BEARISH
    return MtfAlignment.MIXED


def classify_htf_bias(analyses: list[StructureAnalysis]) -> HtfBias:
    """Structural HTF bias from external structure of the HTF timeframes. Ineligible data -> UNKNOWN."""
    if not analyses or any(not a.eligible_for_decision or a.external is None for a in analyses):
        return HtfBias.UNKNOWN
    states = [a.external.state for a in analyses if a.external is not None]
    if any(s is StructureState.UNCLEAR for s in states):
        return HtfBias.UNCLEAR
    if all(s is StructureState.BULLISH for s in states):
        return HtfBias.BULLISH
    if all(s is StructureState.BEARISH for s in states):
        return HtfBias.BEARISH
    if any(s is StructureState.TRANSITIONING for s in states):
        return HtfBias.TRANSITIONING
    if all(s is StructureState.RANGING for s in states):
        return HtfBias.RANGING
    return HtfBias.MIXED


def describe_event(timeframe: Timeframe, event: StructureEvent) -> str:
    return (
        f"{timeframe.value} {event.level.value} {event.type.value} {event.direction.value} "
        f"@ {event.price} ({event.time.isoformat()}) liquidity {event.liquidity_qualifier.value}"
    )
