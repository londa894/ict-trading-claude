"""News state (pure): relevant calendar events around `now` decide CLEAR / CAUTION / BLACKOUT /
POST_NEWS_WAIT / NORMALIZED, or UNAVAILABLE when the calendar cannot prove the period is clear.

Per relevant event E at time T with importance windows (before b, after a, post wait p, caution lead c):
  BLACKOUT        [T - b, T + a)              DELAYED events: [T - b, T + delayedMax)
  POST_NEWS_WAIT  [blackout end, + p)
  NORMALIZED      [post end, + normalizedMinutes)
  CAUTION         [T - c, blackout start)     MEDIUM (no blackout): [T - c, T + c)
Overall state = the most severe across events (BLACKOUT > POST_NEWS_WAIT > CAUTION > NORMALIZED > CLEAR).
Calendar UNAVAILABLE when: no provider/snapshot, fetched more than calendarMaxAgeHours ago, coverage ending
before now + requiredLookaheadHours, or coverage starting after the longest look-back window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.contracts import strategy_version
from app.domain.enums import Blocker, EventImportance, EventStatus, NewsState
from app.domain.instrument import Instrument
from app.services.news.models import (
    CalendarInfo,
    CalendarSnapshot,
    EconomicEvent,
    EventView,
    NewsAssessment,
    NewsConfig,
    Value,
)

SEVERITY = [
    NewsState.CLEAR,
    NewsState.NORMALIZED,
    NewsState.CAUTION,
    NewsState.POST_NEWS_WAIT,
    NewsState.BLACKOUT,
]
IMPORTANCE_ORDER = list(EventImportance)
MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def relevant_currencies(instrument: Instrument, cfg: NewsConfig) -> list[str]:
    rule = cfg.relevant[instrument.asset_class]
    return [instrument.quote] if rule == "QUOTE" else [instrument.base, instrument.quote]


def parse_number(value: Value) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = value.strip().replace(",", "").replace("%", "")
    m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)([KMBT]?)", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1)) * MULTIPLIERS.get(m.group(2).upper(), 1.0)


@dataclass(frozen=True)
class Windows:
    blackout: tuple[datetime, datetime] | None
    post: tuple[datetime, datetime] | None
    normalized: tuple[datetime, datetime] | None
    caution: tuple[datetime, datetime] | None


def windows_for(e: EconomicEvent, cfg: NewsConfig) -> Windows:
    w = cfg.windows.get(e.importance)
    if w is None:
        return Windows(None, None, None, None)
    t = e.scheduled_time
    minutes = timedelta(minutes=1)
    if w.blackout_before + w.blackout_after == 0:
        return Windows(None, None, None, (t - w.caution_lead * minutes, t + w.caution_lead * minutes))
    start = t - w.blackout_before * minutes
    end = t + (cfg.delayed_max_minutes if e.status is EventStatus.DELAYED else w.blackout_after) * minutes
    post_end = end + w.post_wait * minutes
    return Windows(
        blackout=(start, end),
        post=(end, post_end) if w.post_wait else None,
        normalized=(post_end, post_end + cfg.normalized_minutes * minutes),
        caution=(t - w.caution_lead * minutes, start),
    )


def _inside(window: tuple[datetime, datetime] | None, now: datetime) -> bool:
    return window is not None and window[0] <= now < window[1]


def event_state(
    e: EconomicEvent, now: datetime, cfg: NewsConfig
) -> tuple[NewsState, tuple[datetime, datetime] | None]:
    if e.status is EventStatus.CANCELLED:
        return NewsState.CLEAR, None
    w = windows_for(e, cfg)
    for state, window in (
        (NewsState.BLACKOUT, w.blackout),
        (NewsState.POST_NEWS_WAIT, w.post),
        (NewsState.CAUTION, w.caution),
        (NewsState.NORMALIZED, w.normalized),
    ):
        if _inside(window, now):
            return state, window
    return NewsState.CLEAR, None


def derive_status(e: EconomicEvent, now: datetime, cfg: NewsConfig) -> EventStatus:
    if e.status in (EventStatus.CANCELLED, EventStatus.DELAYED, EventStatus.REVISED):
        return e.status
    t = e.scheduled_time
    if now < t - timedelta(minutes=cfg.imminent_minutes):
        return EventStatus.UPCOMING if e.actual is None else EventStatus.RELEASED
    if now < t:
        return EventStatus.IMMINENT if e.actual is None else EventStatus.RELEASED
    w = windows_for(e, cfg)
    settled = w.normalized[1] if w.normalized else (w.caution[1] if w.caution else t)
    return EventStatus.RELEASED if now < settled else EventStatus.COMPLETED


def view(e: EconomicEvent, now: datetime, cfg: NewsConfig) -> EventView:
    actual, forecast = parse_number(e.actual), parse_number(e.forecast)
    surprise = round(actual - forecast, 6) if actual is not None and forecast is not None else None
    pct = round(surprise / abs(forecast) * 100, 2) if surprise is not None and forecast else None
    w = windows_for(e, cfg)
    return EventView(
        id=e.id,
        country=e.country,
        currency=e.currency,
        name=e.name,
        scheduled_time=e.scheduled_time,
        importance=e.importance,
        status=derive_status(e, now, cfg),
        actual=e.actual,
        forecast=e.forecast,
        previous=e.previous,
        revised_previous=e.revised_previous,
        surprise=surprise,
        surprise_pct=pct,
        blackout_start=w.blackout[0] if w.blackout else None,
        blackout_end=w.blackout[1] if w.blackout else None,
        minutes_to_event=round((e.scheduled_time - now).total_seconds() / 60, 1),
    )


def calendar_info(snapshot: CalendarSnapshot | None, provider: str, reason: str | None) -> CalendarInfo:
    return CalendarInfo(
        provider=snapshot.provider if snapshot else provider,
        source=snapshot.source if snapshot else None,
        is_synthetic=snapshot.is_synthetic if snapshot else False,
        available=reason is None,
        fetched_at=snapshot.fetched_at if snapshot else None,
        coverage_start=snapshot.coverage_start if snapshot else None,
        coverage_end=snapshot.coverage_end if snapshot else None,
        reason=reason,
    )


def coverage_problem(snapshot: CalendarSnapshot, now: datetime, cfg: NewsConfig) -> str | None:
    if now - snapshot.fetched_at > timedelta(hours=cfg.calendar_max_age_hours):
        return f"the calendar was fetched more than {cfg.calendar_max_age_hours:g} h ago"
    if snapshot.fetched_at > now + timedelta(minutes=5):
        return "the calendar fetch time is in the future"
    if snapshot.coverage_end < now + timedelta(hours=cfg.required_lookahead_hours):
        return f"the calendar does not cover the next {cfg.required_lookahead_hours:g} h"
    longest = max(w.blackout_after + w.post_wait + w.caution_lead for w in cfg.windows.values())
    lookback = timedelta(minutes=longest + cfg.delayed_max_minutes + cfg.normalized_minutes)
    if snapshot.coverage_start > now - lookback:
        return "the calendar does not cover the recent past needed for post-news windows"
    return None


def assess(
    instrument: Instrument,
    now: datetime,
    snapshot: CalendarSnapshot | None,
    unavailable_reason: str | None,
    cfg: NewsConfig,
    provider_name: str,
) -> NewsAssessment:
    currencies = relevant_currencies(instrument, cfg)
    reason = unavailable_reason if snapshot is None else coverage_problem(snapshot, now, cfg)
    info = calendar_info(snapshot, provider_name, reason)
    base = {
        "symbol": instrument.symbol,
        "relevant_currencies": currencies,
        "calendar": info,
        "strategy_version": strategy_version(),
        "generated_at": now,
    }
    if snapshot is None or reason is not None:
        return NewsAssessment(
            **base,
            state=NewsState.UNAVAILABLE,
            active_event=None,
            next_event=None,
            window_start=None,
            window_end=None,
            events=[],
            blockers=[Blocker.NEWS_DATA_UNAVAILABLE],
            warnings=[f"News cannot be proven clear: {reason or 'calendar unavailable'}"],
        )

    relevant = [e for e in snapshot.events if e.currency in currencies]
    state, active, window = NewsState.CLEAR, None, None
    for e in sorted(relevant, key=lambda x: x.scheduled_time):
        s, w = event_state(e, now, cfg)
        if SEVERITY.index(s) > SEVERITY.index(state):
            state, active, window = s, e, w
    upcoming = [
        e
        for e in relevant
        if e.scheduled_time > now
        and e.status is not EventStatus.CANCELLED
        and IMPORTANCE_ORDER.index(e.importance) >= IMPORTANCE_ORDER.index(EventImportance.MEDIUM)
    ]
    nxt = min(upcoming, key=lambda e: e.scheduled_time) if upcoming else None
    listed = [
        e
        for e in relevant
        if now - timedelta(hours=cfg.list_before_hours)
        <= e.scheduled_time
        <= now + timedelta(hours=cfg.list_after_hours)
    ]
    blockers: list[Blocker] = []
    if state is NewsState.BLACKOUT:
        blockers.append(Blocker.NEWS_BLACKOUT)
    elif state is NewsState.POST_NEWS_WAIT:
        blockers.append(Blocker.NEWS_POST_WAIT)
    if snapshot.is_synthetic:
        blockers.append(Blocker.NEWS_DATA_SYNTHETIC)
    warnings = []
    if state is NewsState.CAUTION and active is not None:
        warnings.append(
            f"{active.importance.value} {active.currency} event at {active.scheduled_time.isoformat()}"
        )
    if snapshot.is_synthetic:
        warnings.append("Synthetic calendar: not market facts")
    return NewsAssessment(
        **base,
        state=state,
        active_event=view(active, now, cfg) if active else None,
        next_event=view(nxt, now, cfg) if nxt else None,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        events=[view(e, now, cfg) for e in sorted(listed, key=lambda x: x.scheduled_time)],
        blockers=blockers,
        warnings=warnings,
    )
