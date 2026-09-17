"""Hand-built liquidity scenarios with exact expected events (pivot length 1, M5 candles)."""

from app.domain.enums import LiquidityPoolType, LiquidityScope, LiquiditySide, LiquidityState
from app.services.liquidity.models import KeyLevel
from tests.liquidity_helpers import liq_cfg, mirror, run
from tests.structure_helpers import hlc_candles

# Swing high 11.0 at index 1, confirmed by index 2 -> BSL pool active from index 3.
BASE = [(10.0, 9.0, 9.5), (11.0, 9.5, 10.5), (10.5, 9.2, 9.8)]


def events_for(result, candles, pool_id):
    idx = {c.open_time: i for i, c in enumerate(candles)}
    return [(idx[e.time], e.type.value) for e in result.events if e.pool_id == pool_id]


def bsl_id(candles, index=1):
    return f"SWING:BSL:{candles[index].open_time.isoformat()}"


def ssl_id(candles, index=1):
    return f"SWING:SSL:{candles[index].open_time.isoformat()}"


def pool(result, pool_id):
    return next(p for p in result.pools if p.id == pool_id)


def test_pool_only_exists_after_swing_confirmation():
    candles = hlc_candles(BASE[:2])  # swing not yet confirmed
    assert not [p for p in run(candles).pools if p.type is LiquidityPoolType.SWING_HIGH]
    candles = hlc_candles(BASE)  # confirmed on the last candle, but not active until the next candle
    result = run(candles)
    p = pool(result, bsl_id(candles))
    assert p.side is LiquiditySide.BSL and p.price == 11.0 and p.known_at == candles[2].close_time
    assert result.events == []


def test_sweep_wick_through_close_back_inside():
    candles = hlc_candles([*BASE, (11.3, 10.0, 10.8)])
    result = run(candles)
    assert events_for(result, candles, bsl_id(candles)) == [(3, "SWEEP")]
    p = pool(result, bsl_id(candles))
    assert p.state is LiquidityState.SWEPT and p.taken and p.magnet_score is None
    sweep = result.events[0]
    assert sweep.extreme == 11.3 and sweep.close == 10.8 and sweep.price == 11.0


def test_false_sweep_becomes_run():
    candles = hlc_candles([*BASE, (11.3, 10.0, 10.8), (11.6, 10.8, 11.5)])  # close 11.5 > sweep high 11.3
    result = run(candles)
    assert events_for(result, candles, bsl_id(candles)) == [(3, "SWEEP"), (4, "SWEEP_FAILED")]
    assert pool(result, bsl_id(candles)).state is LiquidityState.RUN


def test_sweep_failure_window_expires():
    rows = [
        *BASE,
        (11.3, 10.0, 10.8),
        (10.9, 10.2, 10.5),
        (10.8, 10.1, 10.4),
        (10.7, 10.0, 10.3),
        (11.6, 10.3, 11.5),
    ]
    candles = hlc_candles(rows)
    result = run(candles, cfg=liq_cfg(sweep_failure_window_bars=3))
    assert events_for(result, candles, bsl_id(candles)) == [(3, "SWEEP")]  # index 7 is 4 bars later
    assert pool(result, bsl_id(candles)).state is LiquidityState.SWEPT


def test_break_then_reclaim():
    candles = hlc_candles([*BASE, (11.4, 10.5, 11.2), (11.3, 10.6, 10.9)])
    result = run(candles)
    assert events_for(result, candles, bsl_id(candles)) == [(3, "BREAK"), (4, "RECLAIM")]
    assert pool(result, bsl_id(candles)).state is LiquidityState.RECLAIMED


def test_break_then_run_needs_extension_on_a_later_candle():
    candles = hlc_candles([*BASE, (11.4, 10.5, 11.2), (12.0, 11.1, 11.9)])
    result = run(candles, cfg=liq_cfg(run_extension_atr=0.5))
    assert events_for(result, candles, bsl_id(candles)) == [(3, "BREAK"), (4, "RUN")]
    assert pool(result, bsl_id(candles)).state is LiquidityState.RUN


