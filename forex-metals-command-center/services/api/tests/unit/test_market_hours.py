from datetime import UTC, datetime

import pytest

from app.domain.enums import AssetClass, MarketStatus
from app.services.data_quality.market_hours import market_status_at

M, FX = AssetClass.METAL, AssetClass.FX_MAJOR


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("asset", "instant", "expected"),
    [
        # Winter (EST, UTC-5): metals open Sunday 18:00 NY = 23:00 UTC
        (M, utc(2024, 1, 7, 22, 59), MarketStatus.CLOSED),
        (M, utc(2024, 1, 7, 23, 0), MarketStatus.OPEN),
        # Summer (EDT, UTC-4): metals open Sunday 18:00 NY = 22:00 UTC
        (M, utc(2024, 7, 7, 21, 59), MarketStatus.CLOSED),
        (M, utc(2024, 7, 7, 22, 0), MarketStatus.OPEN),
        # FX opens Sunday 17:00 NY: 22:00 UTC winter, 21:00 UTC summer
        (FX, utc(2024, 1, 7, 22, 0), MarketStatus.OPEN),
        (FX, utc(2024, 1, 7, 21, 59), MarketStatus.CLOSED),
        (FX, utc(2024, 7, 7, 21, 0), MarketStatus.OPEN),
        # Friday close 17:00 NY
        (M, utc(2024, 1, 12, 21, 59), MarketStatus.OPEN),
        (M, utc(2024, 1, 12, 22, 0), MarketStatus.CLOSED),
        (FX, utc(2024, 7, 12, 21, 0), MarketStatus.CLOSED),
        # Saturday always closed
        (M, utc(2024, 1, 13, 12, 0), MarketStatus.CLOSED),
        (FX, utc(2024, 1, 13, 12, 0), MarketStatus.CLOSED),
        # Metals daily break 17:00-18:00 NY, Tuesday
        (M, utc(2024, 1, 9, 22, 0), MarketStatus.DAILY_BREAK),
        (M, utc(2024, 1, 9, 22, 59), MarketStatus.DAILY_BREAK),
        (M, utc(2024, 1, 9, 23, 0), MarketStatus.OPEN),
        (M, utc(2024, 7, 9, 21, 0), MarketStatus.DAILY_BREAK),
        # FX has no daily break
        (FX, utc(2024, 1, 9, 22, 30), MarketStatus.OPEN),
    ],
)
def test_market_status_dst_aware(asset, instant, expected):
    assert market_status_at(asset, instant) is expected


def test_dst_transition_sunday_2024_03_10():
    # US DST starts 2024-03-10 02:00 local; that Sunday's 18:00 NY open is already EDT = 22:00 UTC.
    assert market_status_at(M, utc(2024, 3, 10, 21, 59)) is MarketStatus.CLOSED
    assert market_status_at(M, utc(2024, 3, 10, 22, 0)) is MarketStatus.OPEN


def test_naive_instant_rejected():
    with pytest.raises(ValueError):
        market_status_at(M, datetime(2024, 1, 9, 10, 0))  # noqa: DTZ001
