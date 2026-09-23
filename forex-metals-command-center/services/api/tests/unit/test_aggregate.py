from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import AssetClass, Timeframe
from app.domain.enums import ValidationIssueCode as C
from app.services.candles.normalize import build_series
from app.services.timeframes.aggregate import AggregationError, aggregate, aggregate_weekly
from app.services.timeframes.core import floor_to_timeframe, is_aligned
from tests.helpers import TUE_10_UTC, after_last, bar, consecutive_bars


def series(raw, now=None, tf=Timeframe.M5):
    return build_series(
        raw,
        symbol="XAUUSD",
        timeframe=tf,
        source="t",
        asset_class=AssetClass.METAL,
        now=now or after_last(raw),
    )


def test_alignment_and_floor():
    assert is_aligned(TUE_10_UTC, Timeframe.H1)
    assert not is_aligned(TUE_10_UTC + timedelta(minutes=5), Timeframe.M15)
    assert floor_to_timeframe(TUE_10_UTC + timedelta(minutes=14), Timeframe.M15) == TUE_10_UTC


def test_m5_to_m15_ohlcv():
    raw = [
        bar(TUE_10_UTC, 10, 12, 9, 11),
        bar(TUE_10_UTC + timedelta(minutes=5), 11, 15, 10, 14),
        bar(TUE_10_UTC + timedelta(minutes=10), 14, 14.5, 8, 9),
    ]
    s = series(raw)
    out, issues = aggregate(s.candles, Timeframe.M5, Timeframe.M15, AssetClass.METAL, after_last(raw))
    assert issues == []
    assert len(out) == 1
    c = out[0]
    assert (c.open, c.high, c.low, c.close, c.volume) == (10, 15, 8, 9, 300)
    assert c.open_time == TUE_10_UTC and c.close_time == TUE_10_UTC + timedelta(minutes=15)
    assert c.is_closed and c.timeframe is Timeframe.M15


def test_incomplete_bucket_not_closed():
    raw = consecutive_bars(TUE_10_UTC, 6)
    del raw[1]
    s = series(raw)
    out, issues = aggregate(s.candles, Timeframe.M5, Timeframe.M15, AssetClass.METAL, after_last(raw))
    assert [c.is_closed for c in out] == [False, True]
    assert [i.code for i in issues] == [C.INCOMPLETE_BUCKET]


def test_forming_bucket_not_closed():
    raw = consecutive_bars(TUE_10_UTC, 2)
    now = after_last(raw)
    s = series(raw, now=now)
    out, _ = aggregate(s.candles, Timeframe.M5, Timeframe.M15, AssetClass.METAL, now)
    assert out[0].is_closed is False


def test_bucket_spanning_daily_break_is_complete_with_open_slots_only():
    # 16:00-17:00 NY is open; H1 bucket at 21:00 UTC complete. 22:00 UTC (17:00 NY) is the break.
    start = datetime(2024, 1, 9, 21, 0, tzinfo=UTC)
    raw = consecutive_bars(start, 12)
    s = series(raw)
    out, issues = aggregate(s.candles, Timeframe.M5, Timeframe.H1, AssetClass.METAL, after_last(raw))
    assert out[0].is_closed and issues == []


def test_weekly_rejects_non_d1_source():
    # aggregate_weekly only accepts D1 candles; the generic aggregate() still refuses W1 as a target.
    s = series(consecutive_bars(TUE_10_UTC, 3))  # M5 candles
    with pytest.raises(AggregationError):
        aggregate_weekly(s.candles, AssetClass.METAL, TUE_10_UTC)


def test_rejects_unsupported_combinations():
    s = series(consecutive_bars(TUE_10_UTC, 3))
    with pytest.raises(AggregationError):
        aggregate(s.candles, Timeframe.M5, Timeframe.W1, AssetClass.METAL, TUE_10_UTC)
    with pytest.raises(AggregationError):
        aggregate(s.candles, Timeframe.H4, Timeframe.D1, AssetClass.METAL, TUE_10_UTC)
    with pytest.raises(AggregationError):
        aggregate(s.candles, Timeframe.M15, Timeframe.M5, AssetClass.METAL, TUE_10_UTC)
    with pytest.raises(AggregationError):
        aggregate(s.candles, Timeframe.M3, Timeframe.M5, AssetClass.METAL, TUE_10_UTC)
    with pytest.raises(AggregationError):
        aggregate(list(reversed(s.candles)), Timeframe.M5, Timeframe.M15, AssetClass.METAL, TUE_10_UTC)
