"""Default provider when no independent data vendor is configured. Always fails safe."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.domain.candle import RawBar
from app.domain.enums import MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import Instrument, get_instrument
from app.domain.quote import Quote
from app.providers.base import ProviderHealth, ProviderUnavailableError, UnsupportedSymbolError

_MESSAGE = "No market-data provider configured (set MARKET_DATA_PROVIDER)."


class UnconfiguredProvider:
    name = "unconfigured"
    is_synthetic = False

    async def get_latest_quote(self, symbol: str) -> Quote:
        raise ProviderUnavailableError(_MESSAGE)

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RawBar]:
        raise ProviderUnavailableError(_MESSAGE)

    async def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        raise ProviderUnavailableError(_MESSAGE)
        yield  # pragma: no cover

    async def subscribe_bars(self, symbols: list[str], timeframes: list[Timeframe]) -> AsyncIterator[RawBar]:
        raise ProviderUnavailableError(_MESSAGE)
        yield  # pragma: no cover

    async def get_instrument_metadata(self, symbol: str) -> Instrument:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        return instrument

    async def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus.UNKNOWN

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            status=ProviderHealthStatus.DOWN,
            checked_at=datetime.now(UTC),
            is_synthetic=False,
            message=_MESSAGE,
        )
