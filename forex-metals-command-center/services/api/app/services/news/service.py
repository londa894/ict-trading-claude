"""News service: calendar snapshot + news gate per symbol. A failure is UNAVAILABLE (blocks, never clears)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.domain.enums import EventImportance
from app.domain.instrument import get_instrument
from app.services.candles.service import UnknownSymbolError
from app.services.news.engine import IMPORTANCE_ORDER, assess, calendar_info, coverage_problem, view
from app.services.news.models import CalendarResponse, CalendarSnapshot, NewsAssessment, NewsConfig
from app.services.news.providers import CalendarProvider, CalendarUnavailableError

logger = logging.getLogger("fmcc.news")


class NewsService:
    def __init__(
        self,
        provider: CalendarProvider,
        clock: Callable[[], datetime] | None = None,
        cfg: NewsConfig | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or NewsConfig.from_spec()

    @property
    def cfg(self) -> NewsConfig:
        return self._cfg

    async def _snapshot(self, now: datetime) -> tuple[CalendarSnapshot | None, str | None]:
        try:
            return await self._provider.snapshot(now), None
        except CalendarUnavailableError as exc:
            return None, str(exc)
        except Exception as exc:
            logger.exception("calendar provider failed")
            return None, f"calendar provider failed ({type(exc).__name__})"

    async def assess(self, symbol: str, now: datetime | None = None) -> NewsAssessment:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnknownSymbolError(symbol)
        now = now or self._clock()
        snapshot, reason = await self._snapshot(now)
        return assess(instrument, now, snapshot, reason, self._cfg, self._provider.name)

    async def calendar(
        self,
        *,
        hours_before: float = 6,
        hours_after: float = 48,
        currency: str | None = None,
        min_importance: EventImportance = EventImportance.LOW,
    ) -> CalendarResponse:
        now = self._clock()
        snapshot, reason = await self._snapshot(now)
        if snapshot is not None and reason is None:
            reason = coverage_problem(snapshot, now, self._cfg)
        events = []
        if snapshot is not None:
            floor = IMPORTANCE_ORDER.index(min_importance)
            for e in snapshot.events:
                in_range = (
                    now - timedelta(hours=hours_before)
                    <= e.scheduled_time
                    <= now + timedelta(hours=hours_after)
                )
                if (
                    in_range
                    and (currency is None or e.currency == currency.upper())
                    and IMPORTANCE_ORDER.index(e.importance) >= floor
                ):
                    events.append(view(e, now, self._cfg))
        return CalendarResponse(
            events=sorted(events, key=lambda v: v.scheduled_time),
            calendar=calendar_info(snapshot, self._provider.name, reason),
            generated_at=now,
        )


def decision_context(n: NewsAssessment) -> dict[str, object]:
    """Compact MasterDecision.newsState."""
    nxt = n.next_event
    active = n.active_event
    return {
        "state": n.state.value,
        "relevantCurrencies": n.relevant_currencies,
        "activeEvent": {
            "name": active.name,
            "currency": active.currency,
            "importance": active.importance.value,
            "scheduledTime": active.scheduled_time.isoformat(),
        }
        if active
        else None,
        "nextEvent": {
            "name": nxt.name,
            "currency": nxt.currency,
            "importance": nxt.importance.value,
            "scheduledTime": nxt.scheduled_time.isoformat(),
            "minutesToEvent": nxt.minutes_to_event,
        }
        if nxt
        else None,
        "windowEnd": n.window_end.isoformat() if n.window_end else None,
        "calendarAvailable": n.calendar.available,
        "calendarSynthetic": n.calendar.is_synthetic,
    }
