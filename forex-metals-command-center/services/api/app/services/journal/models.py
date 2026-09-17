"""Journal V1 models (spec STEP 10 journal part, Phase 15). Manual records only: nothing is ever executed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, is_utc
from app.domain.enums import (
    Direction,
    ExitReason,
    ExtremeSource,
    JournalEntryKind,
    JournalStatus,
    ProcessClassification,
    RuleViolation,
    SnapshotIntegrity,
    SnapshotTiming,
    Timeframe,
    TradeResult,
)


@dataclass(frozen=True)
class JournalConfig:
    break_even_tolerance_r: float
    full_win_tolerance_fraction: float
    full_loss_tolerance_r: float
    chase_tolerance_r: float
    post_entry_snapshot_minutes: float
    max_open_days: float
    extremes_timeframes: tuple[Timeframe, ...]
    max_notes_length: int
    list_default_limit: int
    list_max_limit: int

    @classmethod
    def from_spec(cls) -> JournalConfig:
        s = load_spec("journal")
        cfg = cls(
            break_even_tolerance_r=float(s["breakEvenToleranceR"]),
            full_win_tolerance_fraction=float(s["fullWinToleranceFraction"]),
            full_loss_tolerance_r=float(s["fullLossToleranceR"]),
            chase_tolerance_r=float(s["chaseToleranceR"]),
            post_entry_snapshot_minutes=float(s["postEntrySnapshotMinutes"]),
            max_open_days=float(s["maxOpenDays"]),
            extremes_timeframes=tuple(Timeframe(x) for x in s["extremesTimeframes"]),
            max_notes_length=int(s["maxNotesLength"]),
            list_default_limit=int(s["listDefaultLimit"]),
            list_max_limit=int(s["listMaxLimit"]),
        )
        if not 0 <= cfg.break_even_tolerance_r < 1 or not 0 <= cfg.full_loss_tolerance_r < 1:
            raise ValueError("journal R tolerances must be in [0, 1)")
        if not 0 <= cfg.full_win_tolerance_fraction < 1 or cfg.chase_tolerance_r < 0:
            raise ValueError("journal win/chase tolerances invalid")
        if not 1 <= cfg.list_default_limit <= cfg.list_max_limit:
            raise ValueError("journal list limits invalid")
        return cfg


NOTES_MAX = JournalConfig.from_spec().max_notes_length


def _utc(value: datetime, field: str) -> datetime:
    if not is_utc(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return value


# --- requests --------------------------------------------------------------------


class JournalTradeFill(ApiModel):
    """The user's own manual trade, typed in after the fact. Prices only; no broker data is ever requested."""

    direction: Direction
    entry: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)  # None = no stop was placed
    targets: list[float] = Field(default_factory=list, max_length=3)
    volume: float | None = Field(default=None, gt=0)
    risk_pct: float | None = Field(default=None, gt=0, le=100)
    opened_at: datetime

    @field_validator("opened_at")
    @classmethod
    def _opened_utc(cls, value: datetime) -> datetime:
        return _utc(value, "openedAt")

    @field_validator("targets")
    @classmethod
    def _positive_targets(cls, value: list[float]) -> list[float]:
        if any(t <= 0 for t in value):
            raise ValueError("targets must be positive prices")
        return value

    @model_validator(mode="after")
    def _sides(self) -> Self:
        sign = 1 if self.direction is Direction.BULLISH else -1
        if self.stop is not None and sign * (self.entry - self.stop) <= 0:
            raise ValueError("stop must be on the losing side of the entry")
        if any(sign * (t - self.entry) <= 0 for t in self.targets):
            raise ValueError("targets must be on the winning side of the entry")
        return self


