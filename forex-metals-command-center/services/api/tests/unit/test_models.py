from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.candle import Candle
from app.domain.enums import DataQuality, Timeframe
from app.domain.quote import Quote

T0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)


def candle(**overrides):
    base = dict(
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open_time=T0,
        close_time=T0 + timedelta(minutes=5),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=None,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )
    base.update(overrides)
    return Candle(**base)


def test_candle_requires_utc():
    with pytest.raises(ValidationError):
        candle(open_time=datetime(2024, 1, 9, 10, 0), close_time=datetime(2024, 1, 9, 10, 5))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        est = timezone(timedelta(hours=-5))
        candle(open_time=T0.astimezone(est), close_time=(T0 + timedelta(minutes=5)).astimezone(est))


def test_candle_close_time_must_match_timeframe():
    with pytest.raises(ValidationError):
        candle(close_time=T0 + timedelta(minutes=15))


def test_candle_is_immutable_and_camel_case():
    c = candle()
    with pytest.raises(ValidationError):
        c.open = 5.0  # type: ignore[misc]
    dumped = c.model_dump(by_alias=True)
    assert {"openTime", "closeTime", "isClosed", "dataQuality"} <= set(dumped)


def test_quote_mid_and_spread():
    q = Quote(
        symbol="XAUUSD", bid=2030.0, ask=2030.4, timestamp=T0, source="t", data_quality=DataQuality.LIVE
    )
    assert q.mid == pytest.approx(2030.2)
    assert q.spread == pytest.approx(0.4)
