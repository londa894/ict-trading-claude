from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings
from app.providers.base import MarketDataProvider
from app.services.alerts.service import AlertService
from app.services.analytics.service import AnalyticsService
from app.services.assistant.service import AssistantService
from app.services.backtest.service import BacktestService
from app.services.candles.service import CandleService
from app.services.journal.service import JournalService
from app.services.liquidity.service import LiquidityService
from app.services.macro.service import MacroService
from app.services.market_state.service import MarketStateService
from app.services.news.service import NewsService
from app.services.no_wick.service import NoWickService
from app.services.paper.service import PaperService
from app.services.pd_arrays.service import PdArrayService
from app.services.replay.service import ReplayService
from app.services.risk.service import RiskService
from app.services.scanner.service import ScannerService
from app.services.scoring.service import EvaluationService
from app.services.sessions.service import SessionService
from app.services.setup_state.service import SetupService
from app.services.structure.service import StructureService


@dataclass
class AppState:
    settings: Settings
    provider: MarketDataProvider
    market_state: MarketStateService
    candles: CandleService
    structure: StructureService
    liquidity: LiquidityService
    pd_arrays: PdArrayService
    no_wick: NoWickService
    sessions: SessionService
    setups: SetupService
    evaluation: EvaluationService
    risk: RiskService
    scanner: ScannerService
    alerts: AlertService
    assistant: AssistantService
    news: NewsService
    macro: MacroService
    journal: JournalService
    paper: PaperService
    analytics: AnalyticsService
    backtest: BacktestService
    replay: ReplayService


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.fmcc
    return state
