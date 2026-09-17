"""Deterministic test-data builders. All prices are synthetic test values, not market facts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from app.domain.candle import RawBar
from app.domain.enums import AssetClass, MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import Instrument, get_instrument
from app.domain.quote import Quote
from app.providers.base import ProviderHealth, ProviderUnavailableError
from app.services.timeframes.core import expected_slots

# Tuesday 2024-01-09 10:00 UTC = 05:00 New York (EST). Market open for metals and FX.
TUE_10_UTC = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)


def bar(
    open_time: datetime,
    o: float = 2030.0,
    h: float = 2031.0,
    low: float = 2029.0,
    c: float = 2030.5,
    *,
    symbol: str = "XAUUSD",
    tf: Timeframe = Timeframe.M5,
    volume: float | None = 100.0,
) -> RawBar:
    return RawBar(
        symbol=symbol, timeframe=tf, open_time=open_time, open=o, high=h, low=low, close=c, volume=volume
    )


def consecutive_bars(
    start: datetime,
    count: int,
    *,
    tf: Timeframe = Timeframe.M5,
    symbol: str = "XAUUSD",
    asset_class: AssetClass = AssetClass.METAL,
    price: float = 2030.0,
) -> list[RawBar]:
    """`count` bars on consecutive market-open slots starting at `start` (inclusive)."""
    slots = list(
        expected_slots(asset_class, tf, start - tf.duration, start + tf.duration * count * 400, limit=count)
    )
    out: list[RawBar] = []
    for i, t in enumerate(slots):
        p = price + (i % 7) * 0.4 - 1.2
        out.append(bar(t, p, p + 1.0, p - 1.0, p + 0.3, symbol=symbol, tf=tf))
    return out


def after_last(bars: list[RawBar], seconds: int = 30) -> datetime:
    """A clock just after the last bar closed."""
    return bars[-1].open_time + bars[-1].timeframe.duration + timedelta(seconds=seconds)


class StaticProvider:
    """Non-synthetic in-memory provider used to exercise the pipeline with controlled bars."""

    name = "static-test"
    is_synthetic = False
    source = "static-test"

    def __init__(self, bars: list[RawBar], *, healthy: bool = True, raise_on_bars: Exception | None = None):
        self._bars = bars
        self._healthy = healthy
        self._raise = raise_on_bars

    async def get_latest_quote(self, symbol: str) -> Quote:
        raise ProviderUnavailableError("no quotes")

    async def get_historical_bars(self, symbol, timeframe, start=None, end=None, limit=None) -> list[RawBar]:
        # Ignores start/end/limit on purpose: validation must not rely on providers honouring them.
        if self._raise:
            raise self._raise
        return [b for b in self._bars if b.symbol == symbol and b.timeframe is timeframe]

    async def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        raise ProviderUnavailableError("no stream")
        yield

    async def subscribe_bars(self, symbols, timeframes) -> AsyncIterator[RawBar]:
        for b in self._bars:
            yield b

    async def get_instrument_metadata(self, symbol: str) -> Instrument:
        inst = get_instrument(symbol)
        assert inst is not None
        return inst

    async def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus.UNKNOWN

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            status=ProviderHealthStatus.HEALTHY if self._healthy else ProviderHealthStatus.DOWN,
            checked_at=datetime.now(UTC),
            is_synthetic=False,
            message="test",
        )
