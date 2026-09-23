"""Validated candle series for chart, structure and decision (Phase 1+).

- M5 / M15 / H1 are requested natively from the provider and validated.
- H4 / D1 are derived from validated H1 using New York 17:00 buckets, so every vendor produces the
  same deterministic higher-timeframe candles regardless of its own daily-candle convention.
- INVALID or DISCONNECTED series return NO candles (issues only): questionable prices are never drawn.
- The chart series never produces or changes a verdict; the Master Decision stays the single authority.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.contracts import strategy_version
from app.domain.base import ApiModel
from app.domain.candle import Candle
from app.domain.enums import DataQuality, MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import get_instrument
from app.domain.issues import ValidationIssue
from app.providers.base import MarketDataProvider, ProviderError
from app.services.candles.normalize import build_series
from app.services.data_quality.market_hours import market_status_at
from app.services.timeframes.aggregate import aggregate, aggregate_weekly

logger = logging.getLogger("fmcc.candles")

CHART_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
    Timeframe.W1,
)
# Higher timeframes are derived from validated H1 (deterministic, vendor-independent). W1 is a
# second hop: H1 -> D1 -> W1, so its source fetch and bar-count math are sized off H1.
DERIVED_FROM: dict[Timeframe, Timeframe] = {
    Timeframe.H4: Timeframe.H1,
    Timeframe.D1: Timeframe.H1,
    Timeframe.W1: Timeframe.H1,
}
MAX_LIMIT = 1000
DEFAULT_LIMIT = 300
WARMUP_BARS = 25  # >= spike-detection lookback, so the oldest returned candles are judged too
# W1 is derived H1 -> D1 -> W1, so N weeks costs ~N*168 source H1 bars. Providers cap a single
# history request (TradeLocker ~11.6k H1 bars), so the weekly window is clamped to what fits; 64
# weeks (~10.9k H1) yields >50 closed weekly candles, clearing the structure minCandles gate.
_MAX_W1_LIMIT = 64
_WITHHELD = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})


class UnknownSymbolError(LookupError):
    pass


class UnsupportedChartTimeframeError(ValueError):
    pass


class ChartCandle(ApiModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    is_closed: bool


class ChartSeriesResponse(ApiModel):
    symbol: str
    timeframe: Timeframe
    source_timeframe: Timeframe
    provider: str
    is_synthetic: bool
    quality: DataQuality
    market_status: MarketStatus
    candles: list[ChartCandle]
    issues: list[ValidationIssue]
    provider_error: str | None
    strategy_version: str
    generated_at: datetime


def _chart_candle(c: Candle) -> ChartCandle:
    return ChartCandle(
        time=c.open_time,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close,
        volume=c.volume,
        is_closed=c.is_closed,
    )


@dataclass(frozen=True)
class LoadedSeries:
    """A validated series for one timeframe (native or derived). Candles are empty when withheld."""

    symbol: str
    timeframe: Timeframe
    source_timeframe: Timeframe
    candles: list[Candle]
    issues: list[ValidationIssue]
    quality: DataQuality
    provider_error: str | None
    is_synthetic: bool
    market_status: MarketStatus
    now: datetime


class CandleService:
    def __init__(self, provider: MarketDataProvider, clock: Callable[[], datetime] | None = None) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def provider(self) -> MarketDataProvider:
        return self._provider

    def now(self) -> datetime:
        return self._clock()

    async def load_series(
        self, symbol: str, timeframe: Timeframe, limit: int, now: datetime | None = None
    ) -> LoadedSeries:
        """Shared loader for chart, structure and decision. Never raises for provider/data problems."""
        if timeframe not in CHART_TIMEFRAMES:
            raise UnsupportedChartTimeframeError(timeframe)
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be 1..{MAX_LIMIT}")
        symbol = symbol.upper()
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnknownSymbolError(symbol)

        now = now or self._clock()
        if timeframe is Timeframe.W1:
            limit = min(limit, _MAX_W1_LIMIT)  # bounded by the provider's single-request H1 ceiling
        source_tf = DERIVED_FROM.get(timeframe, timeframe)
        candles: list[Candle] = []
        issues: list[ValidationIssue] = []
        quality = DataQuality.DISCONNECTED
        provider_error: str | None = None

        try:
            health = await self._provider.health_check()
            if health.status not in (ProviderHealthStatus.HEALTHY, ProviderHealthStatus.DEGRADED):
                provider_error = health.message
            else:
                ratio = timeframe.duration // source_tf.duration
                # One extra target bucket so the (possibly partial) left-edge bucket can be dropped.
                needed = (limit + 1) * ratio + WARMUP_BARS
                raw = await self._provider.get_historical_bars(symbol, source_tf, end=now, limit=needed)
                series = build_series(
                    raw,
                    symbol=symbol,
                    timeframe=source_tf,
                    source=getattr(self._provider, "source", self._provider.name),
                    asset_class=instrument.asset_class,
                    now=now,
                )
                quality, issues, candles = series.quality, list(series.issues), list(series.candles)
                if source_tf is not timeframe and quality not in _WITHHELD:
                    if timeframe is Timeframe.W1:
                        # H1 -> D1 -> W1: the weekly candle groups New York trading days.
                        d1, d1_issues = aggregate(
                            candles, source_tf, Timeframe.D1, instrument.asset_class, now
                        )
                        derived, wk_issues = aggregate_weekly(d1, instrument.asset_class, now)
                        agg_issues = [*d1_issues, *wk_issues]
                    else:
                        derived, agg_issues = aggregate(
                            candles, source_tf, timeframe, instrument.asset_class, now
                        )
                    if len(derived) > 1:
                        edge = derived[0].open_time
                        derived = derived[1:]
                        agg_issues = [i for i in agg_issues if i.at != edge]
                    candles = [c.model_copy(update={"data_quality": quality}) for c in derived]
                    issues.extend(agg_issues)
        except ProviderError as exc:
            provider_error = str(exc) or type(exc).__name__
        except Exception as exc:
            logger.exception("series load failed for %s %s", symbol, timeframe)
            provider_error = f"unexpected ingestion failure: {type(exc).__name__}"
            quality, candles, issues = DataQuality.DISCONNECTED, [], []

        if provider_error is not None:
            quality, candles = DataQuality.DISCONNECTED, []
        if quality in _WITHHELD:
            candles = []

        return LoadedSeries(
            symbol=symbol,
            timeframe=timeframe,
            source_timeframe=source_tf,
            candles=candles[-limit:],
            issues=issues,
            quality=quality,
            provider_error=provider_error,
            is_synthetic=self._provider.is_synthetic,
            market_status=market_status_at(instrument.asset_class, now),
            now=now,
        )

    async def chart_series(
        self, symbol: str, timeframe: Timeframe, limit: int = DEFAULT_LIMIT
    ) -> ChartSeriesResponse:
        s = await self.load_series(symbol, timeframe, limit)
        return ChartSeriesResponse(
            symbol=s.symbol,
            timeframe=s.timeframe,
            source_timeframe=s.source_timeframe,
            provider=self._provider.name,
            is_synthetic=s.is_synthetic,
            quality=s.quality,
            market_status=s.market_status,
            candles=[_chart_candle(c) for c in s.candles],
            issues=s.issues,
            provider_error=s.provider_error,
            strategy_version=strategy_version(),
            generated_at=s.now,
        )
