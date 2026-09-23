"""Timeframe bucket arithmetic.

Conventions (deterministic, vendor-independent):
- M1..H1: fixed durations aligned to the UTC epoch. (New York offsets are whole hours, so H1 buckets
  are also aligned in New York time.)
- H4/D1: anchored to the New York 17:00 trading-day roll ("NY close"), DST-aware. The D1 bucket for
  trading day D runs from (D-1) 17:00 to D 17:00 New York; H4 buckets start at 17, 21, 01, 05, 09, 13.
  US DST changes happen at 02:00 on Sundays while FX/metals are closed, so every bucket that can
  contain bars is exactly 4h / 24h long. That is asserted, not assumed.
- W1: anchored to the Sunday 17:00 New York session open (the weekly open ICT watches). A trading
  week runs Sunday 17:00 -> Friday 17:00 New York. A DST change falls on the terminal Sunday while the
  market is closed, so a week bucket's UTC length can be 7 days +/- 1h; weekly buckets are therefore
  built from D1 (see aggregate_weekly), never sized by a fixed duration.
- MN1: not supported by the candle engine yet.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.base import require_utc
from app.domain.enums import AssetClass, Timeframe
from app.services.data_quality.market_hours import is_market_open

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NEW_YORK = ZoneInfo("America/New_York")
TRADING_DAY_ROLL = time(17, 0)


class UnsupportedTimeframeError(ValueError):
    pass


def _require_supported(timeframe: Timeframe) -> None:
    if not timeframe.is_engine_supported:
        raise UnsupportedTimeframeError(f"{timeframe} is not supported by the candle engine")


def trading_day_start(instant: datetime) -> datetime:
    """UTC start (New York 17:00) of the trading day containing `instant`."""
    local = require_utc(instant, "instant").astimezone(NEW_YORK)
    roll_date = local.date() if local.time() >= TRADING_DAY_ROLL else local.date() - timedelta(days=1)
    return datetime.combine(roll_date, TRADING_DAY_ROLL, tzinfo=NEW_YORK).astimezone(UTC)


def trading_week_start(instant: datetime) -> datetime:
    """UTC start (Sunday 17:00 New York) of the trading week containing `instant`.

    The weekly open is the Sunday session open; the week's constituent trading days start on Sun,
    Mon, Tue, Wed and Thu at 17:00 New York."""
    day_local = trading_day_start(instant).astimezone(NEW_YORK)
    # weekday(): Mon=0 .. Sun=6. Trading-day starts only ever land on Sun-Thu, so this walks back to Sun.
    days_since_sunday = (day_local.weekday() - 6) % 7
    week_date = day_local.date() - timedelta(days=days_since_sunday)
    return datetime.combine(week_date, TRADING_DAY_ROLL, tzinfo=NEW_YORK).astimezone(UTC)


def bucket_start(instant: datetime, timeframe: Timeframe) -> datetime:
    _require_supported(timeframe)
    instant = require_utc(instant, "instant")
    if timeframe.is_fixed_intraday:
        elapsed = instant - _EPOCH
        return _EPOCH + (elapsed // timeframe.duration) * timeframe.duration
    if timeframe.is_trading_week_anchored:
        return trading_week_start(instant)
    day_start = trading_day_start(instant)
    if timeframe is Timeframe.D1:
        return day_start
    # H4: count whole 4h steps of New York wall time since the roll.
    local_day_start = day_start.astimezone(NEW_YORK)
    wall_elapsed = instant.astimezone(NEW_YORK).replace(tzinfo=None) - local_day_start.replace(tzinfo=None)
    steps = wall_elapsed // timeframe.duration
    return (local_day_start + steps * timeframe.duration).astimezone(UTC)


# Backwards-compatible name used by Phase 0 code/tests.
floor_to_timeframe = bucket_start


def next_bucket_start(start: datetime, timeframe: Timeframe) -> datetime:
    _require_supported(timeframe)
    start = require_utc(start, "start")
    if timeframe.is_fixed_intraday:
        return start + timeframe.duration
    # Aware datetime + timedelta is wall-clock arithmetic in the attached zone -> DST-safe stepping.
    candidate = (start.astimezone(NEW_YORK) + timeframe.duration).astimezone(UTC)
    return bucket_start(candidate, timeframe)


def is_aligned(open_time: datetime, timeframe: Timeframe) -> bool:
    if not timeframe.is_engine_supported:
        return False
    return bucket_start(open_time, timeframe) == require_utc(open_time, "open_time")


def bucket_has_open_market(asset_class: AssetClass, timeframe: Timeframe, start: datetime) -> bool:
    if timeframe.is_fixed_intraday:
        return is_market_open(asset_class, start)
    hours = int(timeframe.duration / timedelta(hours=1))
    return any(is_market_open(asset_class, start + timedelta(hours=h)) for h in range(hours))


def expected_slots(
    asset_class: AssetClass,
    timeframe: Timeframe,
    after_open_time: datetime,
    until: datetime,
    *,
    limit: int | None = None,
) -> Iterator[datetime]:
    """Yield bucket open times after `after_open_time` whose close_time <= `until` and that contain
    open market time. `limit` bounds work on long gaps."""
    _require_supported(timeframe)
    cursor = next_bucket_start(after_open_time, timeframe)
    yielded = 0
    while cursor + timeframe.duration <= until:
        if bucket_has_open_market(asset_class, timeframe, cursor):
            yield cursor
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        cursor = next_bucket_start(cursor, timeframe)
