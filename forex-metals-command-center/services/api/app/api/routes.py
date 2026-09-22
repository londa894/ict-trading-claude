"""HTTP API. There are deliberately no order, broker, account-write or execution endpoints.

Writes are limited to the stateless what-if risk calculator (stores nothing), ALERT_ME_WHEN_READY watches
(symbol + direction, in memory) and assistant questions (answered from engine state, stored nowhere)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from app.api.deps import AppState, get_state
from app.contracts import load_spec, strategy_version, verdict_authority
from app.domain.base import ApiModel
from app.domain.enums import (
    AlertCategory,
    AlertPriority,
    AnalyticsSource,
    Direction,
    EventImportance,
    JournalEntryKind,
    PaperStatus,
    PositionSizeStatus,
    Timeframe,
)
from app.domain.instrument import Instrument, instrument_registry
from app.providers.base import ProviderHealth
from app.services.alerts.models import AlertFeed, ReadyWatch, ReadyWatchRequest
from app.services.alerts.service import ReadyWatchLimitError
from app.services.analytics.models import AnalyticsReport
from app.services.assistant.models import AskRequest, AssistantAnswer, AssistantCapabilities
from app.services.backtest.models import BacktestListResponse, BacktestRequest, BacktestRun, BacktestStoreInfo
from app.services.backtest.service import BacktestNotFoundError, BacktestRequestError
from app.services.backtest.store import BacktestUnavailableError
from app.services.candles.service import (
    CHART_TIMEFRAMES,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ChartSeriesResponse,
    UnknownSymbolError,
)
from app.services.journal.models import (
    CreateJournalEntryRequest,
    JournalEntry,
    JournalExport,
    JournalListResponse,
    JournalStoreInfo,
    RecordOutcomeRequest,
)
from app.services.journal.service import JournalEntryNotFoundError, JournalRequestError
from app.services.journal.store import JournalUnavailableError
from app.services.liquidity.models import LiquidityAnalysis
from app.services.macro.models import MacroAssessment, MacroSeriesResponse
from app.services.market_state.service import MarketStateResponse
from app.services.news.models import CalendarResponse, NewsAssessment
from app.services.no_wick.models import NoWickAnalysis
from app.services.paper.models import CreatePaperSimRequest, PaperListResponse, PaperSim, PaperStoreInfo
from app.services.paper.service import PaperRequestError, PaperSimNotFoundError
from app.services.paper.store import PaperUnavailableError
from app.services.pd_arrays.models import PdArrayAnalysis
from app.services.replay.models import (
    CreateReplayRequest,
    QuizAnswerRequest,
    ReplayReveal,
    ReplayState,
    ReplayStatus,
    StepRequest,
)
from app.services.replay.service import ReplayNotFoundError, ReplayRequestError
from app.services.risk.models import RiskAssessment, RiskCalculationRequest
from app.services.scanner.models import MarketRow, ScanResponse
from app.services.scanner.service import markets as market_rows
from app.services.scoring.models import DecisionEvaluation
from app.services.sessions.models import SessionAnalysis
from app.services.setup_state.models import SetupAnalysis
from app.services.structure.models import MtfStructureResponse, StructureAnalysis

router = APIRouter()
State = Annotated[AppState, Depends(get_state)]
Symbol = Annotated[str, Path(pattern=r"^[A-Za-z]{3,12}$")]


class HealthResponse(ApiModel):
    status: str
    service: str
    strategy_version: str
    time: datetime


class ProviderSummary(ApiModel):
    name: str
    is_synthetic: bool
    credentials_configured: bool


class SystemStatus(ApiModel):
    strategy_version: str
    phase: int
    phase_name: str
    verdict_authority: str
    enabled_engines: list[str]
    environment: str
    provider: ProviderSummary
    broker_connection: str
    order_execution: str
    market_data_source: str
    chart_renderer: str
    primary_market: str


class InstrumentOut(ApiModel):
    instrument: Instrument
    position_size_status: PositionSizeStatus


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok", service="fmcc-api", strategy_version=strategy_version(), time=datetime.now(UTC)
    )


@router.get("/api/v1/system/status", response_model=SystemStatus, tags=["system"])
async def system_status(state: State) -> SystemStatus:
    spec = load_spec("strategy_version")
    return SystemStatus(
        strategy_version=spec["strategyVersion"],
        phase=spec["phase"],
        phase_name=spec["phaseName"],
        verdict_authority=verdict_authority(),
        enabled_engines=spec["enabledEngines"],
        environment=state.settings.app_env,
        provider=ProviderSummary(
            name=state.provider.name,
            is_synthetic=state.provider.is_synthetic,
            credentials_configured=state.settings.market_data_api_key is not None,
        ),
        broker_connection="NONE",
        order_execution="NONE",
        market_data_source="INDEPENDENT_PROVIDER",
        chart_renderer="TRADINGVIEW_LIGHTWEIGHT_CHARTS_RENDER_ONLY",
        primary_market="XAUUSD",
    )


@router.get("/api/v1/instruments", response_model=list[InstrumentOut], tags=["instruments"])
async def instruments() -> list[InstrumentOut]:
    ordered = sorted(instrument_registry().values(), key=lambda i: (i.priority, i.symbol))
    return [InstrumentOut(instrument=i, position_size_status=i.position_size_status) for i in ordered]


@router.get("/api/v1/providers/health", response_model=ProviderHealth, tags=["providers"])
async def provider_health(state: State) -> ProviderHealth:
    return await state.provider.health_check()


@router.get("/api/v1/market-state/{symbol}", response_model=MarketStateResponse, tags=["market-state"])
async def market_state(symbol: Symbol, state: State) -> MarketStateResponse:
    return await state.market_state.evaluate(symbol)


@router.get("/api/v1/candles/{symbol}", response_model=ChartSeriesResponse, tags=["candles"])
async def candles(
    symbol: Symbol,
    state: State,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.M5,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> ChartSeriesResponse:
    if timeframe not in CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {[t.value for t in CHART_TIMEFRAMES]}",
        )
    try:
        return await state.candles.chart_series(symbol, timeframe, limit)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/structure/{symbol}", response_model=StructureAnalysis, tags=["structure"])
async def structure(
    symbol: Symbol,
    state: State,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.M15,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> StructureAnalysis:
    if timeframe not in CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {[t.value for t in CHART_TIMEFRAMES]}",
        )
    try:
        return await state.structure.analyze(symbol, timeframe, limit)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/structure/{symbol}/alignment", response_model=MtfStructureResponse, tags=["structure"])
async def structure_alignment(symbol: Symbol, state: State) -> MtfStructureResponse:
    try:
        return await state.structure.alignment(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/liquidity/{symbol}", response_model=LiquidityAnalysis, tags=["liquidity"])
async def liquidity(
    symbol: Symbol,
    state: State,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.H1,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> LiquidityAnalysis:
    if timeframe not in CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {[t.value for t in CHART_TIMEFRAMES]}",
        )
    try:
        return await state.liquidity.analyze(symbol, timeframe, limit)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/pd-arrays/{symbol}", response_model=PdArrayAnalysis, tags=["pd-arrays"])
async def pd_arrays(
    symbol: Symbol,
    state: State,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.M15,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> PdArrayAnalysis:
    if timeframe not in CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {[t.value for t in CHART_TIMEFRAMES]}",
        )
    try:
        return await state.pd_arrays.analyze(symbol, timeframe, limit)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/no-wick/{symbol}", response_model=NoWickAnalysis, tags=["no-wick"])
async def no_wick(
    symbol: Symbol,
    state: State,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.M15,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> NoWickAnalysis:
    if timeframe not in CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {[t.value for t in CHART_TIMEFRAMES]}",
        )
    try:
        return await state.no_wick.analyze(symbol, timeframe, limit)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/no-wick/{symbol}/htf", tags=["no-wick"])
async def no_wick_htf(symbol: Symbol, state: State) -> dict[str, object]:
    """No-wick context across the tracked timeframes (D1/H4/H1/M15). Read-only; never a trade authorization."""
    try:
        return await state.no_wick.htf_context(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/sessions/{symbol}", response_model=SessionAnalysis, tags=["sessions"])
async def sessions(symbol: Symbol, state: State) -> SessionAnalysis:
    try:
        return await state.sessions.analyze(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/setups/{symbol}", response_model=SetupAnalysis, tags=["setups"])
async def setups(symbol: Symbol, state: State) -> SetupAnalysis:
    try:
        return await state.setups.analyze(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/evaluation/{symbol}", response_model=DecisionEvaluation, tags=["evaluation"])
async def evaluation(symbol: Symbol, state: State) -> DecisionEvaluation:
    try:
        return await state.evaluation.evaluate(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/risk/{symbol}", response_model=RiskAssessment, tags=["risk"])
async def risk(symbol: Symbol, state: State) -> RiskAssessment:
    """The server-side account profile applied to the symbol and its confirmed plan (if any)."""
    try:
        ev = await state.evaluation.evaluate(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc
    if ev.risk is None:
        raise HTTPException(status_code=503, detail="risk gate unavailable")
    return ev.risk


@router.post("/api/v1/risk/calculate", response_model=RiskAssessment, tags=["risk"])
async def risk_calculate(request: RiskCalculationRequest, state: State) -> RiskAssessment:
    """What-if position sizing from the request values only. Nothing is stored; volatility is not checked."""
    try:
        return state.risk.calculate(request, state.candles.now())
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/markets", response_model=list[MarketRow], tags=["scanner"])
async def markets(state: State) -> list[MarketRow]:
    """Instrument catalog with validation status, market hours status now and contract-spec status."""
    return market_rows(state.candles.now())


@router.get("/api/v1/scanner", response_model=ScanResponse, tags=["scanner"])
async def scanner(
    state: State,
    symbols: Annotated[
        str | None, Query(max_length=200, pattern=r"^[A-Za-z]{3,12}(,[A-Za-z]{3,12})*$")
    ] = None,
    min_score: Annotated[float | None, Query(alias="minScore", ge=0, le=100)] = None,
    only_setups: Annotated[bool, Query(alias="onlySetups")] = False,
) -> ScanResponse:
    """Each symbol's Master Decision, isolated and ranked for attention. Never a trade signal."""
    try:
        return await state.scanner.scan(
            symbols.split(",") if symbols else None, min_score=min_score, only_setups=only_setups
        )
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/alerts", response_model=AlertFeed, tags=["alerts"])
async def alerts(
    state: State,
    since: Annotated[int, Query(ge=0)] = 0,
    symbol: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3,12}$")] = None,
    category: Annotated[AlertCategory | None, Query()] = None,
    min_priority: Annotated[AlertPriority | None, Query(alias="minPriority")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AlertFeed:
    """Alert feed, newest first. Alerts describe engine state changes; they are never trade instructions."""
    return state.alerts.feed(
        since=since,
        symbol=symbol,
        category=category.value if category else None,
        min_priority=min_priority,
        limit=limit,
    )


@router.get("/api/v1/alerts/ready-watches", response_model=list[ReadyWatch], tags=["alerts"])
async def ready_watches(state: State) -> list[ReadyWatch]:
    return state.alerts.watches()


@router.post("/api/v1/alerts/ready-watches", response_model=ReadyWatch, status_code=201, tags=["alerts"])
async def add_ready_watch(request: ReadyWatchRequest, state: State) -> ReadyWatch:
    """ALERT_ME_WHEN_READY: fires once, only on a LONG/SHORT verdict under FULL verdict authority."""
    try:
        return state.alerts.add_watch(request.symbol, request.direction)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc
    except ReadyWatchLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/v1/alerts/ready-watches/{watch_id}", status_code=204, tags=["alerts"])
async def remove_ready_watch(
    watch_id: Annotated[str, Path(pattern=r"^WATCH:[0-9a-f]{12}$")], state: State
) -> Response:
    if not state.alerts.remove_watch(watch_id):
        raise HTTPException(status_code=404, detail="unknown watch")
    return Response(status_code=204)


@router.get("/api/v1/assistant/capabilities", response_model=AssistantCapabilities, tags=["assistant"])
async def assistant_capabilities(state: State) -> AssistantCapabilities:
    return state.assistant.capabilities()


@router.post("/api/v1/assistant/ask", response_model=AssistantAnswer, tags=["assistant"])
async def assistant_ask(request: AskRequest, state: State) -> AssistantAnswer:
    """Explains the deterministic engine state. Never creates prices, verdicts or trade instructions."""
    try:
        return await state.assistant.ask(request)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/macro/series", response_model=MacroSeriesResponse, tags=["macro"])
async def macro_series(state: State) -> MacroSeriesResponse:
    """The macro series as supplied by the configured provider (synthetic when MACRO_PROVIDER=fixture)."""
    return await state.macro.series()


@router.get("/api/v1/macro/{symbol}", response_model=MacroAssessment, tags=["macro"])
async def macro(
    symbol: Symbol,
    state: State,
    direction: Annotated[Direction | None, Query()] = None,
) -> MacroAssessment:
    """Macro context: bias for the market and, with a direction, the state versus it. Never blocks."""
    try:
        news = await state.news.assess(symbol)
        return await state.macro.assess(symbol, direction=direction, news=news)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/news/{symbol}", response_model=NewsAssessment, tags=["news"])
async def news(symbol: Symbol, state: State) -> NewsAssessment:
    """The news gate: relevant events and CLEAR / CAUTION / BLACKOUT / POST_NEWS_WAIT / NORMALIZED."""
    try:
        return await state.news.assess(symbol)
    except UnknownSymbolError as exc:
        raise HTTPException(status_code=404, detail="unknown symbol") from exc


@router.get("/api/v1/calendar", response_model=CalendarResponse, tags=["news"])
async def calendar(
    state: State,
    hours_before: Annotated[float, Query(alias="hoursBefore", ge=0, le=168)] = 6,
    hours_after: Annotated[float, Query(alias="hoursAfter", ge=0, le=336)] = 48,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    min_importance: Annotated[EventImportance, Query(alias="minImportance")] = EventImportance.LOW,
) -> CalendarResponse:
    return await state.news.calendar(
        hours_before=hours_before, hours_after=hours_after, currency=currency, min_importance=min_importance
    )


# --- Phase 15: journal (the user private manual records; nothing is placed or authorized) -----------

EntryId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]


