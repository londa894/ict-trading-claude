"""Session scenario builders: M15 candles on real market-open slots (synthetic prices, not market facts)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.domain.candle import Candle
from app.domain.enums import AssetClass, DataQuality, MarketStatus, Timeframe
from app.services.candles.service import LoadedSeries
from app.services.data_quality.market_hours import is_market_open
from app.services.sessions.models import SessionConfig

M15 = Timeframe.M15
CFG = SessionConfig.from_spec()
# Sunday 2024-01-07 23:00 UTC = 18:00 New York (EST): metals week open.
WEEK_OPEN = datetime(2024, 1, 7, 23, 0, tzinfo=UTC)
Row = tuple[float, float, float, float]
FLAT: Row = (2030.0, 2030.5, 2029.5, 2030.0)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def candle(t: datetime, row: Row, tf: Timeframe = M15) -> Candle:
    o, h, low, c = row
    return Candle(
        symbol="XAUUSD",
        timeframe=tf,
        open_time=t,
        close_time=t + tf.duration,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )


def slots(start: datetime, end: datetime, tf: Timeframe = M15) -> list[datetime]:
    out, t = [], start
    while t < end:
        if is_market_open(AssetClass.METAL, t):
            out.append(t)
        t += tf.duration
    return out


def m15(
    start: datetime,
    end: datetime,
    overrides: dict[datetime, Row] | None = None,
    skip: set[datetime] | frozenset[datetime] = frozenset(),
) -> list[Candle]:
    overrides = overrides or {}
    return [candle(t, overrides.get(t, FLAT)) for t in slots(start, end) if t not in skip]


def walk(start: datetime, end: datetime, seed: int) -> list[Candle]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    out, price = [], 2000.0
    for t in slots(start, end):
        o = price
        c = round(o + rng.gauss(0, 1.2), 2)
        h = round(max(o, c) + abs(rng.gauss(0, 0.6)), 2)
        low = round(min(o, c) - abs(rng.gauss(0, 0.6)), 2)
        out.append(candle(t, (o, h, low, c)))
        price = c
    return out


def loaded(
    candles: list[Candle], now: datetime, tf: Timeframe = M15, synthetic: bool = False
) -> LoadedSeries:
    return LoadedSeries(
        symbol="XAUUSD",
        timeframe=tf,
        source_timeframe=tf,
        candles=candles,
        issues=[],
        quality=DataQuality.CURRENT,
        provider_error=None,
        is_synthetic=synthetic,
        market_status=MarketStatus.OPEN,
        now=now,
    )


def d1_candles(first_day_open: datetime, ranges: list[float]) -> list[Candle]:
    """Consecutive trading-day D1 candles (22:00/23:00 UTC opens in EST) skipping weekends."""
    out, t = [], first_day_open
    for r in ranges:
        while t.weekday() in (4, 5):  # Friday/Saturday 17:00 opens are not trading days
            t += timedelta(days=1)
        out.append(candle(t, (2030.0, 2030.0 + r / 2, 2030.0 - r / 2, 2030.0), Timeframe.D1))
        t += timedelta(days=1)
    return out
