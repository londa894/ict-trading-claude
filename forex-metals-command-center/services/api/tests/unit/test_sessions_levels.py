"""Hand-built session scenarios: instances, opens, previous session, Asian range, ADR, Judas swing, pools."""

from datetime import date, timedelta

import pytest

from app.domain.enums import (
    AsianRangeState,
    AssetClass,
    Direction,
    ExpansionState,
    JudasStatus,
    LiquidityEventType,
    LiquidityPoolType,
    LiquiditySide,
    SessionInstanceState,
    SessionName,
    SessionQuality,
    TrendDirection,
)
from app.services.liquidity.engine import analyze_liquidity
from app.services.liquidity.models import LiquidityConfig
from app.services.sessions.adr import adr_state, classify_expansion
from app.services.sessions.analysis import analyze_sessions, session_key_levels
from app.services.sessions.judas import detect_judas
from app.services.sessions.levels import build_instances, classify_asian_range, opens, previous_session
from tests.session_helpers import CFG, WEEK_OPEN, candle, d1_candles, loaded, m15, utc

METAL = AssetClass.METAL
S = SessionInstanceState
TUE = date(2024, 1, 9)
ASIA_HIGH_BAR = utc(2024, 1, 9, 2, 0)  # 21:00 EST Monday evening
ASIA_LOW_BAR = utc(2024, 1, 9, 3, 0)
ASIA_EXTREMES = {
    ASIA_HIGH_BAR: (2030.0, 2035.0, 2029.5, 2030.0),
    ASIA_LOW_BAR: (2030.0, 2030.5, 2025.0, 2030.0),
}


def by_id(instances, iid):
    return next(i for i in instances if i.id == iid)


def test_asia_forming_then_complete_with_levels_known_at_window_end():
    candles = m15(WEEK_OPEN, utc(2024, 1, 9, 12, 0), ASIA_EXTREMES)
    forming = by_id(build_instances(candles, utc(2024, 1, 9, 4, 0), METAL, CFG), "ASIA:2024-01-09")
    assert forming.state is S.FORMING and forming.known_at is None
    assert (forming.high, forming.low, forming.candle_count, forming.expected_count) == (
        2035.0,
        2025.0,
        12,
        16,
    )

    instances = build_instances(candles, utc(2024, 1, 9, 12, 0), METAL, CFG)
    asia = by_id(instances, "ASIA:2024-01-09")
    assert asia.state is S.COMPLETE and asia.known_at == utc(2024, 1, 9, 5, 0)
    assert (asia.high, asia.low, asia.midpoint, asia.range) == (2035.0, 2025.0, 2030.0, 10.0)
    assert (asia.high_time, asia.low_time) == (ASIA_HIGH_BAR, ASIA_LOW_BAR)
    assert by_id(instances, "LONDON:2024-01-09").state is S.COMPLETE
    ny_am = by_id(instances, "NY_AM:2024-01-09")
    assert ny_am.state is S.NOT_STARTED and ny_am.high is None
    assert "NY_AM:2024-01-08" in {i.id for i in instances}  # previous day kept


def test_missing_candle_makes_the_session_incomplete_and_unusable():
    candles = m15(WEEK_OPEN, utc(2024, 1, 9, 12, 0), ASIA_EXTREMES, skip={utc(2024, 1, 9, 4, 30)})
    instances = build_instances(candles, utc(2024, 1, 9, 12, 0), METAL, CFG)
    asia = by_id(instances, "ASIA:2024-01-09")
    assert asia.state is S.INCOMPLETE and asia.known_at is None and asia.high == 2035.0
    levels = session_key_levels(loaded(candles, utc(2024, 1, 9, 12, 0)), utc(2024, 1, 9, 12, 0), METAL, CFG)
    assert not [lv for lv in levels if lv.period_start == asia.start]


def test_session_key_levels_are_the_latest_complete_instances_per_session():
    now = utc(2024, 1, 10, 12, 0)
    candles = m15(WEEK_OPEN, now, ASIA_EXTREMES)
    levels = session_key_levels(loaded(candles, now), now, METAL, CFG)
    asia = [lv for lv in levels if lv.type is LiquidityPoolType.ASIA_HIGH]
    assert [lv.label for lv in asia] == ["ASIA high 2024-01-09", "ASIA high 2024-01-10"]
    assert asia[0].price == 2035.0 and asia[0].known_at == utc(2024, 1, 9, 5, 0)
    assert {lv.type for lv in levels} >= {LiquidityPoolType.NY_PM_LOW, LiquidityPoolType.LONDON_HIGH}
    assert not [lv for lv in levels if lv.known_at > now]