def _journal_error(exc: Exception) -> HTTPException:
    if isinstance(exc, JournalUnavailableError):
        return HTTPException(status_code=503, detail=f"journal unavailable: {exc}")
    if isinstance(exc, JournalEntryNotFoundError | UnknownSymbolError):
        return HTTPException(status_code=404, detail="not found")
    return HTTPException(status_code=422, detail=str(exc))  # JournalRequestError / cursor: safe messages only


@router.get("/api/v1/journal/status", response_model=JournalStoreInfo, tags=["journal"])
async def journal_status(state: State) -> JournalStoreInfo:
    return await state.journal.info()


@router.get("/api/v1/journal/entries", response_model=JournalListResponse, tags=["journal"])
async def journal_entries(
    state: State,
    symbol: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3,12}$")] = None,
    kind: JournalEntryKind | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> JournalListResponse:
    """Newest first. Entries are private manual records; filters by symbol, kind and creation time."""
    try:
        return await state.journal.list_entries(
            symbol=symbol, kind=kind, start=start, end=end, limit=limit, cursor=cursor
        )
    except (JournalUnavailableError, ValueError) as exc:
        raise _journal_error(exc) from exc


@router.post("/api/v1/journal/entries", response_model=JournalEntry, status_code=201, tags=["journal"])
async def journal_create(request: CreateJournalEntryRequest, state: State) -> JournalEntry:
    """Records a decision or the user's own manual trade with an immutable engine snapshot captured now."""
    try:
        return await state.journal.create(request)
    except (JournalUnavailableError, JournalRequestError, UnknownSymbolError) as exc:
        raise _journal_error(exc) from exc


