"""Session & Time models and configuration (spec STEP 4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    AsianRangeState,
    DataQuality,
    Direction,
    ExpansionState,
    JudasStatus,
    KillZone,
    MarketStatus,
    SessionInstanceState,
    SessionName,
    SessionQuality,
    Timeframe,
)

TRADING_DAY_ROLL = time(17, 0)


def _parse(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class Window:
    """A New York wall-clock window inside one trading day ((D-1) 17:00 -> D 17:00)."""

    name: str
    start: time
    end: time

    @property
    def starts_previous_evening(self) -> bool:
        return self.start >= TRADING_DAY_ROLL

    @property
    def minutes(self) -> int:
        start = self.start.hour * 60 + self.start.minute
        end = self.end.hour * 60 + self.end.minute
        return (end - start) % (24 * 60)


@dataclass(frozen=True)
class AsianRangeConfig:
    lookback_sessions: int
    min_sessions: int
    tight_below: float
    normal_below: float
    expanded_below: float


@dataclass(frozen=True)
class AdrConfig:
    period_days: int
    consolidating_below_pct: float
    early_below_pct: float
    active_below_pct: float
    late_below_pct: float


@dataclass(frozen=True)
class SessionConfig:
    source_timeframe: Timeframe
    source_bars: int
    pool_source_bars: int
    sessions: dict[SessionName, Window]
    kill_zones: dict[KillZone, Window]
    time_quality: dict[str, SessionQuality]
    outside_quality: SessionQuality
    asian_range: AsianRangeConfig
    adr: AdrConfig
    judas_sessions: tuple[SessionName, ...]
    pool_sessions: tuple[SessionName, ...]
    pool_instances: int
    decision_timeframe: Timeframe

    @classmethod
    def from_spec(cls) -> SessionConfig:
        s = load_spec("sessions")
        if s["timezone"] != "America/New_York":
            raise ValueError("session windows are defined in America/New_York wall time only")
        source = Timeframe(s["sourceTimeframe"])
        step = int(source.duration / timedelta(minutes=1))
        sessions = {
            SessionName(k): Window(k, _parse(v["start"]), _parse(v["end"])) for k, v in s["sessions"].items()
        }
        kill_zones = {
            KillZone(k): Window(k, _parse(v["start"]), _parse(v["end"])) for k, v in s["killZones"].items()
        }
        for w in [*sessions.values(), *kill_zones.values()]:
            for t in (w.start, w.end):
                if (t.hour * 60 + t.minute) % step:
                    raise ValueError(f"{w.name} boundary {t} is not aligned to {source}")
            if w.minutes == 0:
                raise ValueError(f"{w.name} is empty")
            # A window must not cross the 17:00 trading-day roll.
            offset = ((w.start.hour - 17) * 60 + w.start.minute) % (24 * 60)
            if offset + w.minutes > 24 * 60:
                raise ValueError(f"{w.name} crosses the trading-day roll")
        tq = s["timeQuality"]
        a, d = s["asianRange"], s["adr"]
        return cls(
            source_timeframe=source,
            source_bars=int(s["sourceBars"]),
            pool_source_bars=int(s["poolSourceBars"]),
            sessions=sessions,
            kill_zones=kill_zones,
            time_quality={
                k: SessionQuality(v) for k, v in tq.items() if not k.startswith("$") and k != "outside"
            },
            outside_quality=SessionQuality(tq["outside"]),
            asian_range=AsianRangeConfig(
                lookback_sessions=int(a["lookbackSessions"]),
                min_sessions=int(a["minSessions"]),
                tight_below=float(a["tightBelow"]),
                normal_below=float(a["normalBelow"]),
                expanded_below=float(a["expandedBelow"]),
            ),
            adr=AdrConfig(
                period_days=int(d["periodDays"]),
                consolidating_below_pct=float(d["consolidatingBelowPct"]),
                early_below_pct=float(d["earlyBelowPct"]),
                active_below_pct=float(d["activeBelowPct"]),
                late_below_pct=float(d["lateBelowPct"]),
            ),
            judas_sessions=tuple(SessionName(x) for x in s["judas"]["sessions"]),
            pool_sessions=tuple(SessionName(x) for x in s["liquidityPools"]["sessions"]),
            pool_instances=int(s["liquidityPools"]["instancesPerSession"]),
            decision_timeframe=Timeframe(s["decisionContext"]["timeframe"]),
        )


class SessionClock(ApiModel):
    """Time-only facts at `now` (no market data involved)."""

    now: datetime
    new_york_time: str
    london_time: str
    trading_day: date
    market_status: MarketStatus
    active_sessions: list[SessionName]
    active_kill_zones: list[KillZone]
    time_quality: SessionQuality
    next_session: SessionName | None
    next_session_start: datetime | None


class SessionInstance(ApiModel):
    id: str
    session: SessionName
    trading_day: date
    start: datetime
    end: datetime
    state: SessionInstanceState
    high: float | None
    low: float | None
    midpoint: float | None
    range: float | None
    high_time: datetime | None
    low_time: datetime | None
    candle_count: int
    expected_count: int
    known_at: datetime | None  # window end, COMPLETE instances only
    asian_range_state: AsianRangeState | None
    asian_range_ratio: float | None


class SessionOpens(ApiModel):
    daily_open: float | None
    daily_open_time: datetime | None
    ny_midnight_open: float | None
    ny_midnight_open_time: datetime | None
    weekly_open: float | None
    weekly_open_time: datetime | None
    last_close: float | None
    daily_change: float | None
    daily_change_pct: float | None


class PreviousSession(ApiModel):
    instance_id: str
    session: SessionName
    high: float
    low: float
    end: datetime


class AdrState(ApiModel):
    adr: float
    period_days: int
    current_range: float | None
    pct_used: float | None
    expansion: ExpansionState | None


class JudasSwing(ApiModel):
    id: str
    trading_day: date
    session: SessionName
    direction: Direction  # direction of the expected real move (opposite the sweep)
    status: JudasStatus
    asian_high: float
    asian_low: float
    asian_midpoint: float
    sweep_time: datetime
    sweep_extreme: float
    resolved_at: datetime | None
    detail: str


class SessionAnalysis(ApiModel):
    symbol: str
    source_timeframe: Timeframe
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    clock: SessionClock
    session_quality: SessionQuality | None
    instances: list[SessionInstance]
    opens: SessionOpens
    previous_session: PreviousSession | None
    adr: AdrState | None
    judas: list[JudasSwing]
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
