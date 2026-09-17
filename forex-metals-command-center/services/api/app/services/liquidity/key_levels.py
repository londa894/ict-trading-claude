"""Previous-day and previous-week highs/lows from closed New York-close D1 candles.

- PDH/PDL: each closed trading day's high/low, known from that day's close (17:00 New York).
- PWH/PWL: each completed trading week's high/low. A trading week is the set of trading days whose
  New York close falls in the same ISO week (Mon..Fri). It is known at the close of its Friday candle,
  or — if Friday is missing (holiday) — at the close of the first candle of the following week.
  A week that can't yet be proven complete produces no level.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import groupby

from app.domain.base import require_utc
from app.domain.candle import Candle
from app.domain.enums import LiquidityPoolType, Timeframe
from app.services.liquidity.models import KeyLevel
from app.services.timeframes.core import NEW_YORK

FRIDAY = 4


def _close_date(c: Candle) -> tuple[int, int]:
    iso = c.close_time.astimezone(NEW_YORK).isocalendar()
    return iso.year, iso.week


def key_levels(d1: Sequence[Candle], as_of: datetime, days: int, weeks: int) -> list[KeyLevel]:
    """Key levels known at or before `as_of` (the close time of the latest analysed candle)."""
    as_of = require_utc(as_of, "as_of")
    closed = [c for c in d1 if c.timeframe is Timeframe.D1 and c.is_closed and c.close_time <= as_of]
    closed.sort(key=lambda c: c.open_time)
    levels: list[KeyLevel] = []

    for c in closed[-days:] if days > 0 else []:
        day = c.close_time.astimezone(NEW_YORK).date().isoformat()
        levels.append(
            KeyLevel(
                type=LiquidityPoolType.PDH,
                price=c.high,
                period_start=c.open_time,
                known_at=c.close_time,
                label=f"PDH {day}",
            )
        )
        levels.append(
            KeyLevel(
                type=LiquidityPoolType.PDL,
                price=c.low,
                period_start=c.open_time,
                known_at=c.close_time,
                label=f"PDL {day}",
            )
        )

    groups = [list(g) for _, g in groupby(closed, key=_close_date)]
    complete: list[tuple[list[Candle], datetime]] = []
    for idx, week in enumerate(groups):
        last = week[-1]
        if last.close_time.astimezone(NEW_YORK).weekday() == FRIDAY:
            complete.append((week, last.close_time))
        elif idx + 1 < len(groups):
            complete.append((week, groups[idx + 1][0].close_time))
    for week, known_at in complete[-weeks:] if weeks > 0 else []:
        if known_at > as_of:
            continue
        monday = week[0].close_time.astimezone(NEW_YORK).date().isoformat()
        high = max(c.high for c in week)
        low = min(c.low for c in week)
        levels.append(
            KeyLevel(
                type=LiquidityPoolType.PWH,
                price=high,
                period_start=week[0].open_time,
                known_at=known_at,
                label=f"PWH wk {monday}",
            )
        )
        levels.append(
            KeyLevel(
                type=LiquidityPoolType.PWL,
                price=low,
                period_start=week[0].open_time,
                known_at=known_at,
                label=f"PWL wk {monday}",
            )
        )
    return sorted(levels, key=lambda k: (k.known_at, k.type.value))