@router.get("/api/v1/journal/export", response_model=JournalExport, tags=["journal"])
async def journal_export(state: State) -> JournalExport:
    try:
        return await state.journal.export()
    except JournalUnavailableError as exc:
        raise _journal_error(exc) from exc


@router.get("/api/v1/journal/entries/{entry_id}", response_model=JournalEntry, tags=["journal"])
async def journal_entry(entry_id: EntryId, state: State) -> JournalEntry:
    try:
        return await state.journal.get(entry_id)
    except (JournalUnavailableError, JournalEntryNotFoundError) as exc:
        raise _journal_error(exc) from exc


@router.post(
    "/api/v1/journal/entries/{entry_id}/outcomes",
    response_model=JournalEntry,
    status_code=201,
    tags=["journal"],
)
async def journal_outcome(entry_id: EntryId, request: RecordOutcomeRequest, state: State) -> JournalEntry:
    """Appends an outcome revision (earlier revisions are kept)."""
    try:
        return await state.journal.record_outcome(entry_id, request)
    except (JournalUnavailableError, JournalEntryNotFoundError, JournalRequestError) as exc:
        raise _journal_error(exc) from exc


@router.delete("/api/v1/journal/entries/{entry_id}", status_code=204, tags=["journal"])
async def journal_delete(entry_id: EntryId, state: State) -> Response:
    """Permanently deletes the user's own entry and its outcomes (privacy: user-controlled deletion)."""
    try:
        await state.journal.delete(entry_id)
    except (JournalUnavailableError, JournalEntryNotFoundError) as exc:
        raise _journal_error(exc) from exc
    return Response(status_code=204)


