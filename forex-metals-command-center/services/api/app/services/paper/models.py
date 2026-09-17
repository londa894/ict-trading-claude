"""Paper trading models (Phase 16). Simulated only: nothing is sent anywhere and nothing is authorized."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    DataQuality,
    Direction,
    ExitReason,
    PaperEntryType,
    PaperEventType,
    PaperSource,
    PaperStatus,
    ProcessClassification,
    RuleViolation,
    SnapshotIntegrity,
    Timeframe,
    TradeResult,
)
from app.services.journal.models import NOTES_MAX, SnapshotSummary


class AssumedCosts(ApiModel):
    """Assumed per-symbol costs in price units (not broker specifications)."""

    spread: float = Field(ge=0)
    slippage: float = Field(ge=0)
    commission: float = Field(ge=0)  # per side


@dataclass(frozen=True)
class PaperConfig:
    timeframe: Timeframe
    pending_expiry_bars: int
    max_bars_per_advance: int
    advance_seconds: float
    max_open_sims: int
    costs: dict[str, AssumedCosts]

    @classmethod
    def from_spec(cls) -> PaperConfig:
        s = load_spec("paper")
        cfg = cls(
            timeframe=Timeframe(s["executionTimeframe"]),
            pending_expiry_bars=int(s["pendingExpiryBars"]),
            max_bars_per_advance=int(s["maxBarsPerAdvance"]),
            advance_seconds=float(s["advanceSeconds"]),
            max_open_sims=int(s["maxOpenSims"]),
            costs={k: AssumedCosts.model_validate(v) for k, v in s["assumedCosts"].items()},
        )
        if cfg.pending_expiry_bars < 1 or cfg.max_bars_per_advance < 10 or cfg.max_open_sims < 1:
            raise ValueError("paper limits invalid")
        return cfg


class CreatePaperSimRequest(ApiModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    source: PaperSource = PaperSource.MANUAL
    direction: Direction | None = None  # MANUAL only; ENGINE_PLAN takes the plan's direction
    entry_type: PaperEntryType = PaperEntryType.MARKET
    limit_price: float | None = Field(default=None, gt=0)
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=NOTES_MAX)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.source is PaperSource.ENGINE_PLAN:
            if any(v is not None for v in (self.direction, self.limit_price, self.stop, self.target)):
                raise ValueError(
                    "ENGINE_PLAN sims take direction, entry, stop and target from the confirmed plan"
                )
            return self
        if self.direction is None or self.stop is None or self.target is None:
            raise ValueError("MANUAL sims need direction, stop and target")
        if (self.entry_type is PaperEntryType.LIMIT) != (self.limit_price is not None):
            raise ValueError("limitPrice is required for LIMIT entries and not allowed for MARKET entries")
        return self


class PaperEvent(ApiModel):
    seq: int
    type: PaperEventType
    at: datetime  # bar open time (intrabar timing is unknown) or the action time
    price: float | None
    ambiguous: (
        bool  # stop and target (or fill and stop) touched in the same bar: conservative outcome assumed
    )
    detail: str
    integrity: SnapshotIntegrity


class PaperResult(ApiModel):
    result: TradeResult
    exit_reason: ExitReason
    r_multiple: float | None  # after assumed spread and slippage
    net_r_multiple: float | None  # after assumed commission too
    planned_r: float | None
    mfe_r: float | None
    mae_r: float | None
    entry_efficiency: float | None
    exit_efficiency: float | None
    duration_minutes: float
    violations: list[RuleViolation]
    classification: ProcessClassification


class PaperSim(ApiModel):
    id: str
    symbol: str
    source: PaperSource
    direction: Direction
    entry_type: PaperEntryType
    reference_price: float  # limit price, or the last closed price at creation for MARKET entries
    limit_price: float | None
    stop: float
    target: float
    costs: AssumedCosts
    status: PaperStatus
    created_at: datetime
    fill_price: float | None
    filled_at: datetime | None
    exit_price: float | None
    exited_at: datetime | None
    processed_through: datetime | None  # open time of the last simulated bar
    data_quality: DataQuality | None  # of the last advance
    data_note: str | None
    is_synthetic: bool
    detected_violations: list[RuleViolation]
    result: PaperResult | None
    events: list[PaperEvent]
    summary: SnapshotSummary
    integrity: SnapshotIntegrity
    notes: str
    authority: str  # always SIMULATION_ONLY
    strategy_version: str


class PaperSimRow(ApiModel):
    id: str
    symbol: str
    source: PaperSource
    direction: Direction
    entry_type: PaperEntryType
    status: PaperStatus
    created_at: datetime
    fill_price: float | None
    exit_price: float | None
    result: TradeResult | None
    net_r_multiple: float | None
    classification: ProcessClassification | None
    is_synthetic: bool
    integrity: SnapshotIntegrity


class PaperStoreInfo(ApiModel):
    available: bool
    backend: str
    reason: str | None
    monitor_enabled: bool


class PaperListResponse(ApiModel):
    sims: list[PaperSimRow]
    next_cursor: str | None
    store: PaperStoreInfo
    generated_at: datetime
