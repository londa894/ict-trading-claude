from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import AppState
from app.api.routes import router
from app.config import Settings, get_settings
from app.contracts import strategy_version
from app.db.session import make_engine
from app.domain.enums import Blocker, Verdict
from app.providers.base import MarketDataProvider
from app.providers.registry import default_registry
from app.services.alerts.service import AlertService
from app.services.analytics.service import AnalyticsService
from app.services.assistant.anthropic import AnthropicAssistant
from app.services.assistant.service import AssistantService
from app.services.backtest.service import BacktestService
from app.services.backtest.store import BacktestStore, DatabaseBacktestStore, UnconfiguredBacktestStore
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.journal.service import JournalService
from app.services.journal.store import DatabaseJournalStore, JournalStore, UnconfiguredJournalStore
from app.services.liquidity.service import LiquidityService
from app.services.macro.providers import create_macro
from app.services.macro.service import MacroService
from app.services.market_state.service import MarketStateService
from app.services.news.providers import create_calendar
from app.services.news.service import NewsService
from app.services.no_wick.service import NoWickService
from app.services.paper.service import PaperService
from app.services.paper.store import DatabasePaperStore, PaperStore, UnconfiguredPaperStore
from app.services.pd_arrays.service import PdArrayService
from app.services.replay.service import ReplayService
from app.services.risk.service import RiskService
from app.services.risk.store import RiskProfileStore
from app.services.scanner.service import ScannerService
from app.services.scoring.service import EvaluationService
from app.services.sessions.service import SessionService
from app.services.setup_state.service import SetupService
from app.services.structure.service import StructureService

logger = logging.getLogger("fmcc")


def create_app(
    settings: Settings | None = None,
    provider: MarketDataProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    provider = provider or default_registry(
        tradelocker_server=settings.tradelocker_server,
        tradelocker_email=settings.tradelocker_email.get_secret_value() if settings.tradelocker_email else "",
        tradelocker_password=settings.tradelocker_password.get_secret_value() if settings.tradelocker_password else "",
        tradelocker_account_server=settings.tradelocker_account_server,
        data_root=settings.data_root,
    ).create(settings.market_data_provider)
    bus = InMemoryEventBus()
    candles = CandleService(provider, clock)
    structure = StructureService(candles)
    liquidity = LiquidityService(candles)
    pd_arrays = PdArrayService(candles)
    no_wick = NoWickService(candles)
    sessions = SessionService(candles)
    setups = SetupService(candles, sessions)
    risk = RiskService(RiskProfileStore(settings.risk_profile_path or None))
    news = NewsService(create_calendar(settings.calendar_provider, settings.calendar_file_path), clock)
    macro = MacroService(create_macro(settings.macro_provider, settings.macro_file_path), candles, clock)
    evaluation = EvaluationService(setups, structure, liquidity, risk=risk, news=news, macro=macro)
    service = MarketStateService(
        provider,
        bus,
        settings.execution_timeframe,
        clock,
        structure,
        liquidity,
        pd_arrays,
        no_wick,
        sessions,
        evaluation=evaluation,
        news=news,
        macro=macro,
    )

    journal_store: JournalStore = (
        DatabaseJournalStore(make_engine(settings.database_url))
        if settings.journal_store == "database"
        else UnconfiguredJournalStore()
    )
    journal = JournalService(journal_store, service, evaluation, candles, clock)
    paper_store: PaperStore = (
        DatabasePaperStore(make_engine(settings.database_url))
        if settings.paper_store == "database"
        else UnconfiguredPaperStore()
    )
    paper = PaperService(
        paper_store,
        service,
        evaluation,
        candles,
        clock,
        monitor_enabled=settings.paper_monitor_enabled and settings.paper_store == "database",
    )

    analytics = AnalyticsService(journal, paper, clock)
    backtest_store: BacktestStore = (
        DatabaseBacktestStore(make_engine(settings.database_url))
        if settings.backtest_store == "database"
        else UnconfiguredBacktestStore()
    )
    backtest = BacktestService(backtest_store, candles, clock)
    replay = ReplayService(candles, setups, sessions, clock)

    alerts = AlertService(candles, service, evaluation, clock, enabled=settings.alert_monitor_enabled)
    scanner = ScannerService(service, clock)
    external = None
    if settings.ai_provider == "anthropic":
        if settings.anthropic_api_key is None:
            logger.warning(
                "AI_PROVIDER=anthropic without ANTHROPIC_API_KEY: using the deterministic assistant"
            )
        else:
            external = AnthropicAssistant(
                settings.anthropic_api_key.get_secret_value(), settings.ai_model, settings.ai_timeout_seconds
            )
    assistant = AssistantService(
        service,
        evaluation,
        structure,
        scanner,
        external,
        settings.ai_share_account_data,
        clock,
        journal,
        analytics,
        backtest,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks = []
        if settings.alert_monitor_enabled:
            tasks.append(asyncio.create_task(alerts.run_forever()))
        if settings.paper_monitor_enabled and settings.paper_store == "database":
            tasks.append(asyncio.create_task(paper.run_forever()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        lifespan=lifespan,
        title="Forex & Metals ICT/SMC Command Center API",
        version=strategy_version(),
        description="Broker-free decision support. No order execution.",
    )
    app.state.fmcc = AppState(
        settings=settings,
        provider=provider,
        market_state=service,
        candles=candles,
        structure=structure,
        liquidity=liquidity,
        pd_arrays=pd_arrays,
        no_wick=no_wick,
        sessions=sessions,
        setups=setups,
        evaluation=evaluation,
        risk=risk,
        scanner=scanner,
        alerts=alerts,
        assistant=assistant,
        news=news,
        macro=macro,
        journal=journal,
        paper=paper,
        analytics=analytics,
        backtest=backtest,
        replay=replay,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "DELETE"],  # POST/DELETE: risk calculator and ready watches only
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field paths and messages only: submitted values (balances, P/L) are never echoed back or logged.
        detail = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(Exception)
    async def fail_safe_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals; never fail open.
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "verdict": Verdict.UNAVAILABLE.value,
                "blockers": [Blocker.SYSTEM_INTEGRITY_FAILURE.value],
                "error": "INTERNAL_ERROR",
            },
        )

    return app


app = create_app()