# --- Phase 16: paper trading (simulation only; nothing is sent anywhere) --------------------

SimId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]


def _paper_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PaperUnavailableError):
        return HTTPException(status_code=503, detail=f"paper trading unavailable: {exc}")
    if isinstance(exc, PaperSimNotFoundError | UnknownSymbolError):
        return HTTPException(status_code=404, detail="not found")
    return HTTPException(status_code=422, detail=str(exc))


PAPER_ERRORS = (
    PaperUnavailableError,
    PaperSimNotFoundError,
    PaperRequestError,
    UnknownSymbolError,
    ValueError,
)


@router.get("/api/v1/paper/status", response_model=PaperStoreInfo, tags=["paper"])
async def paper_status(state: State) -> PaperStoreInfo:
    return await state.paper.info()


@router.get("/api/v1/paper/sims", response_model=PaperListResponse, tags=["paper"])
async def paper_sims(
    state: State,
    symbol: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3,12}$")] = None,
    status: PaperStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> PaperListResponse:
    """Newest first; pending/open sims are advanced over newly closed bars before listing."""
    try:
        return await state.paper.list_sims(symbol=symbol, status=status, limit=limit, cursor=cursor)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc


@router.post("/api/v1/paper/sims", response_model=PaperSim, status_code=201, tags=["paper"])
async def paper_create(request: CreatePaperSimRequest, state: State) -> PaperSim:
    """Starts a simulated (paper) position on the independent market data. Never an authorization."""
    try:
        return await state.paper.create(request)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc


