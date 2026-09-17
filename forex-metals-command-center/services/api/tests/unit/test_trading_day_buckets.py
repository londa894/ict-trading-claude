"""New York 17:00 (NY close) H4/D1 bucket rules, DST-aware."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import AssetClass, Timeframe
from app.domain.enums import ValidationIssueCode as C
from app.services.candles.normalize import build_series
from app.services.timeframes.aggregate import aggregate
from app.services.timeframes.core import (
    UnsupportedTimeframeError,
    bucket_start,
    expected_slots,
    is_aligned,
    next_bucket_start,
    trading_day_start,
)
from tests.helpers import consecutive_bars

H4, D1, H1 = Timeframe.H4, Timeframe.D1, Timeframe.H1


def utc(*a):
    return datetime(*a, tzinfo=UTC)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        # Winter (EST): roll at 22:00 UTC
        (utc(2024, 1, 9, 21, 59), utc(2024, 1, 8, 22, 0)),
        (utc(2024, 1, 9, 22, 0), utc(2024, 1, 9, 22, 0)),
        (utc(2024, 1, 10, 3, 0), utc(2024, 1, 9, 22, 0)),
        # Summer (EDT): roll at 21:00 UTC
        (utc(2024, 7, 9, 20, 59), utc(2024, 7, 8, 21, 0)),
        (utc(2024, 7, 9, 21, 0), utc(2024, 7, 9, 21, 0)),
    ],
)
def test_trading_day_start(instant, expected):
    assert trading_day_start(instant) == expected
    assert bucket_start(instant, D1) == expected


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (utc(2024, 1, 9, 22, 0), utc(2024, 1, 9, 22, 0)),  # 17:00 EST
        (utc(2024, 1, 10, 1, 59), utc(2024, 1, 9, 22, 0)),
        (utc(2024, 1, 10, 2, 0), utc(2024, 1, 10, 2, 0)),  # 21:00 EST
        (utc(2024, 1, 10, 14, 30), utc(2024, 1, 10, 14, 0)),  # 09:00 EST bucket
        (utc(2024, 7, 10, 13, 30), utc(2024, 7, 10, 13, 0)),  # 09:00 EDT bucket
    ],
)
def test_h4_buckets_follow_ny_wall_clock(instant, expected):
    assert bucket_start(instant, H4) == expected


def test_h4_local_bucket_hours_are_identical_across_dst():
    for day in (utc(2024, 3, 5, 12), utc(2024, 3, 12, 12)):  # week before / after 2024-03-10 DST
        start = trading_day_start(day)
        hours = []
        cursor = start
        for _ in range(6):
            hours.append(cursor.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).hour)
            cursor = next_bucket_start(cursor, H4)
        assert hours == [17, 21, 1, 5, 9, 13]
        assert cursor - start == timedelta(hours=24)


def test_next_bucket_across_dst_weekend_realigns():
    friday_last_h4 = utc(2024, 3, 8, 18, 0)  # 13:00 EST
    assert bucket_start(friday_last_h4, H4) == friday_last_h4
    slots = list(expected_slots(AssetClass.METAL, H4, friday_last_h4, utc(2024, 3, 11, 6, 0)))
    # First open bucket after the weekend is Sunday 17:00 EDT = 21:00 UTC (metals open 18:00 inside it).
    assert slots[0] == utc(2024, 3, 10, 21, 0)
    assert all(is_aligned(s, H4) for s in slots)


def test_alignment_rules():
    assert is_aligned(utc(2024, 1, 9, 22, 0), D1)
    assert not is_aligned(utc(2024, 1, 10, 0, 0), D1)  # UTC-midnight daily convention rejected
    assert not is_aligned(utc(2024, 1, 10, 0, 0), H4)
    assert not is_aligned(utc(2024, 1, 7, 22, 0), Timeframe.W1)
    with pytest.raises(UnsupportedTimeframeError):
        bucket_start(utc(2024, 1, 9), Timeframe.W1)


def test_d1_expected_slots_skip_weekend():
    thu = utc(2024, 1, 10, 22, 0)  # Thursday trading day
    slots = list(expected_slots(AssetClass.FX_MAJOR, D1, thu, utc(2024, 1, 17, 0, 0)))
    # Fri, Mon, Tue trading days (Sat/Sun buckets contain no open market time)
    assert slots == [utc(2024, 1, 11, 22), utc(2024, 1, 14, 22), utc(2024, 1, 15, 22)]


def _h1_series(start, count, now=None):
    raw = consecutive_bars(start, count, tf=H1)
    now = now or raw[-1].open_time + timedelta(hours=1, seconds=30)
    return build_series(raw, symbol="XAUUSD", timeframe=H1, source="t", asset_class=AssetClass.METAL, now=now)


def test_h1_to_d1_metals_trading_day_is_complete_despite_daily_break():
    # Metals trading day Wed: Tue 18:00 EST (23:00 UTC) .. Wed 17:00 EST (22:00 UTC) = 23 open hours.
    s = _h1_series(utc(2024, 1, 9, 23, 0), 23)
    out, issues = aggregate(s.candles, H1, D1, AssetClass.METAL, s.candles[-1].close_time)
    assert issues == []
    assert len(out) == 1
    d = out[0]
    assert d.open_time == utc(2024, 1, 9, 22, 0) and d.close_time == utc(2024, 1, 10, 22, 0)
    assert d.is_closed
    assert d.high == max(c.high for c in s.candles) and d.low == min(c.low for c in s.candles)
    assert d.open == s.candles[0].open and d.close == s.candles[-1].close


def test_h1_to_h4_counts_and_gap_marking():
    s = _h1_series(utc(2024, 1, 9, 23, 0), 23)
    out, issues = aggregate(s.candles, H1, H4, AssetClass.METAL, s.candles[-1].close_time)
    assert [c.open_time.hour for c in out] == [22, 2, 6, 10, 14, 18]
    assert all(c.is_closed for c in out) and issues == []

    candles = [c for c in s.candles if c.open_time != utc(2024, 1, 10, 3, 0)]
    out, issues = aggregate(candles, H1, H4, AssetClass.METAL, s.candles[-1].close_time)
    assert [i.code for i in issues] == [C.INCOMPLETE_BUCKET]
    assert issues[0].at == utc(2024, 1, 10, 2, 0)
    assert out[1].is_closed is False


def test_forming_d1_is_not_closed_and_not_an_issue():
    s = _h1_series(utc(2024, 1, 9, 23, 0), 10)
    out, issues = aggregate(s.candles, H1, D1, AssetClass.METAL, s.candles[-1].close_time)
    assert out[0].is_closed is False
    assert issues == []


def test_dst_sunday_d1_bucket_is_24h():
    # Sunday 2024-03-10: DST began 02:00 local; that evening's 17:00 EDT roll starts Monday's trading day.
    start = utc(2024, 3, 10, 22, 0)  # metals open 18:00 EDT
    s = _h1_series(start, 23)
    out, _ = aggregate(s.candles, H1, D1, AssetClass.METAL, s.candles[-1].close_time)
    assert out[0].open_time == utc(2024, 3, 10, 21, 0)
    assert out[0].close_time - out[0].open_time == timedelta(hours=24)
    assert out[0].is_closed
