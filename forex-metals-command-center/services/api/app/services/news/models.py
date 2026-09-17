"""Economic calendar & news gate models (spec STEP 7, Phase 13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, require_utc
from app.domain.enums import AssetClass, Blocker, EventImportance, EventStatus, NewsState

Value = float | str | None


@dataclass(frozen=True)
class ImportanceWindow:
    blackout_before: int
    blackout_after: int
    post_wait: int
    caution_lead: int


@dataclass(frozen=True)
class NewsConfig:
    relevant: dict[AssetClass, str]
    windows: dict[EventImportance, ImportanceWindow]
    normalized_minutes: int
    imminent_minutes: int
    delayed_max_minutes: int
    calendar_max_age_hours: float
    required_lookahead_hours: float
    list_before_hours: float
    list_after_hours: float
    countdown_minutes: tuple[int, ...]

    @classmethod
    def from_spec(cls) -> NewsConfig:
        s = load_spec("news")
        windows = {
            EventImportance(k): ImportanceWindow(
                int(v["blackoutBeforeMinutes"]),
                int(v["blackoutAfterMinutes"]),
                int(v["postWaitMinutes"]),
                int(v["cautionLeadMinutes"]),
            )
            for k, v in s["windows"].items()
        }
        if EventImportance.LOW in windows:
            raise ValueError("LOW importance events never gate the decision")
        relevant = {AssetClass(k): str(v) for k, v in s["relevantCurrencies"].items()}
        if set(relevant) != set(AssetClass) or not set(relevant.values()) <= {"QUOTE", "BASE_AND_QUOTE"}:
            raise ValueError("relevantCurrencies must map every asset class to QUOTE or BASE_AND_QUOTE")
        return cls(
            relevant=relevant,
            windows=windows,
            normalized_minutes=int(s["normalizedMinutes"]),
            imminent_minutes=int(s["imminentMinutes"]),
            delayed_max_minutes=int(s["delayedMaxMinutes"]),
            calendar_max_age_hours=float(s["calendarMaxAgeHours"]),
            required_lookahead_hours=float(s["requiredLookaheadHours"]),
            list_before_hours=float(s["listBeforeHours"]),
            list_after_hours=float(s["listAfterHours"]),
            countdown_minutes=tuple(sorted((int(x) for x in s["countdownMinutes"]), reverse=True)),
        )


class EconomicEvent(ApiModel):
    """Spec STEP 7 EconomicEvent. `status` may be omitted: it is then derived from the clock."""

    id: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=60)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1, max_length=200)
    scheduled_time: datetime
    importance: EventImportance
    actual: Value = None
    forecast: Value = None
    previous: Value = None
    revised_previous: Value = None
    status: EventStatus | None = None

    @field_validator("scheduled_time")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "scheduled_time")


class CalendarFile(ApiModel):
    """Server-side calendar file. Coverage says which period the file is complete for."""

    source: str = Field(min_length=1, max_length=200)
    fetched_at: datetime
    coverage_start: datetime
    coverage_end: datetime
    events: list[EconomicEvent]

    @field_validator("fetched_at", "coverage_start", "coverage_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "calendar time")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.coverage_end <= self.coverage_start:
            raise ValueError("coverage_end must be after coverage_start")
        ids = [e.id for e in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("event ids must be unique")
        return self


@dataclass(frozen=True)
class CalendarSnapshot:
    provider: str
    source: str
    is_synthetic: bool
    fetched_at: datetime
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[EconomicEvent, ...]


class EventView(ApiModel):
    id: str
    country: str
    currency: str
    name: str
    scheduled_time: datetime
    importance: EventImportance
    status: EventStatus
    actual: Value
    forecast: Value
    previous: Value
    revised_previous: Value
    surprise: float | None  # actual - forecast, when both are numeric (no direction is inferred)
    surprise_pct: float | None
    blackout_start: datetime | None
    blackout_end: datetime | None
    minutes_to_event: float


class CalendarInfo(ApiModel):
    provider: str
    source: str | None
    is_synthetic: bool
    available: bool
    fetched_at: datetime | None
    coverage_start: datetime | None
    coverage_end: datetime | None
    reason: str | None


class NewsAssessment(ApiModel):
    """The news gate for one symbol. BLACKOUT is a hard blocker; UNAVAILABLE means clear cannot be proven."""

    symbol: str
    state: NewsState
    relevant_currencies: list[str]
    active_event: EventView | None
    next_event: EventView | None
    window_start: datetime | None
    window_end: datetime | None
    events: list[EventView]
    calendar: CalendarInfo
    blockers: list[Blocker]
    warnings: list[str]
    strategy_version: str
    generated_at: datetime


class CalendarResponse(ApiModel):
    events: list[EventView]
    calendar: CalendarInfo
    generated_at: datetime
