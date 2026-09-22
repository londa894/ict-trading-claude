"""Single structure -> liquidity -> displacement/FVG pipeline used by every surface (API, chart, decision).

Order follows the spec update cycle: structure (7) -> liquidity (8) -> DOL (9) -> displacement (10)
-> PD arrays (11)
-> No Wick (12, with Phase 6 session time quality). Liquidity also receives COMPLETE session
highs/lows
(Phase 6) from the M15 session source. Each stage after structure fails independently; No Wick context
marks missing sources NOT_EVALUATED.
Liquidity failure never breaks structure: structure events keep NOT_EVALUATED qualifiers and the
liquidity analysis is returned withheld and ineligible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec, strategy_version
from app.domain.enums import (
    AnalysisIneligibility,
    AssetClass,
    DataQuality,
    SessionQuality,
    Timeframe,
    TrendDirection,
)
from app.domain.instrument import get_instrument
from app.services.candles.service import CandleService, LoadedSeries
from app.services.liquidity.engine import analyze_liquidity
from app.services.liquidity.key_levels import key_levels
from app.services.liquidity.models import LiquidityAnalysis, LiquidityConfig, LiquidityEvent
from app.services.liquidity.qualifiers import qualify_level
from app.services.no_wick.engine import NoWickInputs, NoWickResult, analyze_no_wick
from app.services.no_wick.models import NoWickAnalysis, NoWickConfig
from app.services.pd_arrays.displacement import detect_displacements
from app.services.pd_arrays.fvg import detect_fvgs
from app.services.pd_arrays.imr import detect_imrs
from app.services.pd_arrays.models import PdArrayAnalysis, PdArrayConfig
from app.services.pd_arrays.qualifiers import qualify_displacement
from app.services.sessions.analysis import session_key_levels, usable
from app.services.sessions.clock import time_quality
from app.services.sessions.models import SessionConfig
from app.services.structure.analysis import analyze_series, merged_events
from app.services.structure.models import StructureAnalysis, StructureConfig

logger = logging.getLogger("fmcc.analysis")

_UNUSABLE = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})
SESSION_SCORED_TIMEFRAMES = frozenset({Timeframe.M5, Timeframe.M15, Timeframe.H1})


@dataclass(frozen=True)
class PipelineResult:
    structure: StructureAnalysis
    liquidity: LiquidityAnalysis
    pd_arrays: PdArrayAnalysis
    no_wick: NoWickAnalysis


def _quality_fn(
    asset_class: AssetClass | None, cfg: SessionConfig
) -> Callable[[datetime], SessionQuality] | None:
    if asset_class is None:
        return None
    return lambda t: time_quality(t, asset_class, cfg)


def key_level_limit(cfg: LiquidityConfig) -> int:
    # enough closed D1 candles for `days` PDH/PDL and `weeks` completed weeks (+ the forming week/day)
    return cfg.key_level_days + 5 * (cfg.key_level_weeks + 1) + 2


async def load_inputs(
    candles: CandleService, symbol: str, timeframe: Timeframe, limit: int, now: datetime, cfg: LiquidityConfig
) -> tuple[LoadedSeries, LoadedSeries, LoadedSeries]:
    series = await candles.load_series(symbol, timeframe, limit, now)
    d1 = await candles.load_series(
        symbol,
        Timeframe(load_spec("liquidity")["decisionContext"]["keyLevelTimeframe"]),
        key_level_limit(cfg),
        now,
    )
    ss_cfg = SessionConfig.from_spec()
    sessions = await candles.load_series(symbol, ss_cfg.source_timeframe, ss_cfg.pool_source_bars, now)
    return series, d1, sessions


def run_pipeline(
    series: LoadedSeries,
    d1: LoadedSeries | None,
    s_cfg: StructureConfig,
    l_cfg: LiquidityConfig,
    pd_cfg: PdArrayConfig | None = None,
    nw_cfg: NoWickConfig | None = None,
    *,
    sessions: LoadedSeries | None = None,
    ss_cfg: SessionConfig | None = None,
) -> PipelineResult:
    """structure -> liquidity -> displacement -> FVG/IFVG -> structure qualifiers.

    Liquidity and PD arrays are independent stages: a failure in one withholds only that analysis and leaves
    the corresponding structure qualifier NOT_EVALUATED.
    """
    pd_cfg = pd_cfg or PdArrayConfig.from_spec()
    nw_cfg = nw_cfg or NoWickConfig.from_spec()
    ss_cfg = ss_cfg or SessionConfig.from_spec()
    instrument = get_instrument(series.symbol)
    asset_class = instrument.asset_class if instrument else None
    structure = analyze_series(series, s_cfg)
    withheld = structure.internal is None and structure.external is None
    closed = [c for c in series.candles if c.is_closed]
    base_reasons = list(structure.ineligibility)

    key_ok = d1 is not None and d1.quality not in _UNUSABLE and any(c.is_closed for c in d1.candles)
    sessions_ok = usable(sessions) and asset_class is not None
    # A candle's time quality is judged by its open time: meaningful for intraday candles only (an H4/D1
    # candle spans several windows), so No Wick SESSION stays NOT_EVALUATED above H1.
    scored_class = asset_class if series.timeframe in SESSION_SCORED_TIMEFRAMES else None
    liquidity_reasons = [*base_reasons] + ([] if key_ok else [AnalysisIneligibility.KEY_LEVELS_UNAVAILABLE])
    if not sessions_ok:
        liquidity_reasons.append(AnalysisIneligibility.SESSION_LEVELS_UNAVAILABLE)

    def liquidity(pools, events, dol, extra: list[AnalysisIneligibility]) -> LiquidityAnalysis:  # type: ignore[no-untyped-def]
        all_reasons = [*liquidity_reasons, *extra]
        return LiquidityAnalysis(
            symbol=series.symbol,
            timeframe=series.timeframe,
            as_of=structure.as_of,
            candle_count=len(closed),
            quality=series.quality,
            is_synthetic=series.is_synthetic,
            eligible_for_decision=not all_reasons,
            ineligibility=all_reasons,
            key_levels_available=key_ok,
            pools=pools,
            events=events,
            dol=dol,
            provider_error=series.provider_error,
            strategy_version=strategy_version(),
            generated_at=series.now,
        )

    def pd_arrays(displacements, zones, events, extra: list[AnalysisIneligibility]) -> PdArrayAnalysis:  # type: ignore[no-untyped-def]
        all_reasons = [*base_reasons, *extra]
        return PdArrayAnalysis(
            symbol=series.symbol,
            timeframe=series.timeframe,
            as_of=structure.as_of,
            candle_count=len(closed),
            quality=series.quality,
            is_synthetic=series.is_synthetic,
            eligible_for_decision=not all_reasons,
            ineligibility=all_reasons,
            displacements=displacements,
            zones=zones,
            events=events,
            provider_error=series.provider_error,
            strategy_version=strategy_version(),
            generated_at=series.now,
        )

    def no_wick(result: NoWickResult | None, extra: list[AnalysisIneligibility]) -> NoWickAnalysis:
        all_reasons = [*base_reasons, *extra]
        return NoWickAnalysis(
            symbol=series.symbol,
            timeframe=series.timeframe,
            as_of=structure.as_of,
            candle_count=len(closed),
            quality=series.quality,
            is_synthetic=series.is_synthetic,
            eligible_for_decision=not all_reasons,
            ineligibility=all_reasons,
            features=result.features if result else [],
            events=result.events if result else [],
            zones=result.zones if result else [],
            zone_events=result.zone_events if result else [],
            provider_error=series.provider_error,
            strategy_version=strategy_version(),
            generated_at=series.now,
        )

    if withheld or not closed:
        return PipelineResult(
            structure=structure,
            liquidity=liquidity([], [], None, []),
            pd_arrays=pd_arrays([], [], [], []),
            no_wick=no_wick(None, []),
        )

    times = [c.open_time for c in closed]
    internal, external = structure.internal, structure.external
    trend = external.trend if external else TrendDirection.NONE

    try:
        levels = (
            key_levels(d1.candles, closed[-1].close_time, l_cfg.key_level_days, l_cfg.key_level_weeks)
            if key_ok and d1 is not None
            else []
        )
        if sessions_ok and sessions is not None and asset_class is not None:
            levels += session_key_levels(sessions, closed[-1].close_time, asset_class, ss_cfg)
        liq = analyze_liquidity(closed, structure.internal, structure.external, levels, trend, l_cfg)
        internal = qualify_level(internal, liq.events, times, l_cfg.qualifier_lookback_bars)
        external = qualify_level(external, liq.events, times, l_cfg.qualifier_lookback_bars)
        liquidity_result = liquidity(liq.pools, liq.events, liq.dol, [])
        liquidity_events: list[LiquidityEvent] | None = liq.events
    except Exception:
        logger.exception("liquidity analysis failed for %s %s", series.symbol, series.timeframe)
        liquidity_result = liquidity([], [], None, [AnalysisIneligibility.LIQUIDITY_ANALYSIS_FAILED])
        liquidity_events = None

    try:
        displacements = detect_displacements(closed, pd_cfg)
        fvgs = detect_fvgs(closed, displacements, trend, pd_cfg)
        imrs = detect_imrs(closed, displacements, trend, pd_cfg)
        internal = qualify_displacement(
            internal, displacements, times, pd_cfg.qualifier_lookback_bars, pd_cfg.qualifier_min_grade
        )
        external = qualify_displacement(
            external, displacements, times, pd_cfg.qualifier_lookback_bars, pd_cfg.qualifier_min_grade
        )
        pd_result = pd_arrays(
            displacements, [*fvgs.zones, *imrs.zones], [*fvgs.events, *imrs.events], []
        )
        pd_ok = True
    except Exception:
        logger.exception("pd-array analysis failed for %s %s", series.symbol, series.timeframe)
        pd_result = pd_arrays([], [], [], [AnalysisIneligibility.PD_ARRAY_ANALYSIS_FAILED])
        pd_ok = False

    qualified = structure.model_copy(
        update={"internal": internal, "external": external, "events": merged_events(internal, external)}
    )

    try:
        inputs = NoWickInputs(
            structure_events=qualified.events,
            liquidity_events=liquidity_events,
            displacements=pd_result.displacements if pd_ok else None,
            pd_events=pd_result.events if pd_ok else None,
            pd_zones=pd_result.zones if pd_ok else None,
            session_quality=_quality_fn(scored_class, ss_cfg),
        )
        nw_result = no_wick(analyze_no_wick(closed, inputs, nw_cfg), [])
    except Exception:
        logger.exception("no-wick analysis failed for %s %s", series.symbol, series.timeframe)
        nw_result = no_wick(None, [AnalysisIneligibility.NO_WICK_ANALYSIS_FAILED])

    return PipelineResult(
        structure=qualified, liquidity=liquidity_result, pd_arrays=pd_result, no_wick=nw_result
    )