def test_session_pool_is_a_bsl_pool_active_only_after_the_window_ends():
    now = utc(2024, 1, 9, 12, 0)
    sweep = utc(2024, 1, 9, 8, 0)
    candles = m15(WEEK_OPEN, now, {**ASIA_EXTREMES, sweep: (2030.0, 2036.0, 2029.5, 2034.0)})
    levels = [
        lv
        for lv in session_key_levels(loaded(candles, now), now, METAL, CFG)
        if lv.type is LiquidityPoolType.ASIA_HIGH
    ]
    result = analyze_liquidity(candles, None, None, levels, TrendDirection.NONE, LiquidityConfig.from_spec())
    pool = next(p for p in result.pools if p.type is LiquidityPoolType.ASIA_HIGH and p.price == 2035.0)
    assert pool.side is LiquiditySide.BSL and pool.known_at == utc(2024, 1, 9, 5, 0)
    events = [e for e in result.events if e.pool_id == pool.id]
    # The Asian high candle itself (inside the window) is not an event; the London sweep is.
    assert (events[0].type, events[0].time) == (LiquidityEventType.SWEEP, sweep)


def test_opens_use_only_the_exact_expected_candles():
    now = utc(2024, 1, 9, 12, 0)
    candles = m15(WEEK_OPEN, now, {utc(2024, 1, 8, 23, 0): (2031.0, 2031.5, 2030.5, 2031.0)})
    o = opens(candles, now, METAL, CFG)
    assert (o.daily_open, o.daily_open_time) == (2031.0, utc(2024, 1, 8, 23, 0))  # Monday 18:00 EST
    assert o.ny_midnight_open_time == utc(2024, 1, 9, 5, 0)
    assert o.weekly_open_time == WEEK_OPEN
    assert (
        o.last_close == 2030.0
        and o.daily_change == -1.0
        and o.daily_change_pct == pytest.approx(-0.0492, abs=1e-4)
    )

    missing = opens([c for c in candles if c.open_time != utc(2024, 1, 8, 23, 0)], now, METAL, CFG)
    assert missing.daily_open is None and missing.daily_change is None and missing.weekly_open == 2030.0


def test_previous_session_is_the_latest_complete_instance():
    now = utc(2024, 1, 9, 11, 0)
    candles = m15(WEEK_OPEN, now, ASIA_EXTREMES)
    prev = previous_session(build_instances(candles, now, METAL, CFG), now)
    assert prev is not None and prev.instance_id == "LONDON:2024-01-09" and prev.end == utc(2024, 1, 9, 10, 0)


def _asia_ranges(ranges: list[float]):
    """One Asian session per trading day from Tuesday 2024-01-09, each with the given range around 2030."""
    overrides = {}
    days = [TUE + timedelta(days=d) for d in range(14) if (TUE + timedelta(days=d)).weekday() < 5][
        : len(ranges)
    ]
    for day, r in zip(days, ranges, strict=True):
        start = utc(day.year, day.month, day.day, 2, 0)
        overrides[start] = (2030.0, 2030.0 + r / 2, 2030.0 - r / 2, 2030.0)
    return overrides, days


def test_asian_range_state_compares_only_with_earlier_sessions():
    overrides, days = _asia_ranges([10, 10, 10, 10, 10, 5, 25])
    end = utc(days[-1].year, days[-1].month, days[-1].day, 12, 0)
    instances = build_instances(m15(WEEK_OPEN, end, overrides), end, METAL, CFG)
    asia = [i for i in instances if i.session is SessionName.ASIA and i.state is S.COMPLETE]
    by_day = {i.trading_day: i for i in asia}
    # Monday's Asian session is flat (range 1.0) and still counts as prior history.
    assert (
        by_day[days[3]].asian_range_state is None
    )  # 4 earlier sessions (flat Monday + 3): below minSessions
    assert by_day[days[5]].asian_range_state is AsianRangeState.TIGHT
    assert by_day[days[6]].asian_range_state is AsianRangeState.ABNORMALLY_LARGE
    prior = [i.range for i in asia if i.trading_day < days[6]][-10:]
    assert by_day[days[6]].asian_range_ratio == pytest.approx(25 / (sum(prior) / len(prior)), abs=1e-3)


@pytest.mark.parametrize(
    ("ratio", "state"),
    [
        (0.59, AsianRangeState.TIGHT),
        (0.6, AsianRangeState.NORMAL),
        (1.3, AsianRangeState.EXPANDED),
        (2.0, AsianRangeState.ABNORMALLY_LARGE),
    ],
)
def test_asian_range_thresholds(ratio, state):
    assert classify_asian_range(ratio, CFG.asian_range) is state


@pytest.mark.parametrize(
    ("pct", "state"),
    [
        (24.9, ExpansionState.CONSOLIDATING),
        (25, ExpansionState.EARLY),
        (50, ExpansionState.ACTIVE),
        (80, ExpansionState.LATE),
        (110, ExpansionState.EXHAUSTED),
    ],
)
def test_expansion_thresholds(pct, state):
    assert classify_expansion(pct, CFG.adr) is state


