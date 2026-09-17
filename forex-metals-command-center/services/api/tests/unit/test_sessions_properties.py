"""Property tests: session instances, Judas swings, pools and ADR never use data after `as_of`."""

import pytest

from app.domain.enums import AssetClass, JudasStatus, SessionInstanceState, SessionQuality
from app.services.no_wick.engine import NoWickInputs, analyze_no_wick
from app.services.no_wick.models import NoWickConfig
from app.services.sessions.adr import adr_state
from app.services.sessions.analysis import session_key_levels
from app.services.sessions.judas import detect_judas
from app.services.sessions.levels import build_instances
from tests.session_helpers import CFG, WEEK_OPEN, d1_candles, loaded, utc, walk

METAL = AssetClass.METAL
SEEDS = [1, 2, 3, 7, 11]
END = utc(2024, 1, 19, 21, 0)  # two trading weeks


@pytest.mark.parametrize("seed", SEEDS)
def test_prefix_instances_and_judas_agree_with_full_history(seed):
    candles = walk(WEEK_OPEN, END, seed)
    full_as_of = candles[-1].close_time
    full = {i.id: i for i in build_instances(candles, full_as_of, METAL, CFG)}
    full_judas = {j.id: j for j in detect_judas(candles, list(full.values()), full_as_of, CFG)}
    for k in range(40, len(candles), 37):
        as_of = candles[k - 1].close_time
        prefix = build_instances(candles, as_of, METAL, CFG)
        # Passing the FULL candle list with an earlier as_of must equal passing only the prefix.
        assert prefix == build_instances(candles[:k], as_of, METAL, CFG)
        for inst in prefix:
            assert inst.id in full
            if inst.state in (SessionInstanceState.COMPLETE, SessionInstanceState.INCOMPLETE):
                assert inst == full[inst.id], (k, inst.id)
            else:
                assert full[inst.id].start >= inst.start and (inst.high or 0) <= (full[inst.id].high or 0)
        for j in detect_judas(candles, prefix, as_of, CFG):
            f = full_judas[j.id]
            assert (j.sweep_time, j.direction, j.sweep_extreme) == (
                f.sweep_time,
                f.direction,
                f.sweep_extreme,
            )
            if j.status is JudasStatus.CANDIDATE:
                assert f.resolved_at is None or f.resolved_at > as_of
            else:
                assert j == f, (k, j.id)
        for level in session_key_levels(loaded(candles, as_of), as_of, METAL, CFG):
            assert level.known_at <= as_of


@pytest.mark.parametrize("seed", SEEDS)
def test_grammar_of_instances_and_judas(seed):
    candles = walk(WEEK_OPEN, END, seed)
    as_of = candles[-1].close_time
    instances = build_instances(candles, as_of, METAL, CFG)
    for i in instances:
        if i.state is SessionInstanceState.COMPLETE:
            assert i.candle_count == i.expected_count and i.known_at == i.end <= as_of
            assert i.low is not None and i.high is not None and i.low <= i.midpoint <= i.high  # type: ignore[operator]
        else:
            assert i.known_at is None and i.asian_range_state is None
    judas = detect_judas(candles, instances, as_of, CFG)
    for j in judas:
        assert j.asian_low < j.asian_midpoint < j.asian_high
        assert (j.resolved_at is None) == (j.status is JudasStatus.CANDIDATE)
    assert instances and any(i.asian_range_state for i in instances)


def test_random_walks_exercise_judas_outcomes():
    statuses = set()
    for seed in SEEDS:
        candles = walk(WEEK_OPEN, END, seed)
        as_of = candles[-1].close_time
        statuses |= {
            j.status for j in detect_judas(candles, build_instances(candles, as_of, METAL, CFG), as_of, CFG)
        }
    assert statuses == {JudasStatus.CONFIRMED, JudasStatus.FAILED}


def test_adr_ignores_d1_candles_of_the_current_or_later_days():
    candles = walk(WEEK_OPEN, END, 5)
    d1 = d1_candles(utc(2023, 12, 17, 22, 0), [15.0] * 30)
    for k in range(100, len(candles), 97):
        as_of = candles[k - 1].close_time
        visible = [c for c in d1 if c.close_time <= as_of]
        assert adr_state(d1, candles[:k], as_of, CFG.adr) == adr_state(visible, candles[:k], as_of, CFG.adr)


def test_no_wick_session_component_uses_only_the_candle_time():
    candles = walk(WEEK_OPEN, utc(2024, 1, 10, 21, 0), 3)
    calls = []

    def quality(t):
        calls.append(t)
        return SessionQuality.IDEAL

    result = analyze_no_wick(candles, NoWickInputs([], [], [], [], [], quality), NoWickConfig.from_spec())
    assert result.events
    times = {c.open_time for c in candles}
    assert set(calls) <= times
    for e in result.events:
        [session] = [c for c in e.context_components if c.factor.value == "SESSION"]
        assert session.status.value == "EVALUATED" and session.points == 10.0
