"""Weekly FX/metals trading hours, DST-aware via the IANA tz database.

Used only for data-quality purposes (gap and staleness classification). Session / kill-zone logic
belongs to the Session & Time engine (Phase 6). Holidays are not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from functools import cache
from zoneinfo import ZoneInfo

from app.contracts import load_spec
from app.domain.base import require_utc
from app.domain.enums import AssetClass, MarketStatus

_WEEKDAYS = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


@dataclass(frozen=True)
class WeeklyHours:
    tz: ZoneInfo
    open_weekday: int
    open_time: time
    close_weekday: int
    close_time: time
    break_start: time | None
    break_end: time | None


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@cache
def weekly_hours(asset_class: AssetClass) -> WeeklyHours:
    spec = load_spec("market_hours")
    cfg = spec[asset_class.value]
    open_wd = _WEEKDAYS[cfg["weekOpen"]["weekday"]]
    close_wd = _WEEKDAYS[cfg["weekClose"]["weekday"]]
    if (open_wd, close_wd) != (6, 4):
        raise ValueError("Phase 0 market hours support only a Sunday-open / Friday-close week")
    brk = cfg.get("dailyBreak")
    return WeeklyHours(
        tz=ZoneInfo(spec["timezone"]),
        open_weekday=open_wd,
        open_time=_parse_time(cfg["weekOpen"]["time"]),
        close_weekday=close_wd,
        close_time=_parse_time(cfg["weekClose"]["time"]),
        break_start=_parse_time(brk["start"]) if brk else None,
        break_end=_parse_time(brk["end"]) if brk else None,
    )


def market_status_at(asset_class: AssetClass, instant: datetime) -> MarketStatus:
    hours = weekly_hours(asset_class)
    local = require_utc(instant, "instant").astimezone(hours.tz)
    wd, t = local.weekday(), local.time()

    if wd == 5:  # Saturday
        return MarketStatus.CLOSED
    if wd == hours.close_weekday and t >= hours.close_time:
        return MarketStatus.CLOSED
    if wd == hours.open_weekday and t < hours.open_time:
        return MarketStatus.CLOSED
    if hours.break_start and hours.break_end and hours.break_start <= t < hours.break_end:
        return MarketStatus.DAILY_BREAK
    return MarketStatus.OPEN


def is_market_open(asset_class: AssetClass, instant: datetime) -> bool:
    return market_status_at(asset_class, instant) is MarketStatus.OPEN
