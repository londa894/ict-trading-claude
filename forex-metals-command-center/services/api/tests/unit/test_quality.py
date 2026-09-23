from datetime import UTC, datetime, timedelta

from app.domain.enums import AssetClass, DataQuality, Timeframe
from app.services.candles.normalize import build_series
from app.services.data_quality.quality import classify_quote_age, worst_quality
from tests.helpers import TUE_10_UTC, bar, consecutive_bars


def test_worst_quality_wins():
    assert worst_quality([DataQuality.LIVE, DataQuality.STALE, DataQuality.CURRENT]) is DataQuality.STALE
    assert worst_quality([DataQuality.CURRENT, DataQuality.INVALID]) is DataQuality.INVALID
    assert worst_quality([]) is DataQuality.INVALID


def test_quote_age_classification():
    t = TUE_10_UTC
    assert classify_quote_age(t, t + timedelta(seconds=3)) is DataQuality.LIVE
    assert classify_quote_age(t, t + timedelta(seconds=30)) is DataQuality.CURRENT
    assert classify_quote_age(t, t + timedelta(minutes=10)) is DataQuality.DELAYED
    assert classify_quote_age(t, t + timedelta(hours=1)) is DataQuality.STALE
    assert classify_quote_age(t + timedelta(minutes=5), t) is DataQuality.INVALID  # future timestamp


def quality_at(raw, now, tf=Timeframe.M5):
    return build_series(
        raw, symbol="XAUUSD", timeframe=tf, source="t", asset_class=AssetClass.METAL, now=now
    ).quality


def test_candle_series_current_delayed_stale():
    raw = consecutive_bars(TUE_10_UTC, 20)
    last_close = raw[-1].open_time + timedelta(minutes=5)
    assert quality_at(raw, last_close + timedelta(seconds=10)) is DataQuality.CURRENT
    # one full expected bar missing (+grace) -> DELAYED
    assert quality_at(raw, last_close + timedelta(minutes=5, seconds=20)) is DataQuality.DELAYED
    assert quality_at(raw, last_close + timedelta(minutes=10, seconds=20)) is DataQuality.DELAYED
    # three missing -> STALE
    assert quality_at(raw, last_close + timedelta(minutes=15, seconds=20)) is DataQuality.STALE


def test_weekend_does_not_make_complete_data_stale():
    last = datetime(2024, 1, 12, 21, 55, tzinfo=UTC)  # final M5 bar before Friday 17:00 NY close
    raw = [bar(last - timedelta(minutes=5 * i)) for i in range(10)][::-1]
    saturday = datetime(2024, 1, 13, 15, 0, tzinfo=UTC)
    assert quality_at(raw, saturday) is DataQuality.CURRENT
    monday = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
    assert quality_at(raw, monday) is DataQuality.STALE


def test_d1_staleness_counts_ny_close_trading_days_with_weekend_credit():
    # Phase 1 rule: D1 buckets are New York 17:00 trading days. Friday 2024-01-12's trading day
    # opens Thu 17:00 EST = Thu 22:00 UTC and closes Fri 22:00 UTC.
    raw = [bar(datetime(2024, 1, 11, 22, 0, tzinfo=UTC), tf=Timeframe.D1)]
    d1 = Timeframe.D1
    assert quality_at(raw, datetime(2024, 1, 14, 12, 0, tzinfo=UTC), tf=d1) is DataQuality.CURRENT  # Sunday
    assert (
        quality_at(raw, datetime(2024, 1, 15, 23, 0, tzinfo=UTC), tf=d1) is DataQuality.DELAYED
    )  # Mon missing
    assert (
        quality_at(raw, datetime(2024, 1, 17, 23, 0, tzinfo=UTC), tf=d1) is DataQuality.STALE
    )  # Mon-Wed missing


def test_d1_bar_on_utc_midnight_convention_is_invalid():
    raw = [bar(datetime(2024, 1, 12, 0, 0, tzinfo=UTC), tf=Timeframe.D1)]
    assert quality_at(raw, datetime(2024, 1, 13, 6, 0, tzinfo=UTC), tf=Timeframe.D1) is DataQuality.INVALID


def test_weekly_timeframe_is_supported_and_alignment_enforced():
    # W1 is now aggregated by the candle engine (Step 4): an aligned, recent weekly bar is CURRENT.
    aligned = [bar(datetime(2024, 1, 7, 22, 0, tzinfo=UTC), tf=Timeframe.W1)]  # Sun 17:00 EST open
    assert (
        quality_at(aligned, datetime(2024, 1, 20, 6, 0, tzinfo=UTC), tf=Timeframe.W1) is DataQuality.CURRENT
    )
    # A weekly bar that does not sit on the Sunday open still fails safe.
    misaligned = [bar(datetime(2024, 1, 8, 22, 0, tzinfo=UTC), tf=Timeframe.W1)]  # Monday, not a week open
    assert (
        quality_at(misaligned, datetime(2024, 1, 20, 6, 0, tzinfo=UTC), tf=Timeframe.W1)
        is DataQuality.INVALID
    )
