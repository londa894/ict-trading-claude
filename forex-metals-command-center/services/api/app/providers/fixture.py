"""Deterministic SYNTHETIC provider for development and tests.

Its prices are generated, not observed. They are NOT market facts: the decision gate blocks any
verdict on synthetic data (DATA_SYNTHETIC) and the series ends at a fixed historical date, so it
also classifies as STALE against a real clock.

One M5 base series per symbol is generated; M15, M30 and H1 are aggregated from it so that every
timeframe describes the same synthetic price path. H4/D1/W1 are not served natively (the candle engine
derives them from H1). M1 is finer than the M5 base and is not synthesized. The window spans the
2024-03-10 US DST change on purpose.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.domain.candle import RawBar
from app.domain.enums import DataQuality, MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import Instrument, get_instrument
from app.domain.quote import Quote
from app.providers.base import ProviderHealth, UnsupportedSymbolError
from app.services.candles.normalize import build_series
from app.services.data_quality.market_hours import market_status_at
from app.services.timeframes.aggregate import aggregate
from app.services.timeframes.core import expected_slots

SERIES_START = datetime(2024, 2, 25, 23, 0, tzinfo=UTC)  # Sunday 18:00 New York (EST)
SERIES_END = datetime(2024, 4, 19, 21, 0, tzinfo=UTC)  # Friday 17:00 New York (EDT)
BASE_TIMEFRAME = Timeframe.M5
NATIVE_TIMEFRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1)
_BASE_PRICE = {"XAUUSD": 2030.0, "XAGUSD": 23.0, "USDJPY": 144.0}
_DEFAULT_BASE = 1.1


class SyntheticFixtureProvider:
    name = "fixture"
    is_synthetic = True
    source = "fixture:synthetic"

    def __init__(self, start: datetime = SERIES_START, end: datetime = SERIES_END, seed: int = 7) -> None:
        self._start = start
        self._end = end
        self._seed = seed
        self._cache: dict[tuple[str, Timeframe], list[RawBar]] = {}

    def _instrument(self, symbol: str) -> Instrument:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        return instrument

    def _base(self, instrument: Instrument) -> list[RawBar]:
        rng = random.Random(f"{self._seed}:{instrument.symbol}:{BASE_TIMEFRAME}")  # noqa: S311 - not crypto
        price = _BASE_PRICE.get(instrument.symbol, _DEFAULT_BASE)
        step_scale = price * 0.0004
        digits = instrument.price_precision
        bars: list[RawBar] = []
        before_first = self._start - BASE_TIMEFRAME.duration
        for open_time in expected_slots(instrument.asset_class, BASE_TIMEFRAME, before_first, self._end):
            open_ = round(price, digits)
            close = round(open_ + rng.gauss(0, step_scale), digits)
            high = round(max(open_, close) + abs(rng.gauss(0, step_scale / 2)), digits)
            low = round(min(open_, close) - abs(rng.gauss(0, step_scale / 2)), digits)
            bars.append(
                RawBar(
                    symbol=instrument.symbol,
                    timeframe=BASE_TIMEFRAME,
                    open_time=open_time,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=float(rng.randint(50, 500)),
                )
            )
            price = close
        return bars

    def generate(self, symbol: str, timeframe: Timeframe) -> list[RawBar]:
        instrument = self._instrument(symbol)
        if timeframe not in NATIVE_TIMEFRAMES:
            return []
        key = (instrument.symbol, timeframe)
        if key in self._cache:
            return self._cache[key]
        base = self._cache.get((instrument.symbol, BASE_TIMEFRAME)) or self._base(instrument)
        self._cache[(instrument.symbol, BASE_TIMEFRAME)] = base
        if timeframe is BASE_TIMEFRAME:
            return base
        series = build_series(
            base,
            symbol=instrument.symbol,
            timeframe=BASE_TIMEFRAME,
            source=self.source,
            asset_class=instrument.asset_class,
            now=self._end,
        )
        candles, _ = aggregate(series.candles, BASE_TIMEFRAME, timeframe, instrument.asset_class, self._end)
        bars = [
            RawBar(
                symbol=c.symbol,
                timeframe=timeframe,
                open_time=c.open_time,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in candles
        ]
        self._cache[key] = bars
        return bars

    async def get_latest_quote(self, symbol: str) -> Quote:
        last = self.generate(symbol, BASE_TIMEFRAME)[-1]
        half_spread = last.close * 0.00005
        return Quote(
            symbol=last.symbol,
            bid=last.close - half_spread,
            ask=last.close + half_spread,
            timestamp=last.open_time + BASE_TIMEFRAME.duration,
            source=self.source,
            data_quality=DataQuality.STALE,
        )

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RawBar]:
        bars = self.generate(symbol, timeframe)
        selected = [
            b for b in bars if (start is None or b.open_time >= start) and (end is None or b.open_time < end)
        ]
        return selected[-limit:] if limit else selected

    async def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        for symbol in symbols:
            yield await self.get_latest_quote(symbol)

    async def subscribe_bars(self, symbols: list[str], timeframes: list[Timeframe]) -> AsyncIterator[RawBar]:
        for symbol in symbols:
            for timeframe in timeframes:
                for bar in self.generate(symbol, timeframe):
                    yield bar

    async def get_instrument_metadata(self, symbol: str) -> Instrument:
        return self._instrument(symbol)

    async def get_market_status(self, symbol: str) -> MarketStatus:
        return market_status_at(self._instrument(symbol).asset_class, datetime.now(UTC))

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            status=ProviderHealthStatus.HEALTHY,
            checked_at=datetime.now(UTC),
            is_synthetic=True,
            message="SYNTHETIC development data. Not market facts; verdicts are blocked.",
        )