@router.get("/api/v1/paper/sims/{sim_id}", response_model=PaperSim, tags=["paper"])
async def paper_sim(sim_id: SimId, state: State) -> PaperSim:
    try:
        return await state.paper.get(sim_id)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc


@router.post("/api/v1/paper/sims/{sim_id}/close", response_model=PaperSim, tags=["paper"])
async def paper_close(sim_id: SimId, state: State) -> PaperSim:
    """Closes an OPEN sim at the last closed bar (assumed spread + slippage)."""
    try:
        return await state.paper.close_now(sim_id)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc


@router.post("/api/v1/paper/sims/{sim_id}/cancel", response_model=PaperSim, tags=["paper"])
async def paper_cancel(sim_id: SimId, state: State) -> PaperSim:
    try:
        return await state.paper.cancel(sim_id)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc


@router.delete("/api/v1/paper/sims/{sim_id}", status_code=204, tags=["paper"])
async def paper_delete(sim_id: SimId, state: State) -> Response:
    try:
        await state.paper.delete(sim_id)
    except PAPER_ERRORS as exc:
        raise _paper_error(exc) from exc
    return Response(status_code=204)


# --- Phase 17: analytics (descriptive statistics of recorded history; never a probability or signal) -----


@router.get("/api/v1/analytics", response_model=AnalyticsReport, tags=["analytics"])
async def analytics(
    state: State,
    source: AnalyticsSource = AnalyticsSource.JOURNAL,
    symbol: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3,12}$")] = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    include_synthetic: Annotated[bool, Query(alias="includeSynthetic")] = False,
    strategy_version_filter: Annotated[str | None, Query(alias="strategyVersion", max_length=40)] = None,
) -> AnalyticsReport:
    """Statistics of verified closed journal trades or paper sims (never mixed). Labelled by sample size."""
    return await state.analytics.report(
        source,
        symbol=symbol,
        start=start,
        end=end,
        include_synthetic=include_synthetic,
        strategy_version_filter=strategy_version_filter,
    )


# --- Phase 18: backtesting (research replays of the live setup engine; never an authorization) -----------

RunId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
BACKTEST_ERRORS = (BacktestUnavailableError, BacktestNotFoundError, BacktestRequestError, UnknownSymbolError)


