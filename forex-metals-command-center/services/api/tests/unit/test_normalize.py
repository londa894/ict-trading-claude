from datetime import UTC, datetime, timedelta, timezone

from app.domain.enums import AssetClass, DataQuality, IssueSeverity, Timeframe
from app.domain.enums import ValidationIssueCode as C
from app.services.candles.normalize import build_series
from tests.helpers import TUE_10_UTC, after_last, bar, consecutive_bars


def build(raw, now=None, tf=Timeframe.M5, asset=AssetClass.METAL, symbol="XAUUSD"):
    return build_series(
        raw, symbol=symbol, timeframe=tf, source="t", asset_class=asset, now=now or after_last(raw)
    )


def codes(series):
    return {i.code for i in series.issues}


def test_clean_series_is_current_and_sorted():
    raw = consecutive_bars(TUE_10_UTC, 50)
    s = build(raw)
    assert s.issues == []
    assert s.quality is DataQuality.CURRENT
    assert len(s.candles) == 50
    assert all(c.data_quality is DataQuality.CURRENT for c in s.candles)
    assert all(b.open_time > a.open_time for a, b in zip(s.candles, s.candles[1:], strict=False))
    assert all(c.close_time - c.open_time == timedelta(minutes=5) for c in s.candles)


def test_empty_series_invalid():
    s = build_series(
        [], symbol="XAUUSD", timeframe=Timeframe.M5, source="t", asset_class=AssetClass.METAL, now=TUE_10_UTC
    )
    assert s.quality is DataQuality.INVALID
    assert codes(s) == {C.EMPTY_SERIES}


def test_identical_duplicate_dropped_with_warning():
    raw = consecutive_bars(TUE_10_UTC, 10)
    raw.insert(5, raw[4])
    s = build(raw)
    assert len(s.candles) == 10
    assert codes(s) == {C.DUPLICATE_IDENTICAL}
    assert s.quality is DataQuality.CURRENT


def test_conflicting_duplicate_excludes_both_and_invalidates():
    raw = consecutive_bars(TUE_10_UTC, 10)
    t = raw[4].open_time
    raw.insert(5, bar(t, 2030, 2035, 2029, 2034))
    s = build(raw)
    assert all(c.open_time != t for c in s.candles)
    assert C.DUPLICATE_CONFLICT in codes(s)
    assert s.quality is DataQuality.INVALID


def test_out_of_order_resorted_with_warning():
    raw = consecutive_bars(TUE_10_UTC, 10)
    raw[2], raw[7] = raw[7], raw[2]
    s = build(raw)
    assert codes(s) == {C.OUT_OF_ORDER}
    assert [c.open_time for c in s.candles] == sorted(c.open_time for c in s.candles)
    assert s.quality is DataQuality.CURRENT


def test_naive_timestamp_rejected_never_assumed():
    raw = consecutive_bars(TUE_10_UTC, 5)
    raw.append(bar(datetime(2024, 1, 9, 10, 25)))  # noqa: DTZ001
    s = build(raw, now=after_last(raw[:5]))
    assert C.NON_UTC_TIMESTAMP in codes(s)
    assert s.quality is DataQuality.INVALID


def test_aware_non_utc_timestamp_is_converted():
    est = timezone(timedelta(hours=-5))
    raw = [bar(TUE_10_UTC.astimezone(est))]
    s = build(raw, now=TUE_10_UTC + timedelta(minutes=6))
    assert s.candles[0].open_time == TUE_10_UTC
    assert s.candles[0].open_time.tzinfo == UTC


def test_misaligned_open_time():
    raw = [bar(TUE_10_UTC + timedelta(minutes=2))]
    s = build(raw, now=TUE_10_UTC + timedelta(minutes=10))
    assert C.MISALIGNED_OPEN_TIME in codes(s)
    assert s.quality is DataQuality.INVALID


def test_symbol_and_timeframe_mismatch():
    raw = [*consecutive_bars(TUE_10_UTC, 3), bar(TUE_10_UTC + timedelta(minutes=15), symbol="XAGUSD")]
    assert C.SYMBOL_MISMATCH in codes(build(raw, now=after_last(raw[:3])))
    raw2 = [*consecutive_bars(TUE_10_UTC, 3), bar(TUE_10_UTC, tf=Timeframe.M15)]
    assert C.TIMEFRAME_MISMATCH in codes(build(raw2, now=after_last(raw2[:3])))


def test_invalid_ohlc_bar_invalidates_series():
    raw = consecutive_bars(TUE_10_UTC, 5)
    raw[2] = bar(raw[2].open_time, o=2030, h=2029, low=2028, c=2030)
    s = build(raw)
    assert C.IMPOSSIBLE_OHLC in codes(s)
    assert s.quality is DataQuality.INVALID


def test_missing_bars_in_market_hours_reported_not_filled():
    raw = consecutive_bars(TUE_10_UTC, 20)
    del raw[8:11]
    s = build(raw)
    gaps = [i for i in s.issues if i.code is C.MISSING_BARS]
    assert len(gaps) == 1
    assert gaps[0].count == 3
    assert gaps[0].severity is IssueSeverity.WARNING
    assert gaps[0].at == TUE_10_UTC + timedelta(minutes=40)
    assert len(s.candles) == 17  # nothing fabricated


def test_weekend_is_not_a_gap():
    fri_close = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)
    sun_open = datetime(2024, 1, 14, 23, 0, tzinfo=UTC)
    raw = [bar(fri_close - timedelta(minutes=5)), bar(sun_open)]
    s = build(raw, now=sun_open + timedelta(minutes=5, seconds=20))
    assert C.MISSING_BARS not in codes(s)


def test_metals_daily_break_is_not_a_gap_but_fx_gap_is():
    brk_start = datetime(2024, 1, 9, 22, 0, tzinfo=UTC)  # 17:00 NY
    brk_end = datetime(2024, 1, 9, 23, 0, tzinfo=UTC)
    raw = [bar(brk_start - timedelta(minutes=5)), bar(brk_end)]
    assert C.MISSING_BARS not in codes(build(raw, now=brk_end + timedelta(minutes=6)))

    fx = [
        bar(brk_start - timedelta(minutes=5), 1.1, 1.101, 1.099, 1.1005, symbol="EURUSD"),
        bar(brk_end, 1.1, 1.101, 1.099, 1.1005, symbol="EURUSD"),
    ]
    s = build(fx, now=brk_end + timedelta(minutes=6), asset=AssetClass.FX_MAJOR, symbol="EURUSD")
    gaps = [i for i in s.issues if i.code is C.MISSING_BARS]
    assert gaps and gaps[0].count == 12


def test_forming_last_candle_not_closed():
    raw = consecutive_bars(TUE_10_UTC, 10)
    now = raw[-1].open_time + timedelta(minutes=2)
    s = build(raw, now=now)
    assert s.candles[-1].is_closed is False
    assert all(c.is_closed for c in s.candles[:-1])
    assert s.quality is DataQuality.CURRENT


def test_future_bars_are_invalid():
    raw = consecutive_bars(TUE_10_UTC, 10)
    s = build(raw, now=raw[5].open_time - timedelta(seconds=1))
    assert C.NON_UTC_TIMESTAMP in codes(s)
    assert s.quality is DataQuality.INVALID
