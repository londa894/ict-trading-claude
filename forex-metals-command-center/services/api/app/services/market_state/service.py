"""Orchestrates Phase 0 update-cycle steps 1-4 + fail-safe verdict:
ingest -> validate -> normalize time -> candle series -> gate -> publish-if-changed."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.base import ApiModel
from app.domain.decision import MasterDecision
from app.domain.enums import (
    Blocker,
    DataQuality,
    EvaluationOutcome,
    HtfBias,
    MarketStatus,
    PositionSizeStatus,
    ProviderHealthStatus,
    Timeframe,
    Verdict,
)
from app.domain.instrument import get_instrument
from app.domain.issues import ValidationIssue
from app.providers.base import MarketDataProvider, ProviderError
from app.services.candles.normalize import build_series
from app.services.data_quality.market_hours import market_status_at
from app.services.events.bus import DomainEvent, EventBus
from app.services.liquidity.service import LiquidityService
from app.services.macro.service import MacroService
from app.services.macro.service import decision_context as macro_decision_context
from app.services.market_state.gate import (
    GateInput,
    enforce_verdict_authority,
    evaluate,
    ordered_blockers,
)
from app.services.news.service import NewsService
from app.services.news.service import decision_context as news_decision_context
from app.services.no_wick.service import NoWickService
from app.services.pd_arrays.service import PdArrayService
from app.services.scoring.service import EvaluationService
from app.services.sessions.service import SessionService
from app.services.setup_state.service import SetupService
from app.services.structure.service import StructureService

DECISION_CHANGED = "market_state.decision_changed"
MARKET_STATE_BARS = 500  # bounded ingestion window for the execution timeframe
logger = logging.getLogger("fmcc.market_state")


class DataReport(ApiModel):
    provider: str
    is_synthetic: bool
    timeframe: Timeframe
    candle_count: int
    latest_closed_open_time: datetime | None
    quality: DataQuality
    market_status: MarketStatus
    position_size_status: PositionSizeStatus | None
    issues: list[ValidationIssue]
    provider_error: str | None = None


class MarketStateResponse(ApiModel):
    decision: MasterDecision
    data: DataReport


class MarketStateService:
    def __init__(
        self,
        provider: MarketDataProvider,
        bus: EventBus,
        execution_timeframe: Timeframe = Timeframe.M5,
        clock: Callable[[], datetime] | None = None,
        structure: StructureService | None = None,
        liquidity: LiquidityService | None = None,
        pd_arrays: PdArrayService | None = None,
        no_wick: NoWickService | None = None,
        sessions: SessionService | None = None,
        setups: SetupService | None = None,
        evaluation: EvaluationService | None = None,
        news: NewsService | None = None,
        macro: MacroService | None = None,
    ) -> None:
        self._provider = provider
        self._structure = structure
        self._liquidity = liquidity
        self._pd_arrays = pd_arrays
        self._no_wick = no_wick
        self._sessions = sessions
        self._setups = setups
        self._evaluation = evaluation
        self._news = news
        self._macro = macro
        self._bus = bus
        self._timeframe = execution_timeframe
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last: dict[str, tuple[object, ...]] = {}

    async def evaluate(self, symbol: str) -> MarketStateResponse:
        now = self._clock()
        symbol = symbol.upper()
        instrument = get_instrument(symbol)

        provider_error: str | None = None
        provider_ok = False
        issues: list[ValidationIssue] = []
        quality = DataQuality.DISCONNECTED
        candle_count = 0
        latest_closed: datetime | None = None
        market_status = MarketStatus.UNKNOWN

        if instrument is not None:
            market_status = market_status_at(instrument.asset_class, now)
            try:
                health = await self._provider.health_check()
                if health.status in (ProviderHealthStatus.HEALTHY, ProviderHealthStatus.DEGRADED):
                    raw = await self._provider.get_historical_bars(
                        symbol, self._timeframe, end=now, limit=MARKET_STATE_BARS
                    )
                    series = build_series(
                        raw,
                        symbol=symbol,
                        timeframe=self._timeframe,
                        source=getattr(self._provider, "source", self._provider.name),
                        asset_class=instrument.asset_class,
                        now=now,
                    )
                    provider_ok = True
                    issues = series.issues
                    quality = series.quality
                    candle_count = len(series.candles)
                    closed = series.closed
                    latest_closed = closed[-1].open_time if closed else None
                else:
                    provider_error = health.message
            except ProviderError as exc:
                provider_error = str(exc) or type(exc).__name__
            except Exception as exc:
                # Unexpected failure in ingestion must never fail open.
                logger.exception("market-state ingestion failed for %s", symbol)
                provider_error = f"unexpected ingestion failure: {type(exc).__name__}"

        decision = evaluate(
            GateInput(
                symbol=symbol,
                now=now,
                instrument=instrument,
                provider_available=provider_ok,
                is_synthetic=self._provider.is_synthetic,
                data_quality=quality,
                issues=tuple(issues),
                market_status=market_status,
            )
        )
        if self._structure is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: structure can describe context but can never change the verdict.
            try:
                ctx = await self._structure.decision_context(symbol, now)
                decision = decision.model_copy(
                    update={"htf_bias": ctx.htf_bias.value, "structure_event": ctx.structure_event}
                )
            except Exception:
                logger.exception("structure decision context failed for %s", symbol)
                decision = decision.model_copy(
                    update={"htf_bias": HtfBias.UNKNOWN.value, "structure_event": None}
                )
        if self._liquidity is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only. The single thing liquidity may add is the WAIT-class DOL_UNCLEAR blocker.
            try:
                lctx = await self._liquidity.decision_context(symbol, now)
                blockers = set(decision.blockers)
                if lctx.dol_unclear:
                    blockers.add(Blocker.DOL_UNCLEAR)
                decision = decision.model_copy(
                    update={
                        "primary_dol": lctx.primary_dol,
                        "secondary_dol": lctx.secondary_dol,
                        "liquidity_event": lctx.liquidity_event,
                        "blockers": ordered_blockers(blockers),
                    }
                )
            except Exception:
                logger.exception("liquidity decision context failed for %s", symbol)
                decision = decision.model_copy(
                    update={"primary_dol": None, "secondary_dol": None, "liquidity_event": None}
                )
            decision = enforce_verdict_authority(decision)
        if self._pd_arrays is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: latest meaningful displacement; never touches verdict or blockers.
            try:
                dctx = await self._pd_arrays.decision_context(symbol, now)
                decision = decision.model_copy(update={"displacement": dctx.displacement})
            except Exception:
                logger.exception("displacement decision context failed for %s", symbol)
                decision = decision.model_copy(update={"displacement": None})
            decision = enforce_verdict_authority(decision)
        if self._no_wick is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: No Wick never authorizes LONG/SHORT (spec STEP 13).
            try:
                state = await self._no_wick.decision_context(symbol, now)
                decision = decision.model_copy(update={"no_wick_state": state})
            except Exception:
                logger.exception("no-wick decision context failed for %s", symbol)
                decision = decision.model_copy(update={"no_wick_state": None})
            decision = enforce_verdict_authority(decision)
        if self._sessions is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: time never creates a trade by itself (spec STEP 4).
            try:
                session_state = await self._sessions.decision_context(symbol, now)
                decision = decision.model_copy(update={"session_state": session_state})
            except Exception:
                logger.exception("session decision context failed for %s", symbol)
                decision = decision.model_copy(update={"session_state": None})
            decision = enforce_verdict_authority(decision)
        if self._evaluation is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: score, grade, capped confidence and WAIT-class blockers. The outcome is never a
            # verdict; entry/stop/targets stay null while authority is FAIL_SAFE_ONLY.
            try:
                bias = decision.htf_bias if decision.htf_bias in HtfBias.__members__ else None
                ev = await self._evaluation.evaluate(
                    symbol,
                    now,
                    htf_bias=HtfBias(bias) if bias else None,
                    primary_dol=decision.primary_dol,
                    resolve_context=False,
                )
                blockers = set(decision.blockers)
                update: dict[str, object] = {}
                if ev.news is not None:
                    # The news gate applies whatever the market data (calendar-driven).
                    update["news_state"] = news_decision_context(ev.news)
                    blockers |= set(ev.news.blockers)
                if ev.macro is not None:
                    # Context only: macro never adds blockers and never sets the verdict.
                    update["macro_state"] = macro_decision_context(ev.macro)
                if ev.risk is not None:
                    # Risk status and risk blockers (locks, missing/invalid profile) apply whatever the data.
                    update["risk_status"] = ev.risk.status.value
                    blockers |= set(ev.risk.blockers)
                if ev.eligible_for_decision:
                    blockers |= set(ev.hard_blockers)
                    if Blocker.INSTRUMENT_SPEC_MISSING not in ev.hard_blockers:
                        blockers.discard(Blocker.INSTRUMENT_SPEC_MISSING)  # a user-supplied spec exists
                    if ev.outcome is EvaluationOutcome.CONFIRMED_PENDING_GATES:
                        blockers |= set(ev.missing_gates)
                    update |= {
                        "setup_state": ev.setup_state.value if ev.setup_state else "NO_SETUP",
                        "setup_type": ev.setup_type.value if ev.setup_type else None,
                        "setup_score": ev.score,
                        "setup_grade": ev.grade.value if ev.grade else None,
                        "decision_confidence": ev.confidence,
                    }
                update["blockers"] = ordered_blockers(blockers)
                decision = decision.model_copy(update=update)
            except Exception:
                logger.exception("evaluation decision context failed for %s", symbol)
            decision = enforce_verdict_authority(decision)
        elif self._setups is not None and decision.verdict is not Verdict.UNAVAILABLE:
            # Enrichment only: the setup state describes progress; it never sets verdict or direction.
            try:
                setup = await self._setups.decision_context(symbol, now)
                if setup is not None:
                    decision = decision.model_copy(update={"setup_state": setup[0], "setup_type": setup[1]})
            except Exception:
                logger.exception("setup decision context failed for %s", symbol)
            decision = enforce_verdict_authority(decision)
        if self._news is not None and decision.news_state is None and instrument is not None:
            # The news gate is calendar-driven: every surface sees it even when market data is unusable.
            try:
                assessment = await self._news.assess(symbol, now)
                decision = decision.model_copy(
                    update={
                        "news_state": news_decision_context(assessment),
                        "blockers": ordered_blockers(set(decision.blockers) | set(assessment.blockers)),
                    }
                )
            except Exception:
                logger.exception("news decision context failed for %s", symbol)
            decision = enforce_verdict_authority(decision)
        if self._macro is not None and decision.macro_state is None and instrument is not None:
            # Macro bias is data-driven context: shown even when the market data cannot be decided on.
            try:
                macro = await self._macro.assess(symbol, now)
                decision = decision.model_copy(update={"macro_state": macro_decision_context(macro)})
            except Exception:
                logger.exception("macro decision context failed for %s", symbol)
            decision = enforce_verdict_authority(decision)
        await self._publish_if_changed(decision)
        return MarketStateResponse(
            decision=decision,
            data=DataReport(
                provider=self._provider.name,
                is_synthetic=self._provider.is_synthetic,
                timeframe=self._timeframe,
                candle_count=candle_count,
                latest_closed_open_time=latest_closed,
                quality=decision.data_quality,
                market_status=market_status,
                position_size_status=instrument.position_size_status if instrument else None,
                issues=issues,
                provider_error=provider_error,
            ),
        )

    async def _publish_if_changed(self, decision: MasterDecision) -> None:
        fingerprint = (
            decision.verdict,
            decision.data_quality,
            tuple(decision.blockers),
            decision.htf_bias,
            decision.primary_dol,
            decision.risk_status,
            decision.news_state["state"] if decision.news_state else None,
        )
        if self._last.get(decision.symbol) == fingerprint:
            return
        self._last[decision.symbol] = fingerprint
        await self._bus.publish(
            DomainEvent(
                type=DECISION_CHANGED,
                symbol=decision.symbol,
                occurred_at=decision.updated_at,
                payload={
                    "verdict": decision.verdict.value,
                    "dataQuality": decision.data_quality.value,
                    "blockers": [b.value for b in decision.blockers],
                },
            )
        )