def test_break_without_continuation_stays_broken_after_window():
    rows = [
        *BASE,
        (11.2, 10.5, 11.05),
        (11.2, 11.0, 11.1),
        (11.2, 11.0, 11.1),
        (11.2, 11.0, 11.1),
        (12.5, 11.1, 12.4),
    ]
    candles = hlc_candles(rows)
    result = run(candles, cfg=liq_cfg(break_acceptance_window_bars=3, run_extension_atr=0.5))
    assert events_for(result, candles, bsl_id(candles)) == [(3, "BREAK")]
    assert pool(result, bsl_id(candles)).state is LiquidityState.BROKEN


def test_touch_episodes_counted_once_each():
    rows = [*BASE, (10.97, 10.0, 10.5), (10.98, 10.1, 10.4), (10.4, 9.9, 10.1), (10.96, 10.0, 10.6)]
    candles = hlc_candles(rows)
    result = run(candles, cfg=liq_cfg(touch_tolerance_atr=0.1))
    assert events_for(result, candles, bsl_id(candles)) == [(3, "TOUCH"), (6, "TOUCH")]
    p = pool(result, bsl_id(candles))
    assert p.touches == 2 and not p.taken and p.state in (LiquidityState.TOUCHED, LiquidityState.APPROACHING)


def test_ssl_mirror_of_sweep_break_reclaim_run():
    for rows, expected in (
        ([*BASE, (11.3, 10.0, 10.8)], [(3, "SWEEP")]),
        ([*BASE, (11.3, 10.0, 10.8), (11.6, 10.8, 11.5)], [(3, "SWEEP"), (4, "SWEEP_FAILED")]),
        ([*BASE, (11.4, 10.5, 11.2), (11.3, 10.6, 10.9)], [(3, "BREAK"), (4, "RECLAIM")]),
        ([*BASE, (11.4, 10.5, 11.2), (12.0, 11.1, 11.9)], [(3, "BREAK"), (4, "RUN")]),
    ):
        candles = hlc_candles(mirror(rows))
        result = run(candles)
        assert events_for(result, candles, ssl_id(candles)) == expected
        assert pool(result, ssl_id(candles)).side is LiquiditySide.SSL


# --- equal highs / lows ---------------------------------------------------------------------------

EQH_ROWS = [
    (10.0, 9.0, 9.5),
    (11.0, 9.5, 10.5),  # swing high 11.00
    (10.5, 9.2, 9.8),
    (10.4, 9.0, 9.3),
    (10.6, 9.1, 10.4),
    (10.98, 10.0, 10.6),  # swing high 10.98, 4 pivots later, within tolerance
    (10.5, 9.6, 9.9),
    (10.3, 9.5, 9.7),
]


def eq_pools(result, eq_type):
    return [p for p in result.pools if p.type is eq_type]


def test_eqh_cluster_forms_with_outermost_price():
    candles = hlc_candles(EQH_ROWS)
    result = run(candles, cfg=liq_cfg(equal_tolerance_atr=0.5, equal_min_bars_apart=3))
    [eqh] = eq_pools(result, LiquidityPoolType.EQH)
    assert eqh.price == 11.0 and eqh.side is LiquiditySide.BSL
    assert eqh.source_times == [candles[1].open_time, candles[5].open_time]
    assert eqh.known_at == candles[7].open_time  # active from the candle after the 2nd swing confirmed


def test_eqh_swept_takes_the_cluster():
    candles = hlc_candles([*EQH_ROWS, (11.1, 9.8, 10.2)])
    result = run(candles, cfg=liq_cfg(equal_tolerance_atr=0.5, equal_min_bars_apart=3))
    [eqh] = eq_pools(result, LiquidityPoolType.EQH)
    assert events_for(result, candles, eqh.id)[-1] == (8, "SWEEP")
    assert eqh.state is LiquidityState.SWEPT


