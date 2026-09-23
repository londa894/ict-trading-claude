"""Session clock: DST-aware windows, trading day, active windows, time quality, next session, config."""

from datetime import date

import pytest

from app.domain.enums import AssetClass, KillZone, MarketStatus, SessionName, SessionQuality
from app.services.sessions import models as session_models
from app.services.sessions.clock import (
    active_windows,
    best_quality,
    downgrade,
    session_clock,
    time_quality,
    trading_day_of,
    window_bounds,
)
from app.services.sessions.models import SessionConfig
from tests.session_helpers import CFG, utc

METAL = AssetClass.METAL
Q = SessionQuality


@pytest.mark.parametrize(
    ("session", "day", "start", "end"),
    [
        # EST (UTC-5)
        (SessionName.ASIA, date(2024, 3, 8), utc(2024, 3, 8, 1, 0), utc(2024, 3, 8, 5, 0)),
        (SessionName.LONDON, date(2024, 3, 8), utc(2024, 3, 8, 7, 0), utc(2024, 3, 8, 10, 0)),
        # US DST began Sunday 2024-03-10 02:00: Sunday-evening Asia is already EDT (UTC-4)
        (SessionName.ASIA, date(2024, 3, 11), utc(2024, 3, 11, 0, 0), utc(2024, 3, 11, 4, 0)),
        (SessionName.LONDON, date(2024, 3, 11), utc(2024, 3, 11, 6, 0), utc(2024, 3, 11, 9, 0)),
        (SessionName.NY_AM, date(2024, 3, 11), utc(2024, 3, 11, 12, 30), utc(2024, 3, 11, 16, 0)),
        # US DST ended Sunday 2024-11-03: back to EST
        (SessionName.NY_PM, date(2024, 11, 4), utc(2024, 11, 4, 18, 30), utc(2024, 11, 4, 21, 0)),
    ],
)
def test_window_bounds_follow_new_york_dst(session, day, start, end):
    assert window_bounds(CFG.sessions[session], day) == (start, end)


@pytest.mark.parametrize(
    ("instant", "day"),
    [
        (utc(2024, 1, 12, 21, 59), date(2024, 1, 12)),  # Friday 16:59 EST
        (utc(2024, 1, 14, 23, 0), date(2024, 1, 15)),  # Sunday 18:00 EST -> Monday's trading day
        (utc(2024, 1, 15, 22, 0), date(2024, 1, 16)),  # Monday 17:00 EST rolls
        (utc(2024, 3, 11, 21, 0), date(2024, 3, 12)),  # Monday 17:00 EDT rolls
    ],
)
def test_trading_day_rolls_at_new_york_17(instant, day):
    assert trading_day_of(instant) == day


def test_london_time_reflects_the_dst_gap_weeks():
    gap = session_clock(utc(2024, 3, 11, 11, 0), METAL, CFG)  # US on EDT, UK still on GMT
    after = session_clock(utc(2024, 4, 8, 11, 0), METAL, CFG)  # both on summer time
    assert gap.new_york_time.endswith("07:00:00-04:00") and gap.london_time.endswith("11:00:00+00:00")
    assert after.new_york_time.endswith("07:00:00-04:00") and after.london_time.endswith("12:00:00+01:00")
    assert gap.active_kill_zones == after.active_kill_zones == [KillZone.NY_AM_KZ]


@pytest.mark.parametrize(
    ("instant", "sessions", "zones", "quality"),
    [
        (utc(2024, 1, 9, 8, 0), [SessionName.LONDON], [KillZone.LONDON_KZ], Q.IDEAL),  # 03:00 EST
        (utc(2024, 1, 9, 14, 0), [SessionName.NY_AM], [KillZone.NY_AM_KZ], Q.IDEAL),  # 09:00
        (utc(2024, 1, 9, 15, 30), [SessionName.NY_AM, SessionName.LONDON_CLOSE], [], Q.ACCEPTABLE),  # 10:30
        (utc(2024, 1, 9, 19, 0), [SessionName.NY_PM], [KillZone.NY_PM_KZ], Q.ACCEPTABLE),  # 14:00
        (utc(2024, 1, 10, 2, 0), [SessionName.ASIA], [], Q.ACCEPTABLE),  # 21:00 (Asia boosted)
        (utc(2024, 1, 9, 11, 0), [], [], Q.LOW_QUALITY),  # 06:00, outside every window
        (utc(2024, 1, 9, 22, 30), [], [], Q.AVOID),  # 17:30 metals daily break
        (utc(2024, 1, 13, 15, 0), [SessionName.NY_AM, SessionName.LONDON_CLOSE], [], Q.AVOID),  # Saturday
    ],
)
def test_active_windows_and_time_quality(instant, sessions, zones, quality):
    assert active_windows(CFG.sessions, instant) == sessions
    assert active_windows(CFG.kill_zones, instant) == zones
    assert time_quality(instant, METAL, CFG) is quality


def test_window_start_is_inclusive_and_end_exclusive():
    start, end = window_bounds(CFG.sessions[SessionName.LONDON], date(2024, 1, 9))
    assert active_windows(CFG.sessions, start) == [SessionName.LONDON]
    assert active_windows(CFG.sessions, end) == []


def test_next_session_skips_the_weekend():
    clock = session_clock(utc(2024, 1, 12, 21, 30), METAL, CFG)  # Friday 16:30 EST
    assert clock.next_session is SessionName.ASIA
    assert clock.next_session_start == utc(2024, 1, 15, 1, 0)  # Sunday 20:00 EST
    assert clock.market_status is MarketStatus.OPEN and clock.trading_day == date(2024, 1, 12)


def test_quality_helpers():
    assert best_quality([Q.LOW_QUALITY, Q.IDEAL, Q.AVOID]) is Q.IDEAL
    assert best_quality([]) is None
    assert [downgrade(q) for q in (Q.IDEAL, Q.ACCEPTABLE, Q.LOW_QUALITY, Q.AVOID)] == [
        Q.ACCEPTABLE,
        Q.LOW_QUALITY,
        Q.AVOID,
        Q.AVOID,
    ]


@pytest.mark.parametrize(
    ("window", "message"),
    [
        ({"start": "02:10", "end": "05:00"}, "not aligned"),
        ({"start": "16:00", "end": "18:00"}, "crosses the trading-day roll"),
        ({"start": "02:00", "end": "02:00"}, "empty"),
    ],
)
def test_config_rejects_bad_windows(monkeypatch, window, message):
    real = session_models.load_spec

    def patched(name):
        spec = real(name)
        if name == "sessions":
            spec = {**spec, "sessions": {**spec["sessions"], "LONDON": window}}
        return spec

    monkeypatch.setattr(session_models, "load_spec", patched)
    with pytest.raises(ValueError, match=message):
        SessionConfig.from_spec()
