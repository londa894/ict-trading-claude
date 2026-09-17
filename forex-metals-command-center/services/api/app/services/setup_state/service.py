"""Setup service: the shared pipeline on the setup timeframe, plus bias timeframes and session context.

Setups are context: the Master Decision receives the current setup state and type, never a LONG/SHORT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.contracts import strategy_version
from app.domain.candle import Candle
from app.domain.enums import AnalysisIneligibility, DataQuality, Po3Phase, SetupState, SetupType
from app.domain.instrument import get_instrument
from app.services.analysis.pipeline import PipelineResult, load_inputs, run_pipeline
from app.services.candles.service import CandleService, LoadedSeries, UnknownSymbolError
from app.services.entry.models import EntryConfig
from app.services.liquidity.models import LiquidityConfig
from app.services.sessions.clock import trading_day_of
from app.services.sessions.models import SessionAnalysis
from app.services.sessions.service import SessionService
from app.services.setup_state.bias import bias_points
from app.services.setup_state.engine import SetupInputs, analyze_setups, bias_at
from app.services.setup_state.models import BiasState, Po3State, SetupAnalysis, SetupConfig
from app.services.setup_state.po3 import po3_state
from app.services.structure.analysis import analyze_series
from app.services.structure.models import StructureConfig

logger = logging.getLogger("fmcc.setups")


def _dedupe(reasons: list[AnalysisIneligibility]) -> list[AnalysisIneligibility]:
    return list(dict.fromkeys(reasons))


@dataclass(frozen=True)
class SetupRun:
    """Everything one setup analysis computed, reused by the evaluation (no second pipeline pass)."""

    analysis: SetupAnalysis
    pipeline: PipelineResult | None
    session: SessionAnalysis
    candles: tuple[Candle, ...] = ()  # closed setup-timeframe candles


class SetupService:
    def __init__(
        self,
        candles: CandleService,
        sessions: SessionService | None = None,
        cfg: SetupConfig | None = None,
        structure_cfg: StructureConfig | None = None,
        liquidity_cfg: LiquidityConfig | None = None,
        entry_cfg: EntryConfig | None = None,
    ) -> None:
        self._candles = candles
        self._sessions = sessions or SessionService(candles)
        self._cfg = cfg or SetupConfig.from_spec()
        self._s_cfg = structure_cfg or StructureConfig.from_spec()
        self._l_cfg = liquidity_cfg or LiquidityConfig.from_spec()
        self._e_cfg = entry_cfg or EntryConfig.from_spec()

    @property
    def entry_cfg(self) -> EntryConfig:
        return self._e_cfg

    async def analyze(self, symbol: str, now: datetime | None = None) -> SetupAnalysis:
        return (await self.run(symbol, now)).analysis

    async def run(self, symbol: str, now: datetime | None = None) -> SetupRun:
        now = now or self._candles.now()
        if get_instrument(symbol.upper()) is None:
            raise UnknownSymbolError(symbol)
        tf = self._cfg.setup_timeframe
        series, d1, session_src = await load_inputs(
            self._candles, symbol, tf, self._cfg.setup_bars, now, self._l_cfg
        )
        bias_series = [
            await self._candles.load_series(symbol, btf, self._cfg.bias_bars, now)
            for btf in self._cfg.bias_timeframes
        ]
        ltf = await self._candles.load_series(
            symbol, self._e_cfg.execution_timeframe, self._e_cfg.execution_bars, now
        )
        session = await self._sessions.analyze(symbol, now)
        try:
            analysis, pipeline = self._analyze(series, d1, session_src, bias_series, session, ltf)
            closed = tuple(c for c in series.candles if c.is_closed)
            return SetupRun(analysis=analysis, pipeline=pipeline, session=session, candles=closed)
        except Exception:
            logger.exception("setup analysis failed for %s", symbol)
            empty = self._empty(series, [AnalysisIneligibility.SETUP_ANALYSIS_FAILED])
            return SetupRun(analysis=empty, pipeline=None, session=session)

    def _analyze(
        self,
        series: LoadedSeries,
        d1: LoadedSeries,
        session_src: LoadedSeries,
        bias_series: list[LoadedSeries],
        session: SessionAnalysis,
        ltf: LoadedSeries,
    ) -> tuple[SetupAnalysis, PipelineResult]:
        result = run_pipeline(series, d1, self._s_cfg, self._l_cfg, sessions=session_src)
        closed = [c for c in series.candles if c.is_closed]
        reasons = [
            *result.structure.ineligibility,
            *result.liquidity.ineligibility,
            *result.pd_arrays.ineligibility,
        ]
        bias_analyses = [analyze_series(b, self._s_cfg) for b in bias_series]
        unusable = (DataQuality.INVALID, DataQuality.DISCONNECTED)
        if any(a.external is None or a.quality in unusable for a in bias_analyses):
            reasons.append(AnalysisIneligibility.SETUP_BIAS_UNAVAILABLE)
        reasons += [r for a in bias_analyses for r in a.ineligibility]
        withheld = result.structure.internal is None and result.structure.external is None
        if withheld or not closed:
            return self._empty(series, _dedupe(reasons)), result

        points = [p for a in bias_analyses for p in bias_points(a)]
        setups = analyze_setups(
            closed,
            SetupInputs(
                structure_events=result.structure.events,
                pools=result.liquidity.pools,
                liquidity_events=result.liquidity.events,
                pd_zones=result.pd_arrays.zones,
                pd_events=result.pd_arrays.events,
                bias_points=points,
                bias_timeframes=self._cfg.bias_timeframes,
                no_wick_events=result.no_wick.events,
                ltf_candles=[c for c in ltf.candles if c.is_closed],
                ltf_breaks=analyze_series(ltf, self._s_cfg).events,
            ),
            self._cfg,
            trading_day_of,
            self._e_cfg,
        )
        as_of = closed[-1].close_time
        bias = bias_at(points, self._cfg.bias_timeframes, as_of)
        latest = []
        for btf in self._cfg.bias_timeframes:
            known = [p for p in points if p.timeframe is btf and p.known_at <= as_of]
            if known:
                latest.append(max(known, key=lambda p: p.known_at))
        reasons = _dedupe(reasons)
        eligible = not reasons
        current = next((s for s in reversed(setups.setups) if not s.terminal), None)
        day = trading_day_of(closed[-1].open_time)
        today = [c for c in closed if trading_day_of(c.open_time) == day]
        po3 = po3_state(
            today,
            day,
            session.opens.daily_open,
            session.adr.adr if session.adr else None,
            bias,
            self._cfg,
        )
        analysis = SetupAnalysis(
            symbol=series.symbol,
            timeframe=series.timeframe,
            as_of=as_of,
            candle_count=len(closed),
            quality=series.quality,
            is_synthetic=series.is_synthetic,
            eligible_for_decision=eligible,
            ineligibility=reasons,
            bias=BiasState(direction=bias, timeframes=list(self._cfg.bias_timeframes), latest=latest),
            current_state=(current.state if current else None) if eligible else SetupState.BLOCKED,
            current=current,
            setups=setups.setups,
            events=setups.events,
            po3=po3,
            provider_error=series.provider_error,
            strategy_version=strategy_version(),
            generated_at=series.now,
        )
        return analysis, result

    def _empty(self, series: LoadedSeries, reasons: list[AnalysisIneligibility]) -> SetupAnalysis:
        closed = [c for c in series.candles if c.is_closed]
        return SetupAnalysis(
            symbol=series.symbol,
            timeframe=series.timeframe,
            as_of=closed[-1].close_time if closed else None,
            candle_count=len(closed),
            quality=series.quality,
            is_synthetic=series.is_synthetic,
            eligible_for_decision=False,
            ineligibility=reasons,
            bias=BiasState(direction=None, timeframes=list(self._cfg.bias_timeframes), latest=[]),
            current_state=SetupState.BLOCKED,
            current=None,
            setups=[],
            events=[],
            po3=Po3State(
                trading_day=None, phase=Po3Phase.UNCLEAR, daily_open=None, adr=None, detail="no data"
            ),
            provider_error=series.provider_error,
            strategy_version=strategy_version(),
            generated_at=series.now,
        )

    async def decision_context(
        self, symbol: str, now: datetime | None = None
    ) -> tuple[str, str | None] | None:
        """(setupState, setupType) for the Master Decision, from eligible data only."""
        a = await self.analyze(symbol, now)
        if not a.eligible_for_decision:
            return None
        if a.current is None:
            return ("NO_SETUP", None)
        return (a.current.state.value, SetupType.LIQUIDITY_SWEEP_MSS.value)
