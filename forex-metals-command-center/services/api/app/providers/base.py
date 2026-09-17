"""Market-data provider abstraction (spec STEP 12).

Providers deliver *market data only*. There is intentionally no order, account, position or broker
surface anywhere in this interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.base import ApiModel
from app.domain.candle import RawBar
from app.domain.enums import MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import Instrument
from app.domain.quote import Quote


class ProviderError(Exception):
    """Base class for provider failures. Callers must fail safe (UNAVAILABLE)."""


class ProviderUnavailableError(ProviderError):
    pass


class UnsupportedSymbolError(ProviderError):
    pass


class ProviderHealth(ApiModel):
    provider: str
    status: ProviderHealthStatus
    checked_at: datetime
    is_synthetic: bool
    message: str


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    is_synthetic: bool

    async def get_latest_quote(self, symbol: str) -> Quote: ...

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RawBar]:
        """Bars with start <= open_time < end, ascending; if `limit`, only the most recent `limit` of them.

        Callers still validate everything returned (a provider ignoring `end` is caught downstream)."""
        ...

    def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]: ...

    def subscribe_bars(self, symbols: list[str], timeframes: list[Timeframe]) -> AsyncIterator[RawBar]: ...

    async def get_instrument_metadata(self, symbol: str) -> Instrument: ...

    async def get_market_status(self, symbol: str) -> MarketStatus: ...

    async def health_check(self) -> ProviderHealth: ...
