"""Pure session analysis over loaded series (no I/O): shared by the sessions API, decision and pipeline."""

from __future__ import annotations

from datetime import datetime

from app.contracts import strategy_version
from app.domain.enums import (
    AnalysisIneligibility,
    AssetClass,
    DataQuality,
    ExpansionState,
    LiquidityPoolType,
    SessionInstanceState,
    SessionName,
)
from app.services.candles.service import LoadedSeries
from app.services.liquidity.models import KeyLevel
from app.services.sessions.adr import adr_state
from app.services.sessions.clock import downgrade, session_clock
from app.services.sessions.judas import detect_judas
from app.services.sessions.levels import build_instances, opens, previous_session
from app.services.sessions.models import SessionAnalysis, SessionConfig, SessionInstance, SessionOpens
from app.services.sessions.sweep_confluence import detect_sweep_confluence

UNUSABLE = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})

POOL_TYPES: dict[SessionName, tuple[LiquidityPoolType, LiquidityPoolType]] = {
    SessionName.ASIA: (LiquidityPoolType.ASIA_HIGH, LiquidityPoolType.ASIA_LOW),
    SessionName.LONDON: (LiquidityPoolType.LONDON_HIGH, LiquidityPoolType.LONDON_LOW),
    SessionName.NY_AM: (LiquidityPoolType.NY_AM_HIGH, LiquidityPoolType.NY_AM_LOW),
    SessionName.NY_PM: (LiquidityPoolType.NY_PM_HIGH, LiquidityPoolType.NY_PM_LOW),
}


def usable(series: LoadedSeries | None) -> bool:
    return series is not None and series.quality not in UNUSABLE and any(c.is_closed for c in series.candles)


def data_ineligibility(series: LoadedSeries) -> list[AnalysisIneligibility]:
    reasons: list[AnalysisIneligibility] = []
    if series.is_synthetic:
        reasons.append(AnalysisIneligibility.DATA_SYNTHETIC)
    if series.quality is DataQuality.STALE:
        reasons.append(AnalysisIneligibility.DATA_STALE)
    elif series.quality is DataQuality.INVALID:
        reasons.append(AnalysisIneligibility.DATA_INVALID)
    elif series.quality is DataQuality.DISCONNECTED:
        reasons.append(AnalysisIneligibility.DATA_DISCONNECTED)
    return reasons


def session_key_levels(
    source: LoadedSeries, as_of: datetime, asset_class: AssetClass, cfg: SessionConfig
) -> list[KeyLevel]:
    """COMPLETE session highs/lows known at or before `as_of`, latest `instancesPerSession` per session."""
    instances = build_instances(source.candles, as_of, asset_class, cfg)
    levels: list[KeyLevel] = []
    for name in cfg.pool_sessions:
        high_type, low_type = POOL_TYPES[name]
        done = [
            i
            for i in instances
            if i.session is name and i.state is SessionInstanceState.COMPLETE and i.known_at is not None
        ][-cfg.pool_instances :]
        for i in done:
            assert i.high is not None and i.low is not None and i.known_at is not None
            day = i.trading_day.isoformat()
            levels.append(
                KeyLevel(
                    type=high_type,
                    price=i.high,
                    period_start=i.start,
                    known_at=i.known_at,
                    label=f"{name.value} high {day}",
                )
            )
            levels.append(
                KeyLevel(
                    type=low_type,
                    price=i.low,
                    period_start=i.start,
                    known_at=i.known_at,
                    label=f"{name.value} low {day}",
                )
            )
    return levels


def _empty_opens() -> SessionOpens:
    return SessionOpens(
        daily_open=None,
        daily_open_time=None,
        ny_midnight_open=None,
        ny_midnight_open_time=None,
        weekly_open=None,
        weekly_open_time=None,
        last_close=None,
        daily_change=None,
        daily_change_pct=None,
    )


def analyze_sessions(
    source: LoadedSeries,
    d1: LoadedSeries | None,
    asset_class: AssetClass,
    cfg: SessionConfig,
    failed: bool = False,
) -> SessionAnalysis:
    now = source.now
    closed = [c for c in source.candles if c.is_closed]
    reasons = data_ineligibility(source)
    clock = session_clock(now, asset_class, cfg)
    instances: list[SessionInstance] = []
    opens_state = _empty_opens()
    prev = None
    adr = None
    judas = []
    sweep_confluence = []
    as_of = max((c.close_time for c in closed), default=None)
    if failed:
        reasons.append(AnalysisIneligibility.SESSION_ANALYSIS_FAILED)
    elif as_of is not None and source.quality not in UNUSABLE:
        instances = build_instances(closed, as_of, asset_class, cfg)
        opens_state = opens(closed, as_of, asset_class, cfg)
        prev = previous_session(instances, as_of)
        adr = adr_state(d1.candles if usable(d1) and d1 is not None else [], closed, as_of, cfg.adr)
        judas = detect_judas(closed, instances, as_of, cfg)
        sweep_confluence = detect_sweep_confluence(closed, instances, as_of, cfg)
    quality = None
    if as_of is not None and not failed and source.quality not in UNUSABLE:
        quality = clock.time_quality
        if adr is not None and adr.expansion is ExpansionState.EXHAUSTED:
            quality = downgrade(quality)
    return SessionAnalysis(
        symbol=source.symbol,
        source_timeframe=source.timeframe,
        as_of=as_of,
        candle_count=len(closed),
        quality=source.quality,
        is_synthetic=source.is_synthetic,
        eligible_for_decision=not reasons and as_of is not None,
        ineligibility=reasons,
        clock=clock,
        session_quality=quality,
        instances=instances,
        opens=opens_state,
        previous_session=prev,
        adr=adr,
        judas=judas,
        sweep_confluence=sweep_confluence,
        provider_error=source.provider_error,
        strategy_version=strategy_version(),
        generated_at=now,
    )