def test_adr_uses_only_prior_complete_days_and_needs_the_full_period():
    now = utc(2024, 1, 29, 12, 0)  # Monday 07:00 EST, trading day 2024-01-29
    day_start = utc(2024, 1, 28, 23, 0)
    source = m15(day_start, now, {utc(2024, 1, 29, 8, 0): (2030.0, 2035.0, 2025.0, 2030.0)})
    d1 = d1_candles(utc(2024, 1, 7, 22, 0), [20.0] * 15)  # 15 trading days ending Friday 2024-01-26
    future = candle(utc(2024, 1, 28, 22, 0), (2030.0, 2130.0, 1930.0, 2030.0), d1[0].timeframe)
    state = adr_state([*d1, future], source, now, CFG.adr)
    assert state is not None and state.adr == 20.0 and state.current_range == 10.0
    assert state.pct_used == 50.0 and state.expansion is ExpansionState.ACTIVE
    assert adr_state(d1[-13:], source, now, CFG.adr) is None


LONDON_START = utc(2024, 1, 9, 7, 0)


JUDAS_END = utc(2024, 1, 9, 12, 0)


def _judas(extra, end=JUDAS_END, skip=frozenset()):
    candles = m15(WEEK_OPEN, end, {**ASIA_EXTREMES, **extra}, skip)
    instances = build_instances(candles, end, METAL, CFG)
    return [j for j in detect_judas(candles, instances, end, CFG) if j.id == "JUDAS:LONDON:2024-01-09"]


def at(minutes):
    return LONDON_START + timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("extra", "direction", "status", "detail"),
    [
        (
            {at(15): (2030.0, 2036.0, 2029.5, 2034.0), at(30): (2034.0, 2034.5, 2028.5, 2029.0)},
            Direction.BEARISH,
            JudasStatus.CONFIRMED,
            "closed beyond the Asian midpoint",
        ),
        (
            {at(15): (2030.0, 2030.5, 2024.0, 2026.0), at(45): (2026.0, 2031.5, 2025.5, 2031.0)},
            Direction.BULLISH,
            JudasStatus.CONFIRMED,
            "closed beyond the Asian midpoint",
        ),
        (
            {at(15): (2030.0, 2036.0, 2029.5, 2034.0), at(30): (2034.0, 2037.0, 2033.0, 2036.5)},
            Direction.BEARISH,
            JudasStatus.FAILED,
            "closed beyond the sweep extreme",
        ),
        ({at(15): (2030.0, 2036.0, 2029.5, 2034.0)}, Direction.BEARISH, JudasStatus.FAILED, "window ended"),
    ],
)
def test_judas_swing_lifecycle(extra, direction, status, detail):
    [j] = _judas(extra)
    assert (j.direction, j.status, j.detail) == (direction, status, detail)
    assert j.sweep_time == at(15) and (j.asian_high, j.asian_low, j.asian_midpoint) == (
        2035.0,
        2025.0,
        2030.0,
    )


def test_judas_candidate_before_the_window_ends():
    [j] = _judas({at(15): (2030.0, 2036.0, 2029.5, 2034.0)}, end=at(60))
    assert j.status is JudasStatus.CANDIDATE and j.resolved_at is None


@pytest.mark.parametrize(
    ("extra", "skip"),
    [
        ({at(15): (2030.0, 2036.0, 2029.5, 2035.5)}, frozenset()),  # closes beyond: a breakout, not a sweep
        ({at(15): (2030.0, 2036.0, 2024.0, 2030.0)}, frozenset()),  # both sides in one candle
        ({at(15): (2030.0, 2036.0, 2029.5, 2034.0)}, frozenset({utc(2024, 1, 9, 4, 30)})),  # Asia INCOMPLETE
    ],
)
def test_no_judas_swing(extra, skip):
    assert _judas(extra, skip=skip) == []


def test_session_quality_downgrades_when_the_day_is_exhausted():
    now = utc(2024, 1, 29, 14, 0)  # 09:00 EST: NY AM kill zone (IDEAL)
    source = loaded(
        m15(utc(2024, 1, 28, 23, 0), now, {utc(2024, 1, 29, 8, 0): (2030.0, 2060.0, 2000.0, 2030.0)}), now
    )
    d1 = loaded(d1_candles(utc(2024, 1, 7, 22, 0), [20.0] * 15), now)
    a = analyze_sessions(source, d1, METAL, CFG)
    assert a.clock.time_quality is SessionQuality.IDEAL and a.adr is not None
    assert a.adr.expansion is ExpansionState.EXHAUSTED and a.session_quality is SessionQuality.ACCEPTABLE
    assert a.eligible_for_decision and a.ineligibility == []
