"""Property tests: no lookahead, only allowed transitions, and never a trade-authority state."""

import random
from collections import Counter
from datetime import timedelta
from itertools import pairwise

import pytest

from app.domain.enums import AUTHORITY_SETUP_STATES, Direction, EntryMode, SetupState, Timeframe
from app.services.analysis.pipeline import run_pipeline
from app.services.entry.models import EntryConfig
from app.services.liquidity.models import LiquidityConfig
from app.services.sessions.clock import trading_day_of
from app.services.setup_state.engine import ALLOWED, SetupInputs, analyze_setups
from app.services.setup_state.models import BiasPoint, SetupConfig
from app.services.structure.models import StructureConfig
from tests.session_helpers import WEEK_OPEN, loaded, utc, walk

SEEDS = [1, 2, 3, 7, 11]
END = utc(2024, 1, 12, 21, 0)
CFG = SetupConfig.from_spec()


def bias_points(seed):
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    points, t = [], WEEK_OPEN - timedelta(hours=1)
    while t < END:
        d = Direction.BULLISH if rng.random() < 0.6 else Direction.BEARISH
        for tf in (Timeframe.H4, Timeframe.H1):
            points.append(
                BiasPoint(timeframe=tf, direction=d, known_at=t, event_id=f"{tf.value}:{t.isoformat()}")
            )
        t += timedelta(hours=rng.randint(6, 30))
    return points


def run(candles, points, entry_cfg=None):
    as_of = candles[-1].close_time
    series = loaded(candles, as_of + timedelta(seconds=1))
    # Key levels and session pools are windowed as of the last candle (latest N), so the pool universe of a
    # longer history can drop older levels; swing/EQ pools are prefix-stable. See PHASE_7 known limitations.
    result = run_pipeline(series, None, StructureConfig.from_spec(), LiquidityConfig.from_spec())
    inputs = SetupInputs(
        structure_events=result.structure.events,
        pools=result.liquidity.pools,
        liquidity_events=result.liquidity.events,
        pd_zones=result.pd_arrays.zones,
        pd_events=result.pd_arrays.events,
        bias_points=points,
        bias_timeframes=[Timeframe.H4, Timeframe.H1],
        no_wick_events=result.no_wick.events,
    )
    return analyze_setups(candles, inputs, CFG, trading_day_of, entry_cfg)


@pytest.mark.parametrize("mode", [EntryMode.STANDARD, EntryMode.AGGRESSIVE])
@pytest.mark.parametrize("seed", SEEDS)
def test_prefix_events_agree_with_full_history(seed, mode):
    entry_cfg = EntryConfig.from_spec(mode)
    candles = walk(WEEK_OPEN, END, seed)
    points = bias_points(seed)
    full = run(candles, points, entry_cfg)
    for k in range(60, len(candles), 53):
        prefix = run(candles[:k], points, entry_cfg)
        last = candles[k - 1].open_time
        assert prefix.events == [e for e in full.events if e.time <= last], (seed, k)


@pytest.mark.parametrize("mode", [EntryMode.STANDARD, EntryMode.AGGRESSIVE])
@pytest.mark.parametrize("seed", SEEDS)
def test_transitions_are_allowed_and_authority_states_never_emitted(seed, mode):
    result = run(walk(WEEK_OPEN, END, seed), bias_points(seed), EntryConfig.from_spec(mode))
    by_setup: dict[str, list[SetupState]] = {}
    for e in result.events:
        assert e.state not in AUTHORITY_SETUP_STATES  # never LONG_READY / SHORT_READY / ACTIVE / CLOSED
        by_setup.setdefault(e.setup_id, []).append(e.state)
    for sid, seq in by_setup.items():
        assert seq[0] is SetupState.DISCOVERED, sid
        for a, b in pairwise(seq):
            assert b in ALLOWED[a], (sid, a, b)
        terminal = (SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.ENTRY_MISSED)
        assert all(s not in terminal for s in seq[:-1])
    open_setups = [s for s in result.setups if not s.terminal]
    assert len(open_setups) <= 1
    for setup in result.setups:
        plan = setup.entry_plan
        if plan is not None:  # every plan passed chase protection when it was made
            assert plan.rr1 >= plan.min_rr and plan.risk > 0
            sign = 1 if plan.direction is Direction.BULLISH else -1
            assert sign * (plan.tp1 - plan.entry) > 0 and sign * (plan.entry - plan.stop) > 0


def test_random_walks_exercise_the_lifecycle():
    seen: Counter[str] = Counter()
    for seed in SEEDS:
        seen.update(e.state.value for e in run(walk(WEEK_OPEN, END, seed), bias_points(seed)).events)
    for state in (
        "DISCOVERED",
        "WATCH",
        "SETUP_FORMING",
        "LIQUIDITY_EVENT",
        "WAITING_FOR_MSS",
        "INVALIDATED",
        "EXPIRED",
    ):
        assert seen[state], (state, seen)
