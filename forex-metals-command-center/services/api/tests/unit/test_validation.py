import math
from datetime import timedelta

from app.domain.enums import AssetClass, DataQuality, IssueSeverity
from app.domain.enums import ValidationIssueCode as C
from app.domain.quote import Quote
from app.services.candles.normalize import build_series
from app.services.data_quality.validation import (
    compare_providers,
    detect_range_spikes,
    validate_bar_prices,
    validate_quote,
)
from tests.helpers import TUE_10_UTC, after_last, bar, consecutive_bars


def codes(issues):
    return {i.code for i in issues}


def test_clean_bar_has_no_issues():
    assert validate_bar_prices(bar(TUE_10_UTC)) == []


def test_impossible_ohlc_high_below_close():
    issues = validate_bar_prices(bar(TUE_10_UTC, o=10, h=11, low=9, c=12))
    assert codes(issues) == {C.IMPOSSIBLE_OHLC}
    assert issues[0].severity is IssueSeverity.ERROR


def test_impossible_ohlc_low_above_open():
    assert C.IMPOSSIBLE_OHLC in codes(validate_bar_prices(bar(TUE_10_UTC, o=10, h=12, low=10.5, c=11)))


def test_non_positive_price():
    assert C.NON_POSITIVE_PRICE in codes(validate_bar_prices(bar(TUE_10_UTC, o=0, h=1, low=0, c=1)))
    assert C.NON_POSITIVE_PRICE in codes(validate_bar_prices(bar(TUE_10_UTC, o=-1, h=1, low=-2, c=0.5)))


def test_zero_range_is_warning_not_error():
    issues = validate_bar_prices(bar(TUE_10_UTC, o=10, h=10, low=10, c=10))
    assert codes(issues) == {C.ZERO_RANGE}
    assert issues[0].severity is IssueSeverity.WARNING


def test_non_finite_and_negative_volume():
    assert codes(validate_bar_prices(bar(TUE_10_UTC, h=math.nan))) == {C.NON_FINITE_VALUE}
    assert codes(validate_bar_prices(bar(TUE_10_UTC, volume=-1))) == {C.NEGATIVE_VOLUME}


def _series(raw):
    return build_series(
        raw,
        symbol="XAUUSD",
        timeframe=raw[0].timeframe,
        source="t",
        asset_class=AssetClass.METAL,
        now=after_last(raw),
    )


def test_range_spike_flagged_without_lookahead():
    raw = consecutive_bars(TUE_10_UTC, 30)
    t = raw[25].open_time
    raw[25] = bar(t, 2030, 2100, 2029, 2090)  # 71-point range vs 2-point median
    series = _series(raw)
    spikes = [i for i in series.issues if i.code is C.SUSPECT_BAD_TICK]
    assert [i.at for i in spikes] == [t]
    assert spikes[0].severity is IssueSeverity.WARNING


def test_spike_detection_ignores_future_candles():
    # A spike judged at index i must not change when later candles are appended.
    raw = consecutive_bars(TUE_10_UTC, 30)
    first = detect_range_spikes(_series(raw[:20]).candles)
    later = [i for i in detect_range_spikes(_series(raw).candles) if i.at <= raw[19].open_time]
    assert first == later


def test_spike_needs_minimum_samples():
    raw = consecutive_bars(TUE_10_UTC, 5)
    raw[4] = bar(raw[4].open_time, 2030, 2300, 2029, 2200)
    assert C.SUSPECT_BAD_TICK not in codes(_series(raw).issues)


def quote(bid, ask, source="a", seconds=0):
    return Quote(
        symbol="XAUUSD",
        bid=bid,
        ask=ask,
        timestamp=TUE_10_UTC + timedelta(seconds=seconds),
        source=source,
        data_quality=DataQuality.LIVE,
    )


def test_crossed_quote_is_error():
    issues = validate_quote(quote(2030.5, 2030.0), AssetClass.METAL)
    assert codes(issues) == {C.CROSSED_QUOTE}


def test_wide_spread_warning():
    issues = validate_quote(quote(2030.0, 2032.0), AssetClass.METAL, max_spread=0.5)
    assert codes(issues) == {C.WIDE_SPREAD}


def test_bad_tick_jump_vs_reference():
    assert validate_quote(quote(2030.0, 2030.4), AssetClass.METAL, reference_price=2031.0) == []
    issues = validate_quote(quote(2080.0, 2080.4), AssetClass.METAL, reference_price=2030.0)  # ~2.5%
    assert codes(issues) == {C.SUSPECT_BAD_TICK}
    assert issues[0].severity is IssueSeverity.ERROR


def test_fx_bad_tick_threshold_is_tighter():
    q = Quote(
        symbol="EURUSD",
        bid=1.1066,
        ask=1.1067,
        timestamp=TUE_10_UTC,
        source="a",
        data_quality=DataQuality.LIVE,
    )
    assert codes(validate_quote(q, AssetClass.FX_MAJOR, reference_price=1.1000)) == {C.SUSPECT_BAD_TICK}


def test_provider_disagreement():
    assert compare_providers(quote(2030.0, 2030.4), quote(2030.1, 2030.5, "b"), AssetClass.METAL) == []
    issues = compare_providers(quote(2030.0, 2030.4), quote(2036.0, 2036.4, "b"), AssetClass.METAL)
    assert codes(issues) == {C.PROVIDER_DISAGREEMENT}
