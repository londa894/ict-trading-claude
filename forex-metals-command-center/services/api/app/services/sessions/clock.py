"""Session clock: trading day, window bounds, active sessions/kill zones and time quality (time only).

Windows are New York wall-clock times, converted with the IANA tz database, so they follow US DST. London
DST changes on different dates: during the gap weeks the London wall time of a New York window shifts by an
hour, which is intended (the windows are defined in New York time). Trading day D runs from (D-1) 17:00 to
D 17:00 New York, matching the D1 candles.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.base import require_utc
from app.domain.enums import QUALITY_ORDER, AssetClass, MarketStatus, SessionQuality
from app.services.data_quality.market_hours import market_status_at
from app.services.sessions.models import SessionClock, SessionConfig, Window
from app.services.timeframes.core import NEW_YORK, trading_day_start

LONDON = ZoneInfo("Europe/London")


def trading_day_of(instant: datetime) -> date:
    """Label of the trading day containing `instant` = the New York date of its 17:00 close."""
    start = trading_day_start(require_utc(instant, "instant")).astimezone(NEW_YORK)
    return (start + timedelta(days=1)).date()


def trading_day_bounds(day: date) -> tuple[datetime, datetime]:
    end = datetime(day.year, day.month, day.day, 17, 0, tzinfo=NEW_YORK)
    start = datetime.combine(day - timedelta(days=1), end.timetz())
    return start.astimezone(UTC), end.astimezone(UTC)


def window_bounds(w: Window, day: date) -> tuple[datetime, datetime]:
    """UTC [start, end) of window `w` in trading day `day` (DST-aware wall-clock arithmetic)."""
    start_date = day - timedelta(days=1) if w.starts_previous_evening else day
    naive_start = datetime.combine(start_date, w.start)
    naive_end = naive_start + timedelta(minutes=w.minutes)
    return (
        naive_start.replace(tzinfo=NEW_YORK).astimezone(UTC),
        naive_end.replace(tzinfo=NEW_YORK).astimezone(UTC),
    )


def active_windows[K](windows: dict[K, Window], instant: datetime) -> list[K]:
    day = trading_day_of(instant)
    out: list[K] = []
    for key, w in windows.items():
        start, end = window_bounds(w, day)
        if start <= instant < end:
            out.append(key)
    return out


def best_quality(qualities: Iterable[SessionQuality]) -> SessionQuality | None:
    ranked = sorted(qualities, key=QUALITY_ORDER.index)
    return ranked[-1] if ranked else None


def time_quality(instant: datetime, asset_class: AssetClass, cfg: SessionConfig) -> SessionQuality:
    """AVOID when the market is not open; otherwise the best quality of the active windows."""
    instant = require_utc(instant, "instant")
    if market_status_at(asset_class, instant) is not MarketStatus.OPEN:
        return SessionQuality.AVOID
    names = [k.value for k in active_windows(cfg.kill_zones, instant)]
    names += [k.value for k in active_windows(cfg.sessions, instant)]
    best = best_quality(cfg.time_quality[n] for n in names if n in cfg.time_quality)
    return best or cfg.outside_quality


def downgrade(quality: SessionQuality) -> SessionQuality:
    return QUALITY_ORDER[max(0, QUALITY_ORDER.index(quality) - 1)]


def session_clock(now: datetime, asset_class: AssetClass, cfg: SessionConfig) -> SessionClock:
    now = require_utc(now, "now")
    day = trading_day_of(now)
    upcoming: list[tuple[datetime, str]] = []
    for offset in range(8):
        d = day + timedelta(days=offset)
        for name, w in cfg.sessions.items():
            start, _ = window_bounds(w, d)
            if start > now and market_status_at(asset_class, start) is MarketStatus.OPEN:
                upcoming.append((start, name.value))
        if upcoming:
            break
    nxt = min(upcoming) if upcoming else None
    return SessionClock(
        now=now,
        new_york_time=now.astimezone(NEW_YORK).isoformat(),
        london_time=now.astimezone(LONDON).isoformat(),
        trading_day=day,
        market_status=market_status_at(asset_class, now),
        active_sessions=active_windows(cfg.sessions, now),
        active_kill_zones=active_windows(cfg.kill_zones, now),
        time_quality=time_quality(now, asset_class, cfg),
        next_session=next((k for k in cfg.sessions if nxt and k.value == nxt[1]), None),
        next_session_start=nxt[0] if nxt else None,
    )