def test_no_eqh_when_too_close_outside_tolerance_or_first_already_taken():
    rows = [
        (10, 9, 9.5),
        (11, 9.5, 10.5),
        (10.5, 9.2, 9.8),
        (10.98, 9.3, 10),
        (10.4, 9.1, 9.5),
        (10.3, 9, 9.2),
    ]
    assert not eq_pools(
        run(hlc_candles(rows), cfg=liq_cfg(equal_tolerance_atr=0.5, equal_min_bars_apart=3)),
        LiquidityPoolType.EQH,
    )  # pivots only 2 apart
    assert not eq_pools(
        run(hlc_candles(EQH_ROWS), cfg=liq_cfg(equal_tolerance_atr=0.001, equal_min_bars_apart=3)),
        LiquidityPoolType.EQH,
    )  # tolerance too tight
    taken = list(EQH_ROWS)
    taken[4] = (11.2, 9.1, 10.4)  # sweeps 11.0 before the second swing forms (and becomes the pivot itself)
    result = run(hlc_candles(taken), cfg=liq_cfg(equal_tolerance_atr=0.5, equal_min_bars_apart=3))
    assert not [p for p in eq_pools(result, LiquidityPoolType.EQH) if p.price == 11.0]


def test_eql_mirror():
    candles = hlc_candles(mirror(EQH_ROWS))
    [eql] = eq_pools(
        run(candles, cfg=liq_cfg(equal_tolerance_atr=0.5, equal_min_bars_apart=3)), LiquidityPoolType.EQL
    )
    assert eql.price == 9.0 and eql.side is LiquiditySide.SSL


# --- scope and key levels -------------------------------------------------------------------------


def test_swing_scope_upgrades_to_external_only_after_external_confirmation():
    rows = [
        (10, 9, 9.5),
        (10.5, 9.2, 10),
        (11, 9.5, 10.5),
        (10.5, 9.2, 9.8),
        (10.2, 9.1, 9.6),
        (10.1, 9.0, 9.4),
    ]
    candles = hlc_candles(rows)
    pid = bsl_id(candles, 2)
    # internal pivot (L=1) confirms at index 3, external pivot (L=2) at index 4
    before = run(hlc_candles(rows[:4]), pivot=1, external_pivot=2)
    at_confirmation = run(hlc_candles(rows[:5]), pivot=1, external_pivot=2)
    after = run(candles, pivot=1, external_pivot=2)
    assert pool(before, pid).scope is LiquidityScope.INTERNAL
    assert pool(at_confirmation, pid).scope is LiquidityScope.EXTERNAL  # known as of that close
    assert pool(after, pid).scope is LiquidityScope.EXTERNAL


def test_key_level_cannot_be_swept_before_it_is_known():
    candles = hlc_candles([*BASE, (11.3, 10.0, 10.8), (10.9, 10.0, 10.2), (11.4, 10.1, 10.6)])
    level = KeyLevel(
        type=LiquidityPoolType.PDH,
        price=11.2,
        period_start=candles[0].open_time,
        known_at=candles[4].open_time,
        label="PDH test",
    )
    result = run(candles, key_levels=[level])
    pid = f"PDH:{candles[0].open_time.isoformat()}"
    # candle 3 (high 11.3) is before the level is known; only candle 5 (high 11.4) can sweep it
    assert events_for(result, candles, pid) == [(5, "SWEEP")]
    assert pool(result, pid).scope is LiquidityScope.EXTERNAL


def test_approaching_is_derived_from_the_last_close_only():
    candles = hlc_candles([*BASE, (10.9, 10.4, 10.85)])
    result = run(candles, cfg=liq_cfg(approach_distance_atr=0.5, touch_tolerance_atr=0.01))
    p = pool(result, bsl_id(candles))
    assert p.state is LiquidityState.APPROACHING and not p.taken
    assert not [e for e in result.events if e.pool_id == p.id]  # APPROACHING is never an event
