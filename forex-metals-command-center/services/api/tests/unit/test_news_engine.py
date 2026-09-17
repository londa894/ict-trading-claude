"""News gate engine, calendar models and providers (Phase 13)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.enums import Blocker, EventImportance, EventStatus, NewsState
from app.domain.instrument import get_instrument
from app.services.news import models as news_models
from app.services.news.engine import assess, derive_status, parse_number, relevant_currencies, view
from app.services.news.models import CalendarFile, CalendarSnapshot, EconomicEvent, NewsConfig
from app.services.news.providers import (
    CalendarUnavailableError,
    FileCalendar,
    FixtureCalendar,
    UnconfiguredCalendar,
    create_calendar,
)

CFG = NewsConfig.from_spec()
NOW = datetime(2024, 4, 18, 12, 0, tzinfo=UTC)  # Thursday 08:00 New York
GOLD = get_instrument("XAUUSD")
EURUSD = get_instrument("EURUSD")


def event(minutes: float, importance="HIGH", currency="USD", **over) -> EconomicEvent:
    base = dict(
        id=f"E{currency}{importance}{minutes}",
        country="Testland",
        currency=currency,
        name="Test release",
        scheduled_time=NOW + timedelta(minutes=minutes),
        importance=importance,
    )
    base.update(over)
    return EconomicEvent(**base)


def snapshot(
    *events,
    synthetic=False,
    fetched=NOW - timedelta(hours=1),
    start=NOW - timedelta(days=1),
    end=NOW + timedelta(days=2),
):
    return CalendarSnapshot("file", "test", synthetic, fetched, start, end, tuple(events))


def state(*events, instrument=GOLD, **kw):
    return assess(instrument, NOW, snapshot(*events, **kw), None, CFG, "file")


@pytest.mark.parametrize(
    ("minutes", "importance", "expected"),
    [
        (200, "HIGH", NewsState.CLEAR),
        (59, "HIGH", NewsState.CAUTION),  # caution lead 60 min
        (15, "HIGH", NewsState.BLACKOUT),  # blackout 15 before
        (-14, "HIGH", NewsState.BLACKOUT),
        (-15, "HIGH", NewsState.POST_NEWS_WAIT),  # blackout ends 15 after, post wait 10
        (-25, "HIGH", NewsState.NORMALIZED),
        (-56, "HIGH", NewsState.CLEAR),
        (30, "EXTREME", NewsState.BLACKOUT),  # 30 before
        (119, "EXTREME", NewsState.CAUTION),  # caution lead 120
        (-45, "EXTREME", NewsState.NORMALIZED),  # 30 after + 15 post
        (14, "MEDIUM", NewsState.CAUTION),  # MEDIUM never blacks out
        (-14, "MEDIUM", NewsState.CAUTION),
        (5, "LOW", NewsState.CLEAR),
    ],
)
def test_windows(minutes, importance, expected):
    assert state(event(minutes, importance)).state is expected


def test_most_severe_event_wins_and_blockers():
    a = state(event(50, "HIGH"), event(10, "EXTREME"), event(-20, "HIGH"))
    assert a.state is NewsState.BLACKOUT and a.blockers == [Blocker.NEWS_BLACKOUT]
    assert a.active_event.importance is EventImportance.EXTREME and a.window_end == NOW + timedelta(
        minutes=40
    )
    post = state(event(-16, "HIGH"))
    assert post.state is NewsState.POST_NEWS_WAIT and post.blockers == [Blocker.NEWS_POST_WAIT]


def test_relevant_currencies_per_asset_class():
    assert relevant_currencies(GOLD, CFG) == ["USD"]
    assert relevant_currencies(EURUSD, CFG) == ["EUR", "USD"]
    assert state(event(0, currency="EUR")).state is NewsState.CLEAR  # EUR news does not gate gold
    assert state(event(0, currency="EUR"), instrument=EURUSD).state is NewsState.BLACKOUT


def test_cancelled_and_delayed_events():
    assert state(event(0, status="CANCELLED")).state is NewsState.CLEAR
    delayed = state(
        event(-60, status="DELAYED")
    )  # delayed blackout lasts up to 120 min after the scheduled time
    assert delayed.state is NewsState.BLACKOUT and delayed.active_event.status is EventStatus.DELAYED


def test_calendar_that_cannot_prove_clear_is_unavailable():
    for kw, fragment in (
        ({"fetched": NOW - timedelta(hours=25)}, "fetched more than 24 h ago"),
        ({"fetched": NOW + timedelta(hours=1)}, "in the future"),
        ({"end": NOW + timedelta(hours=10)}, "next 24 h"),
        ({"start": NOW - timedelta(minutes=30)}, "recent past"),
    ):
        a = state(event(500), **kw)
        assert a.state is NewsState.UNAVAILABLE and a.blockers == [Blocker.NEWS_DATA_UNAVAILABLE]
        assert fragment in a.calendar.reason and a.events == []
    missing = assess(GOLD, NOW, None, "no economic calendar provider is configured", CFG, "unconfigured")
    assert missing.state is NewsState.UNAVAILABLE and missing.calendar.provider == "unconfigured"


def test_synthetic_calendar_never_clears_the_gate():
    a = state(event(500), synthetic=True)
    assert a.state is NewsState.CLEAR and a.blockers == [Blocker.NEWS_DATA_SYNTHETIC]
    assert "Synthetic calendar" in a.warnings[-1]


def test_event_views_statuses_surprise_and_lists():
    assert parse_number("0.3%") == 0.3 and parse_number("250K") == 250_000 and parse_number("n/a") is None
    released = view(event(-5, actual="0.5%", forecast="0.3%"), NOW, CFG)
    assert (
        released.status is EventStatus.RELEASED
        and released.surprise == 0.2
        and released.surprise_pct == 66.67
    )
    assert derive_status(event(10), NOW, CFG) is EventStatus.IMMINENT
    assert derive_status(event(60), NOW, CFG) is EventStatus.UPCOMING
    assert derive_status(event(-300), NOW, CFG) is EventStatus.COMPLETED
    assert view(event(30), NOW, CFG).blackout_start == NOW + timedelta(minutes=15)
    a = state(event(-400), event(-60), event(600, "LOW"), event(2000))
    assert [round(e.minutes_to_event) for e in a.events] == [-60, 600]  # 6 h back, 24 h ahead
    assert a.next_event.minutes_to_event == 2000.0  # LOW events are never the next gating event


def test_calendar_file_validation(tmp_path):
    good = {
        "source": "test",
        "fetchedAt": "2024-04-18T11:00:00Z",
        "coverageStart": "2024-04-17T00:00:00Z",
        "coverageEnd": "2024-04-20T00:00:00Z",
        "events": [event(30).model_dump(mode="json", by_alias=True)],
    }
    assert CalendarFile.model_validate(good).events[0].importance is EventImportance.HIGH
    for bad in (
        {**good, "coverageEnd": "2024-04-16T00:00:00Z"},
        {**good, "events": good["events"] * 2},
        {**good, "events": [{**good["events"][0], "currency": "usd"}]},
        {**good, "events": [{**good["events"][0], "scheduledTime": "2024-04-18T12:00:00"}]},
    ):
        with pytest.raises(ValidationError):
            CalendarFile.model_validate(bad)


async def test_providers(tmp_path):
    with pytest.raises(CalendarUnavailableError, match="CALENDAR_PROVIDER"):
        await UnconfiguredCalendar().snapshot(NOW)
    with pytest.raises(CalendarUnavailableError, match="not set"):
        await FileCalendar(None).snapshot(NOW)
    path = tmp_path / "cal.json"
    with pytest.raises(CalendarUnavailableError, match="does not exist"):
        await FileCalendar(path).snapshot(NOW)
    path.write_text(json.dumps({"source": "x", "events": []}), encoding="utf-8")
    with pytest.raises(CalendarUnavailableError, match="fetchedAt"):
        await FileCalendar(path).snapshot(NOW)
    fixture = await FixtureCalendar().snapshot(NOW)
    assert fixture.is_synthetic and all(e.name.startswith("SYNTHETIC") for e in fixture.events)
    assert len({e.currency for e in fixture.events}) >= 3
    assert isinstance(create_calendar("fixture", ""), FixtureCalendar)
    assert isinstance(create_calendar("file", "x.json"), FileCalendar)
    assert isinstance(create_calendar("unconfigured", ""), UnconfiguredCalendar)


def test_config_guards(monkeypatch):
    spec = news_models.load_spec("news")
    monkeypatch.setattr(
        news_models,
        "load_spec",
        lambda _n: {**spec, "windows": {**spec["windows"], "LOW": spec["windows"]["MEDIUM"]}},
    )
    with pytest.raises(ValueError, match="LOW"):
        NewsConfig.from_spec()


def test_committed_example_calendar_is_valid():
    path = Path(__file__).resolve().parents[4] / "config" / "calendar.example.json"
    example = CalendarFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert {e.importance for e in example.events} >= {EventImportance.HIGH, EventImportance.EXTREME}