def _backtest_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BacktestUnavailableError):
        return HTTPException(status_code=503, detail=f"backtesting unavailable: {exc}")
    if isinstance(exc, BacktestNotFoundError | UnknownSymbolError):
        return HTTPException(status_code=404, detail="not found")
    if isinstance(exc, BacktestRequestError) and "already running" in str(exc):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/backtests/status", response_model=BacktestStoreInfo, tags=["backtest"])
async def backtest_status(state: State) -> BacktestStoreInfo:
    return await state.backtest.info()


@router.get("/api/v1/backtests", response_model=BacktestListResponse, tags=["backtest"])
async def backtests(state: State, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> BacktestListResponse:
    return await state.backtest.list_runs(limit)


@router.post("/api/v1/backtests", response_model=BacktestRun, status_code=202, tags=["backtest"])
async def backtest_start(request: BacktestRequest, state: State) -> BacktestRun:
    """Queues a research replay of the live setup engine over closed history (runs in the background)."""
    try:
        return await state.backtest.start(request)
    except BACKTEST_ERRORS as exc:
        raise _backtest_error(exc) from exc


@router.get("/api/v1/backtests/{run_id}", response_model=BacktestRun, tags=["backtest"])
async def backtest_run(run_id: RunId, state: State) -> BacktestRun:
    try:
        return await state.backtest.get(run_id)
    except BACKTEST_ERRORS as exc:
        raise _backtest_error(exc) from exc


@router.post("/api/v1/backtests/{run_id}/cancel", response_model=BacktestRun, tags=["backtest"])
async def backtest_cancel(run_id: RunId, state: State) -> BacktestRun:
    try:
        return await state.backtest.cancel(run_id)
    except BACKTEST_ERRORS as exc:
        raise _backtest_error(exc) from exc


@router.delete("/api/v1/backtests/{run_id}", status_code=204, tags=["backtest"])
async def backtest_delete(run_id: RunId, state: State) -> Response:
    try:
        await state.backtest.delete(run_id)
    except BACKTEST_ERRORS as exc:
        raise _backtest_error(exc) from exc
    return Response(status_code=204)


# --- Phase 19: replay (education only; every response is computed as of the session cursor) --------------

SessionId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
REPLAY_ERRORS = (ReplayNotFoundError, ReplayRequestError, UnknownSymbolError)


def _replay_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReplayNotFoundError | UnknownSymbolError):
        return HTTPException(status_code=404, detail="not found")
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/replay/sessions", response_model=ReplayStatus, tags=["replay"])
async def replay_sessions(state: State) -> ReplayStatus:
    return await state.replay.status()


@router.post("/api/v1/replay/sessions", response_model=ReplayState, status_code=201, tags=["replay"])
async def replay_create(request: CreateReplayRequest, state: State) -> ReplayState:
    """Starts a MANUAL / GUIDED / BLIND / QUIZ replay at a past time; nothing after the cursor is shown."""
    try:
        return await state.replay.create(request)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc


@router.get("/api/v1/replay/sessions/{session_id}", response_model=ReplayState, tags=["replay"])
async def replay_state(session_id: SessionId, state: State) -> ReplayState:
    try:
        return await state.replay.state(session_id)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc


@router.post("/api/v1/replay/sessions/{session_id}/step", response_model=ReplayState, tags=["replay"])
async def replay_step(session_id: SessionId, request: StepRequest, state: State) -> ReplayState:
    try:
        return await state.replay.step(session_id, request.bars)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc


@router.post("/api/v1/replay/sessions/{session_id}/answer", response_model=ReplayState, tags=["replay"])
async def replay_answer(session_id: SessionId, request: QuizAnswerRequest, state: State) -> ReplayState:
    try:
        return await state.replay.answer(session_id, request)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc


@router.post("/api/v1/replay/sessions/{session_id}/end", response_model=ReplayReveal, tags=["replay"])
async def replay_end(session_id: SessionId, state: State) -> ReplayReveal:
    """Ends the session and reveals the instrument, dates and price scale (BLIND sessions)."""
    try:
        return await state.replay.end(session_id)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc


@router.delete("/api/v1/replay/sessions/{session_id}", status_code=204, tags=["replay"])
async def replay_delete(session_id: SessionId, state: State) -> Response:
    try:
        await state.replay.delete(session_id)
    except REPLAY_ERRORS as exc:
        raise _replay_error(exc) from exc
    return Response(status_code=204)