class CreateJournalEntryRequest(ApiModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    kind: JournalEntryKind
    trade: JournalTradeFill | None = None
    notes: str = Field(default="", max_length=NOTES_MAX)

    @model_validator(mode="after")
    def _trade_for_trades(self) -> Self:
        if (self.kind is JournalEntryKind.TRADE) != (self.trade is not None):
            raise ValueError("trade details are required for TRADE entries and not allowed otherwise")
        return self


class RecordOutcomeRequest(ApiModel):
    exit_price: float = Field(gt=0)  # average exit price when scaled out
    exited_at: datetime
    exit_reason: ExitReason
    mfe_price: float | None = Field(default=None, gt=0)
    mae_price: float | None = Field(default=None, gt=0)
    reported_violations: list[RuleViolation] = Field(default_factory=list, max_length=len(RuleViolation))
    notes: str = Field(default="", max_length=NOTES_MAX)

    @field_validator("exited_at")
    @classmethod
    def _exit_utc(cls, value: datetime) -> datetime:
        return _utc(value, "exitedAt")

    @field_validator("reported_violations")
    @classmethod
    def _unique(cls, value: list[RuleViolation]) -> list[RuleViolation]:
        return list(dict.fromkeys(value))


# --- stored / responses --------------------------------------------------------------------


class SnapshotSummary(ApiModel):
    """Spec journal fields read from the immutable snapshot (null = the engine had no value)."""

    captured_at: datetime
    day_of_week: str
    active_sessions: list[str]
    active_kill_zones: list[str]
    time_quality: str | None
    verdict: str
    data_quality: str
    engine_authorization: str
    execution_timeframe: str | None
    htf_bias: str | None
    primary_dol: str | None
    liquidity_event: str | None
    structure_event: str | None
    displacement: str | None
    pd_array: str | None
    no_wick: str | None
    news_state: str | None
    macro_bias: str | None
    macro_state: str | None
    setup_type: str | None
    setup_state: str | None
    setup_score: float | None
    setup_grade: str | None
    confidence: str | None
    plan_entry: float | None
    plan_stop: float | None
    plan_targets: list[float]
    plan_rr: float | None
    risk_status: str | None
    blockers: list[str]
    is_synthetic: bool
    strategy_version: str


class JournalSnapshot(ApiModel):
    captured_at: datetime
    timing: SnapshotTiming
    integrity: SnapshotIntegrity
    hash: str
    decision: dict[str, object]
    evaluation: dict[str, object] | None
    data: dict[str, object] | None


class JournalOutcome(ApiModel):
    revision: int
    recorded_at: datetime
    exit_price: float
    exited_at: datetime
    exit_reason: ExitReason
    mfe_price: float | None
    mae_price: float | None
    extreme_source: ExtremeSource
    extremes_synthetic: bool
    extremes_detail: str | None
    result: TradeResult
    r_multiple: float | None
    planned_r: float | None
    mfe_r: float | None
    mae_r: float | None
    entry_efficiency: float | None
    exit_efficiency: float | None
    duration_minutes: float
    reported_violations: list[RuleViolation]
    violations: list[RuleViolation]  # detected at entry + reported
    classification: ProcessClassification
    notes: str
    integrity: SnapshotIntegrity


class JournalEntry(ApiModel):
    id: str
    kind: JournalEntryKind
    status: JournalStatus
    symbol: str
    created_at: datetime
    trade: JournalTradeFill | None
    notes: str
    detected_violations: list[RuleViolation]
    result: TradeResult | None
    classification: ProcessClassification | None
    r_multiple: float | None
    summary: SnapshotSummary
    snapshot: JournalSnapshot
    outcome: JournalOutcome | None  # latest revision
    outcome_revisions: list[JournalOutcome]
    strategy_version: str


class JournalEntryRow(ApiModel):
    """List row: the entry without its snapshot body or outcome history."""

    id: str
    kind: JournalEntryKind
    status: JournalStatus
    symbol: str
    created_at: datetime
    direction: Direction | None
    entry: float | None
    result: TradeResult | None
    classification: ProcessClassification | None
    r_multiple: float | None
    detected_violations: list[RuleViolation]
    snapshot_timing: SnapshotTiming
    integrity: SnapshotIntegrity
    is_synthetic: bool
    setup_type: str | None
    verdict: str


class JournalStoreInfo(ApiModel):
    available: bool
    backend: str
    reason: str | None


class JournalListResponse(ApiModel):
    entries: list[JournalEntryRow]
    next_cursor: str | None
    store: JournalStoreInfo
    generated_at: datetime


class JournalExport(ApiModel):
    exported_at: datetime
    strategy_version: str
    store: JournalStoreInfo
    entries: list[JournalEntry]
