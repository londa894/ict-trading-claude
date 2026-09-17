"""Economic calendar providers behind one interface. No scraping: the file provider reads a server file."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.domain.enums import EventImportance
from app.services.news.models import CalendarFile, CalendarSnapshot, EconomicEvent
from app.services.timeframes.core import NEW_YORK


class CalendarUnavailableError(RuntimeError):
    pass


class CalendarProvider(Protocol):
    name: str
    is_synthetic: bool

    async def snapshot(self, now: datetime) -> CalendarSnapshot: ...


class UnconfiguredCalendar:
    name = "unconfigured"
    is_synthetic = False

    async def snapshot(self, now: datetime) -> CalendarSnapshot:
        raise CalendarUnavailableError("no economic calendar provider is configured (CALENDAR_PROVIDER)")


class FileCalendar:
    """Reads CALENDAR_FILE_PATH; re-reads when the file changes. Errors list field paths only."""

    name = "file"
    is_synthetic = False

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None
        self._key: tuple[int, int] | None = None
        self._cached: CalendarFile | None = None
        self._error: str | None = None

    async def snapshot(self, now: datetime) -> CalendarSnapshot:
        if self._path is None:
            raise CalendarUnavailableError("CALENDAR_FILE_PATH is not set")
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise CalendarUnavailableError("the calendar file does not exist") from exc
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._key:
            self._key, self._cached, self._error = key, None, None
            try:
                self._cached = CalendarFile.model_validate(json.loads(self._path.read_text(encoding="utf-8")))
            except ValidationError as exc:
                self._error = "; ".join(
                    ".".join(str(p) for p in e["loc"]) + f": {e['msg']}"
                    for e in exc.errors(include_input=False)
                )
            except (OSError, ValueError) as exc:
                self._error = f"unreadable calendar file ({type(exc).__name__})"
        if self._cached is None:
            raise CalendarUnavailableError(f"invalid calendar file: {self._error}")
        f = self._cached
        return CalendarSnapshot(
            provider=self.name,
            source=f.source,
            is_synthetic=False,
            fetched_at=f.fetched_at,
            coverage_start=f.coverage_start,
            coverage_end=f.coverage_end,
            events=tuple(f.events),
        )


# (currency, country, New York time, importance, weekdays Mon=0.., label)
_SCHEDULE: tuple[tuple[str, str, time, EventImportance, tuple[int, ...], str], ...] = (
    ("USD", "United States", time(8, 30), EventImportance.HIGH, (1, 3), "SYNTHETIC US high-impact release"),
    ("USD", "United States", time(10, 0), EventImportance.MEDIUM, (0, 2, 4), "SYNTHETIC US medium release"),
    ("USD", "United States", time(14, 0), EventImportance.EXTREME, (2,), "SYNTHETIC US policy decision"),
    ("EUR", "Euro Area", time(4, 0), EventImportance.HIGH, (1, 4), "SYNTHETIC EU high-impact release"),
    ("GBP", "United Kingdom", time(2, 0), EventImportance.MEDIUM, (2,), "SYNTHETIC UK medium release"),
    ("JPY", "Japan", time(19, 50), EventImportance.MEDIUM, (0, 3), "SYNTHETIC JP medium release"),
    ("USD", "United States", time(15, 0), EventImportance.LOW, (1,), "SYNTHETIC US low-impact release"),
)


class FixtureCalendar:
    """Deterministic SYNTHETIC schedule for development. It can never clear the gate (NEWS_DATA_SYNTHETIC)."""

    name = "fixture"
    is_synthetic = True

    async def snapshot(self, now: datetime) -> CalendarSnapshot:
        start_day = (now - timedelta(days=7)).astimezone(NEW_YORK).date()
        events: list[EconomicEvent] = []
        for offset in range(15):
            day: date = start_day + timedelta(days=offset)
            for currency, country, at, importance, weekdays, label in _SCHEDULE:
                if day.weekday() not in weekdays:
                    continue
                scheduled = datetime.combine(day, at, tzinfo=NEW_YORK).astimezone(UTC)
                events.append(
                    EconomicEvent(
                        id=f"FIXTURE:{currency}:{scheduled.isoformat()}",
                        country=country,
                        currency=currency,
                        name=label,
                        scheduled_time=scheduled,
                        importance=importance,
                        forecast=1.0,
                        previous=0.8,
                        actual=1.2 if scheduled <= now else None,
                    )
                )
        return CalendarSnapshot(
            provider=self.name,
            source="synthetic fixture schedule",
            is_synthetic=True,
            fetched_at=now,
            coverage_start=now - timedelta(days=7),
            coverage_end=now + timedelta(days=7),
            events=tuple(sorted(events, key=lambda e: e.scheduled_time)),
        )


def create_calendar(kind: str, path: str) -> CalendarProvider:
    if kind == "file":
        return FileCalendar(path or None)
    if kind == "fixture":
        return FixtureCalendar()
    return UnconfiguredCalendar()
