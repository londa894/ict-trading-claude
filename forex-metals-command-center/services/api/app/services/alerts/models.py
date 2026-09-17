"""Alerts V1 models (spec STEP 9, Phase 11): engine state changes, never trade instructions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AlertCategory,
    AlertPriority,
    AlertType,
    ConditionStatus,
    Direction,
    DisplacementGrade,
    NoWickStrength,
    ReadyWatchState,
    RiskLock,
    SessionName,
)

PRIORITY_ORDER = list(AlertPriority)


@dataclass(frozen=True)
class AlertConfig:
    monitored_symbols: tuple[str, ...]
    max_monitored_symbols: int
    max_ready_watches: int
    poll_seconds: float
    max_quiet_seconds: float
    buffer_size: int
    novelty_grace_bars: int
    min_displacement_grade: DisplacementGrade
    min_no_wick_strength: NoWickStrength
    category_cooldown: dict[AlertCategory, float]
    types: dict[AlertType, tuple[AlertCategory, AlertPriority]]

    @classmethod
    def from_spec(cls) -> AlertConfig:
        s = load_spec("alerts")
        types = {
            AlertType(k): (AlertCategory(v["category"]), AlertPriority(v["priority"]))
            for k, v in s["types"].items()
        }
        if set(types) != set(AlertType):
            raise ValueError("alerts.json must configure every alert type")
        cooldown = {AlertCategory(k): float(v) for k, v in s["categoryCooldownSeconds"].items()}
        if set(cooldown) != set(AlertCategory):
            raise ValueError("alerts.json must set a cooldown for every category")
        return cls(
            monitored_symbols=tuple(str(x).upper() for x in s["monitoredSymbols"]),
            max_monitored_symbols=int(s["maxMonitoredSymbols"]),
            max_ready_watches=int(s["maxReadyWatches"]),
            poll_seconds=float(s["pollSeconds"]),
            max_quiet_seconds=float(s["maxQuietSeconds"]),
            buffer_size=int(s["bufferSize"]),
            novelty_grace_bars=int(s["noveltyGraceBars"]),
            min_displacement_grade=DisplacementGrade(s["minDisplacementGrade"]),
            min_no_wick_strength=NoWickStrength(s["minNoWickStrength"]),
            category_cooldown=cooldown,
            types=types,
        )


@dataclass(frozen=True)
class AlertCandidate:
    type: AlertType
    symbol: str
    reference: str  # dedupe reference inside (symbol, type): an event id, a lock, "decision"...
    title: str
    message: str
    occurred_at: datetime
    price: float | None = None
    direction: Direction | None = None


@dataclass(frozen=True)
class SymbolMemory:
    """What the previous monitor cycle saw for one symbol (state memory)."""

    as_of: datetime | None
    seen_ids: frozenset[str]
    approaching_pools: frozenset[str]
    verdict: str
    data_quality: str
    risk_locks: frozenset[RiskLock]
    warnings: frozenset[str]
    setup_id: str | None
    active_sessions: frozenset[SessionName]
    eligible: bool = field(default=False)
    news_state: str | None = None
    macro_bias: str | None = None  # BULLISH / BEARISH / NEUTRAL while macro is available
    countdowns: frozenset[str] = (
        frozenset()
    )  # event:threshold keys already announced (or crossed at baseline)


class Alert(ApiModel):
    id: str
    seq: int
    dedupe_key: str
    symbol: str
    type: AlertType
    category: AlertCategory
    priority: AlertPriority
    title: str
    message: str
    direction: Direction | None
    price: float | None
    occurred_at: datetime
    created_at: datetime
    strategy_version: str


class MonitorStatus(ApiModel):
    enabled: bool
    running: bool
    symbols: list[str]
    poll_seconds: float
    max_quiet_seconds: float
    cycles: int
    last_cycle_at: datetime | None
    last_cycle_ms: int | None
    last_error: str | None
    baselined: list[str]


class AlertFeed(ApiModel):
    """Newest first. `nextCursor` = highest sequence returned; pass it as `since` to get newer alerts."""

    alerts: list[Alert]
    next_cursor: int
    suppressed: dict[str, int]
    monitor: MonitorStatus
    authority: str
    generated_at: datetime


class ReadyCondition(ApiModel):
    name: str
    status: ConditionStatus
    detail: str


class ReadyWatch(ApiModel):
    """ALERT_ME_WHEN_READY. Fires once, only on a LONG/SHORT verdict under FULL verdict authority."""

    id: str
    symbol: str
    direction: Direction | None
    state: ReadyWatchState
    conditions: list[ReadyCondition]
    next_required_event: str | None
    created_at: datetime
    last_checked_at: datetime | None
    fired_at: datetime | None


class ReadyWatchRequest(ApiModel):
    symbol: str
    direction: Direction | None = None
