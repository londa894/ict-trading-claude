from datetime import UTC, datetime

import pytest

from app.domain.enums import AssetClass, DataQuality, ProviderHealthStatus, Timeframe
from app.providers.base import MarketDataProvider, ProviderUnavailableError, UnsupportedSymbolError
from app.providers.fixture import SERIES_END, SERIES_START, SyntheticFixtureProvider
from app.providers.registry import (
    ForbiddenProviderError,
    ProviderRegistry,
    UnknownProviderError,
    default_registry,
)
from app.providers.unconfigured import UnconfiguredProvider
from app.services.candles.normalize import build_series
from app.services.data_quality.market_hours import is_market_open


@pytest.mark.parametrize(
    "name", ["tradingview", "TradingView", "trading_view", "my-TradingView-feed", "Trading View"]
)
def test_tradingview_can_never_be_a_data_provider(name):
    reg = ProviderRegistry()
    with pytest.raises(ForbiddenProviderError):
        reg.register(name, SyntheticFixtureProvider)
    with pytest.raises(ForbiddenProviderError):
        default_registry().create(name)


def test_provider_instance_name_is_also_checked():
    class Sneaky(SyntheticFixtureProvider):
        name = "tradingview-proxy"

    reg = ProviderRegistry()
    reg.register("innocent", Sneaky)
    with pytest.raises(ForbiddenProviderError):
        reg.create("innocent")


def test_registry_unknown_and_duplicate():
    reg = default_registry()
    assert reg.names() == ["fixture", "historical_file", "unconfigured"]
    with pytest.raises(UnknownProviderError):
        reg.create("nope")
    with pytest.raises(ValueError):
        reg.register("fixture", SyntheticFixtureProvider)


def test_providers_satisfy_protocol():
    assert isinstance(UnconfiguredProvider(), MarketDataProvider)
    assert isinstance(SyntheticFixtureProvider(), MarketDataProvider)


async def test_unconfigured_provider_fails_safe():
    p = UnconfiguredProvider()
    assert (await p.health_check()).status is ProviderHealthStatus.DOWN
    with pytest.raises(ProviderUnavailableError):
        await p.get_historical_bars("XAUUSD", Timeframe.M5)
    with pytest.raises(ProviderUnavailableError):
        await p.get_latest_quote("XAUUSD")


async def test_fixture_is_deterministic_labelled_synthetic_and_clean():
    p1, p2 = SyntheticFixtureProvider(), SyntheticFixtureProvider()
    a = await p1.get_historical_bars("XAUUSD", Timeframe.M5)
    b = await p2.get_historical_bars("XAUUSD", Timeframe.M5)
    assert a == b and len(a) > 1000
    assert p1.is_synthetic and (await p1.health_check()).is_synthetic
    assert a[0].open_time == SERIES_START
    assert a[-1].open_time + Timeframe.M5.duration == SERIES_END
    assert all(is_market_open(AssetClass.METAL, x.open_time) for x in a)

    series = build_series(
        a,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        source=p1.source,
        asset_class=AssetClass.METAL,
        now=SERIES_END,
    )
    assert series.issues == []
    assert series.quality is DataQuality.CURRENT
    # Against a real clock long after the fixture window it must be STALE.
    later = build_series(
        a,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        source=p1.source,
        asset_class=AssetClass.METAL,
        now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    assert later.quality is DataQuality.STALE


async def test_fixture_rejects_unknown_symbol_and_non_intraday():
    p = SyntheticFixtureProvider()
    with pytest.raises(UnsupportedSymbolError):
        await p.get_historical_bars("BTCUSD", Timeframe.M5)
    assert await p.get_historical_bars("XAUUSD", Timeframe.D1) == []
