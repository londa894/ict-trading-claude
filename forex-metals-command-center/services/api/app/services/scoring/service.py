"""Evaluation service: scoring, confidence and the not-authorized verdict evaluation for one symbol."""

from __future__ import annotations

import logging
from datetime import datetime

from app.domain.enums import AnalysisIneligibility, HtfBias, LiquiditySide, SetupState
from app.domain.instrument import get_instrument
from app.services.candles.service import UnknownSymbolError
from app.services.liquidity.service import LiquidityService
from app.services.macro.service import MacroService
from app.services.news.service import NewsService
from app.services.risk.models import TradeInput
from app.services.risk.service import RiskService
from app.services.scoring.engine import EvaluationInputs, evaluate
from app.services.scoring.models import DecisionEvaluation, ScoringConfig
from app.services.setup_state.service import SetupRun, SetupService
from app.services.structure.service import StructureService

logger = logging.getLogger("fmcc.scoring")


def dol_side(primary_dol: str | None) -> LiquiditySide | None:
    """Side token of a decision DOL description ("H1 BSL PDH ... @ price")."""
    if not primary_dol:
        return None
    parts = primary_dol.split()
    return LiquiditySide(parts[1]) if len(parts) > 1 and parts[1] in LiquiditySide.__members__ else None


class EvaluationService:
    def __init__(
        self,
        setups: SetupService,
        structure: StructureService | None = None,
        liquidity: LiquidityService | None = None,
        cfg: ScoringConfig | None = None,
        risk: RiskService | None = None,
        news: NewsService | None = None,
        macro: MacroService | None = None,
    ) -> None:
        self._setups = setups
        self._structure = structure
        self._liquidity = liquidity
        self._cfg = cfg or ScoringConfig.from_spec()
        self._risk = risk
        self._news = news
        self._macro = macro

    async def evaluate(
        self,
        symbol: str,
        now: datetime | None = None,
        *,
        htf_bias: HtfBias | None = None,
        primary_dol: str | None = None,
        resolve_context: bool = True,
    ) -> DecisionEvaluation:
        ev, _run = await self.evaluate_with_run(
            symbol, now, htf_bias=htf_bias, primary_dol=primary_dol, resolve_context=resolve_context
        )
        return ev

    async def evaluate_with_run(
        self,
        symbol: str,
        now: datetime | None = None,
        *,
        htf_bias: HtfBias | None = None,
        primary_dol: str | None = None,
        resolve_context: bool = True,
    ) -> tuple[DecisionEvaluation, SetupRun]:
        """The evaluation plus the setup run it was computed from (reused by the alert monitor)."""
        instrument = get_instrument(symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(symbol)
        run = await self._setups.run(symbol, now)
        if resolve_context and htf_bias is None and self._structure is not None:
            htf_bias = (await self._structure.decision_context(symbol, now)).htf_bias
        if resolve_context and primary_dol is None and self._liquidity is not None:
            primary_dol = (await self._liquidity.decision_context(symbol, now)).primary_dol
        known_bias = htf_bias if htf_bias not in (None, HtfBias.UNKNOWN) else None
        pipeline = run.pipeline
        news = await self._news.assess(instrument.symbol, run.analysis.generated_at) if self._news else None
        current_setup = run.analysis.current
        macro = None
        if self._macro is not None:
            macro = await self._macro.assess(
                instrument.symbol,
                run.analysis.generated_at,
                # A setup from data that cannot be decided on is not a direction to compare macro with.
                direction=(
                    current_setup.direction
                    if current_setup is not None and run.analysis.eligible_for_decision
                    else None
                ),
                news=news,
            )
        risk = None
        user_spec = False
        if self._risk is not None:
            current = run.analysis.current
            plan = current.entry_plan if current is not None and current.state is SetupState.BLOCKED else None
            trade = TradeInput(direction=plan.direction, entry=plan.entry, stop=plan.stop) if plan else None
            same_tf = run.analysis.timeframe is self._risk.cfg.volatility_timeframe
            risk = self._risk.assess(
                instrument.symbol,
                run.analysis.generated_at,
                trade,
                run.candles if same_tf else None,
                news_state=news.state if news else None,
            )
            user_spec = self._risk.user_spec(instrument.symbol) is not None
        inputs = EvaluationInputs(
            setups=run.analysis,
            no_wick_events=pipeline.no_wick.events if pipeline else [],
            pd_zones=pipeline.pd_arrays.zones if pipeline else [],
            time_quality=run.session.clock.time_quality,
            expansion=run.session.adr.expansion if run.session.adr else None,
            htf_bias=known_bias,
            primary_dol_side=dol_side(primary_dol),
            instrument_spec_missing=instrument.spec is None and not user_spec,
            weak_fvg_size_atr=self._setups.entry_cfg.weak_fvg_size_atr,
            now=run.analysis.generated_at,
            risk=risk,
            news=news,
            macro=macro,
        )
        try:
            return evaluate(inputs, self._cfg), run
        except Exception:
            logger.exception("evaluation failed for %s", symbol)
            failed = run.analysis.model_copy(
                update={
                    "eligible_for_decision": False,
                    "ineligibility": [*run.analysis.ineligibility, AnalysisIneligibility.EVALUATION_FAILED],
                }
            )
            return (
                evaluate(EvaluationInputs(**{**inputs.__dict__, "setups": failed}), self._cfg),
                run,
            )
