"""Watchlist & scanner models (Phase 10). A scan row is a projection of the symbol's Master Decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AssetClass,
    Blocker,
    DataQuality,
    DecisionConfidence,
    MarketStatus,
    PositionSizeStatus,
    SetupState,
    Verdict,
)

RANKING_RULES = [
    "usable data first (verdict not UNAVAILABLE)",
    "deeply validated markets first",
    "further setup progress first (BLOCKED = confirmed pending gates)",
    "higher setup score first (no score last)",
    "fewer blockers first",
    "instrument priority, then symbol",
]


@dataclass(frozen=True)
class ScannerConfig:
    cache_seconds: float
    max_symbols: int
    setup_progress: tuple[SetupState, ...]

    @classmethod
    def from_spec(cls) -> ScannerConfig:
        s = load_spec("scanner")
        progress = tuple(SetupState(x) for x in s["setupProgress"])
        if len(set(progress)) != len(progress):
            raise ValueError("setupProgress must not repeat a state")
        return cls(
            cache_seconds=float(s["cacheSeconds"]),
            max_symbols=int(s["maxSymbols"]),
            setup_progress=progress,
        )

    def progress(self, setup_state: str) -> int:
        """1-based stage of an open setup; 0 for no setup, terminal states and NOT_EVALUATED."""
        for i, state in enumerate(self.setup_progress, start=1):
            if state.value == setup_state:
                return i
        return 0


class MarketRow(ApiModel):
    symbol: str
    asset_class: AssetClass
    base: str
    quote: str
    priority: int
    deeply_validated: bool
    market_status: MarketStatus
    position_size_status: PositionSizeStatus


class ScanRow(ApiModel):
    rank: int
    symbol: str
    asset_class: AssetClass
    priority: int
    deeply_validated: bool
    market_status: MarketStatus
    verdict: Verdict
    data_quality: DataQuality
    htf_bias: str
    setup_state: str
    setup_type: str | None
    setup_progress: int
    setup_score: float | None
    setup_grade: str | None
    decision_confidence: DecisionConfidence
    risk_status: str
    primary_dol: str | None
    blockers: list[Blocker]
    next_required_event: str | None
    latest_closed_open_time: datetime | None
    evaluated_at: datetime
    cache_age_seconds: float
    error: str | None


class ScanResponse(ApiModel):
    """Attention ranking across markets. Never a trade signal; every row keeps its own fail-safe verdict."""

    rows: list[ScanRow]
    requested_symbols: list[str]
    min_score: float | None
    only_setups: bool
    ranking: list[str]
    cache_seconds: float
    duration_ms: int
    verdict_authority: str
    authority: str
    strategy_version: str
    scanned_at: datetime
