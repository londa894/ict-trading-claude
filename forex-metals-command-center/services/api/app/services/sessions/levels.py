"""Session instances, opens, previous session and Asian range state from closed source candles (no lookahead).

Everything is computed as of `as_of` = the close time of the latest closed candle used:
- an instance is NOT_STARTED while as_of <= start, FORMING while start < as_of < end (levels provisional,
  only from candles closed by as_of), and after the end COMPLETE when every expected candle is present,
  otherwise INCOMPLETE (shown, but never used for liquidity pools, range state or Judas swings);
- expected candles = source-timeframe slots inside the window while the market is open (holidays are not
  modelled, so a missing holiday session is INCOMPLETE: fail-safe direction);
- opens come only from the exact expected candle (a missing candle gives null, never a substitute);
- the Asian range state of an instance compares it only with EARLIER complete Asian sessions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app.domain.base import require_utc
from app.domain.candle import Candle
from app.domain.enums import AsianRangeState, AssetClass, SessionInstanceState, SessionName
from app.services.data_quality.market_hours import is_market_open
from app.services.sessions.clock import trading_day_bounds, trading_day_of, window_bounds
from app.services.sessions.models import (
    AsianRangeConfig,
    PreviousSession,
    SessionConfig,
    SessionInstance,
    SessionOpens,
)
from app.services.timeframes.core import NEW_YORK

S = SessionInstanceState


def closed_until(candles: Sequence[Candle], as_of: datetime) -> list[Candle]:
    return sorted((c for c in candles if c.is_closed and c.close_time <= as_of), key=lambda c: c.open_time)


def expected_slots(
    start: datetime, end: datetime, step: timedelta, asset_class: AssetClass
) -> list[datetime]:
    slots, cursor = [], start
    while cursor < end:
        if is_market_open(asset_class, cursor):
            slots.append(cursor)
        cursor += step
    return slots


def build_instances(
    candles: Sequence[Candle], as_of: datetime, asset_class: AssetClass, cfg: SessionConfig
) -> list[SessionInstance]:
    as_of = require_utc(as_of, "as_of")
    closed = closed_until(candles, as_of)
    step = cfg.source_timeframe.duration
    days = sorted(
        {trading_day_of(c.open_time) for c in closed} | ({trading_day_of(as_of)} if closed else set())
    )
    out: list[SessionInstance] = []
    for day in days:
        for name, w in cfg.sessions.items():
            start, end = window_bounds(w, day)
            expected = expected_slots(start, end, step, asset_class)
            if not expected:
                continue
            inside = [c for c in closed if start <= c.open_time and c.close_time <= end]
            if as_of <= start:
                state = S.NOT_STARTED
            elif as_of < end:
                state = S.FORMING
            else:
                present = {c.open_time for c in inside}
                state = S.COMPLETE if all(t in present for t in expected) else S.INCOMPLETE
            if state is S.NOT_STARTED and day != trading_day_of(as_of):
                continue
            hi = max(inside, key=lambda c: c.high) if inside else None
            lo = min(inside, key=lambda c: c.low) if inside else None
            out.append(
                SessionInstance(
                    id=f"{name.value}:{day.isoformat()}",
                    session=name,
                    trading_day=day,
                    start=start,
                    end=end,
                    state=state,
                    high=hi.high if hi else None,
                    low=lo.low if lo else None,
                    midpoint=round((hi.high + lo.low) / 2, 10) if hi and lo else None,
                    range=round(hi.high - lo.low, 10) if hi and lo else None,
                    high_time=hi.open_time if hi else None,
                    low_time=lo.open_time if lo else None,
                    candle_count=len(inside),
                    expected_count=len(expected),
                    known_at=end if state is S.COMPLETE else None,
                    asian_range_state=None,
                    asian_range_ratio=None,
                )
            )
    out.sort(key=lambda x: (x.start, x.session.value))
    return _with_asian_range(out, cfg.asian_range)


def classify_asian_range(ratio: float, cfg: AsianRangeConfig) -> AsianRangeState:
    if ratio < cfg.tight_below:
        return AsianRangeState.TIGHT
    if ratio < cfg.normal_below:
        return AsianRangeState.NORMAL
    if ratio < cfg.expanded_below:
        return AsianRangeState.EXPANDED
    return AsianRangeState.ABNORMALLY_LARGE


def _with_asian_range(instances: list[SessionInstance], cfg: AsianRangeConfig) -> list[SessionInstance]:
    prior: list[float] = []
    out: list[SessionInstance] = []
    for inst in instances:
        rng = inst.range
        if inst.session is SessionName.ASIA and inst.state is S.COMPLETE and rng is not None:
            window = prior[-cfg.lookback_sessions :]
            if len(window) >= cfg.min_sessions and sum(window) > 0:
                ratio = rng / (sum(window) / len(window))
                inst = inst.model_copy(
                    update={
                        "asian_range_ratio": round(ratio, 3),
                        "asian_range_state": classify_asian_range(ratio, cfg),
                    }
                )
            prior.append(rng)
        out.append(inst)
    return out


def previous_session(instances: Sequence[SessionInstance], as_of: datetime) -> PreviousSession | None:
    done = [i for i in instances if i.state is S.COMPLETE and i.end <= as_of and i.high is not None]
    if not done:
        return None
    last = max(done, key=lambda i: (i.end, i.start))
    assert last.high is not None and last.low is not None
    return PreviousSession(
        instance_id=last.id, session=last.session, high=last.high, low=last.low, end=last.end
    )


def _candle_at(candles: Sequence[Candle], t: datetime) -> Candle | None:
    return next((c for c in candles if c.open_time == t), None)


def _first_open_slot(start: datetime, step: timedelta, asset_class: AssetClass, limit: int = 96) -> datetime:
    cursor = start
    for _ in range(limit):
        if is_market_open(asset_class, cursor):
            return cursor
        cursor += step
    return start


def opens(
    candles: Sequence[Candle], as_of: datetime, asset_class: AssetClass, cfg: SessionConfig
) -> SessionOpens:
    closed = closed_until(candles, as_of)
    step = cfg.source_timeframe.duration
    empty = SessionOpens(
        daily_open=None,
        daily_open_time=None,
        ny_midnight_open=None,
        ny_midnight_open_time=None,
        weekly_open=None,
        weekly_open_time=None,
        last_close=None,
        daily_change=None,
        daily_change_pct=None,
    )
    if not closed:
        return empty
    day = trading_day_of(closed[-1].open_time)
    day_start, _ = trading_day_bounds(day)
    daily = _candle_at(closed, _first_open_slot(day_start, step, asset_class))
    midnight_t = datetime(day.year, day.month, day.day, tzinfo=NEW_YORK).astimezone(UTC)
    midnight = _candle_at(closed, midnight_t)
    monday = day - timedelta(days=day.weekday())
    week_start, _ = trading_day_bounds(monday)
    weekly = _candle_at(closed, _first_open_slot(week_start, step, asset_class))
    last = closed[-1].close
    change = round(last - daily.open, 10) if daily else None
    return SessionOpens(
        daily_open=daily.open if daily else None,
        daily_open_time=daily.open_time if daily else None,
        ny_midnight_open=midnight.open if midnight else None,
        ny_midnight_open_time=midnight.open_time if midnight else None,
        weekly_open=weekly.open if weekly else None,
        weekly_open_time=weekly.open_time if weekly else None,
        last_close=last,
        daily_change=change,
        daily_change_pct=round(change / daily.open * 100, 4) if daily and change is not None else None,
    )


def current_day_range(candles: Sequence[Candle], as_of: datetime) -> tuple[date, float | None]:
    closed = closed_until(candles, as_of)
    if not closed:
        return trading_day_of(as_of), None
    day = trading_day_of(closed[-1].open_time)
    today = [c for c in closed if trading_day_of(c.open_time) == day]
    return day, round(max(c.high for c in today) - min(c.low for c in today), 10)
