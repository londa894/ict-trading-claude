"""Assistant service: deterministic facts always; an external narrative only if it passes the guard."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.contracts import strategy_version
from app.domain.enums import AssistantIntent, AssistantProvider, EducationLevel, GuardStatus
from app.domain.instrument import get_instrument
from app.services.analytics.service import AnalyticsService
from app.services.assistant import guard
from app.services.assistant.anthropic import AnthropicAssistant, AssistantProviderError
from app.services.assistant.deterministic import COMMANDS, explain
from app.services.assistant.glossary import define, find_term
from app.services.assistant.models import (
    AskRequest,
    AssistantAnswer,
    AssistantCapabilities,
    DecisionRef,
    GuardReport,
    ToolCallRecord,
    ToolInfo,
)
from app.services.assistant.tools import TOOLS, AssistantContext
from app.services.backtest.service import BacktestService
from app.services.candles.service import UnknownSymbolError
from app.services.journal.service import JournalService
from app.services.market_state.service import MarketStateService
from app.services.scanner.service import ScannerService
from app.services.scoring.service import EvaluationService
from app.services.structure.service import StructureService

logger = logging.getLogger("fmcc.assistant")
NOT_AUTHORIZED = "NOT_AUTHORIZED"


class AssistantService:
    def __init__(
        self,
        market_state: MarketStateService,
        evaluation: EvaluationService,
        structure: StructureService,
        scanner: ScannerService,
        external: AnthropicAssistant | None = None,
        share_account_data: bool = False,
        clock: Callable[[], datetime] | None = None,
        journal: JournalService | None = None,
        analytics: AnalyticsService | None = None,
        backtest: BacktestService | None = None,
    ) -> None:
        self._market_state = market_state
        self._evaluation = evaluation
        self._structure = structure
        self._scanner = scanner
        self._external = external
        self._share = share_account_data
        self._clock = clock or (lambda: datetime.now(UTC))
        self._journal = journal
        self._analytics = analytics
        self._backtest = backtest

    def capabilities(self) -> AssistantCapabilities:
        return AssistantCapabilities(
            provider=AssistantProvider.ANTHROPIC if self._external else AssistantProvider.DETERMINISTIC,
            model=self._external.model if self._external else None,
            external_ai_configured=self._external is not None,
            shares_account_data=self._share,
            commands=COMMANDS,
            levels=list(EducationLevel),
            tools=[
                ToolInfo(name=t.name, description=t.description, available=t.available, detail=t.detail)
                for t in TOOLS
            ],
            authority=NOT_AUTHORIZED,
        )

    async def ask(self, request: AskRequest) -> AssistantAnswer:
        instrument = get_instrument(request.symbol)
        if instrument is None:
            raise UnknownSymbolError(request.symbol)
        compare = []
        for s in request.compare_symbols:
            if get_instrument(s) is None:
                raise UnknownSymbolError(s)
            compare.append(s.upper())
        ctx = AssistantContext(
            instrument.symbol,
            self._market_state,
            self._evaluation,
            self._structure,
            self._scanner,
            self._share,
            self._journal,
            self._analytics,
            self._backtest,
        )
        intent, draft = await explain(ctx, request.question, request.level, compare)
        decision = (await ctx.market()).decision

        answer = " ".join(draft.answer)
        unknowns = list(draft.unknowns)
        provider = AssistantProvider.DETERMINISTIC
        report = GuardReport(status=GuardStatus.NOT_APPLICABLE, violations=[])
        if self._external is not None and intent is not AssistantIntent.HELP:
            try:
                model_answer = await self._external.answer(ctx, request.question, request.level)
                term = find_term(request.question)
                corpus = [*ctx.payloads, *(f.value for f in draft.facts)]
                if term:
                    corpus.append(define(term, request.level))
                scanned = {
                    str(r["symbol"])
                    for p in ctx.payloads
                    if isinstance(p, dict)
                    for r in p.get("rows", [])
                    if isinstance(r, dict)
                }
                violations = guard.check(
                    model_answer.answer,
                    model_answer.verdict,
                    decision.verdict.value,
                    corpus,
                    instrument.symbol,
                    scanned,
                )
                if violations:
                    report = GuardReport(status=GuardStatus.FALLBACK, violations=violations)
                else:
                    answer = model_answer.answer
                    unknowns = list(dict.fromkeys([*unknowns, *model_answer.unknowns]))
                    provider = AssistantProvider.ANTHROPIC
                    report = GuardReport(status=GuardStatus.PASSED, violations=[])
            except AssistantProviderError as exc:
                report = GuardReport(
                    status=GuardStatus.FALLBACK, violations=[f"external model unavailable: {exc}"]
                )
            except Exception as exc:
                logger.exception("assistant external model failed")
                report = GuardReport(
                    status=GuardStatus.FALLBACK, violations=[f"external model failed: {type(exc).__name__}"]
                )

        seen: set[tuple[str, str | None]] = set()
        tools: list[ToolCallRecord] = []
        for name, symbol, status, detail in ctx.calls:
            if (name, symbol) in seen:
                continue
            seen.add((name, symbol))
            tools.append(ToolCallRecord(name=name, symbol=symbol, status=status, detail=detail))
        return AssistantAnswer(
            symbol=instrument.symbol,
            question=request.question,
            intent=intent,
            level=request.level,
            answer=answer,
            facts=draft.facts,
            unknowns=unknowns,
            tools=tools,
            decision=DecisionRef(
                symbol=decision.symbol,
                verdict=decision.verdict,
                data_quality=decision.data_quality,
                blockers=decision.blockers,
                strategy_version=decision.strategy_version,
                updated_at=decision.updated_at,
            ),
            proposal=draft.proposal,
            provider=provider,
            model=self._external.model if self._external else None,
            guard=report,
            authority=NOT_AUTHORIZED,
            strategy_version=strategy_version(),
            generated_at=self._clock(),
        )
