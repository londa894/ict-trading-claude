"""REVERSAL_NO_WICK_IFVG state machine — Phase D step 1 (discovery -> rebalance -> reaction)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.candle import Candle
from app.domain.enums import (
    DataQuality,
    Direction,
    DisplacementGrade,
    HtfBias,
    ReversalOriginKind,
    SetupState,
    Timeframe,
)
from app.services.pd_arrays.models import DisplacementEvent
from app.services.reversal.engine import analyze_reversals, bias_gate_allows
from app.services.reversal.models import OriginZone, ReversalConfig

CFG = ReversalConfig.from_spec()
T0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)


def _bar(i, o, h, low, c):
    t = T0 + timedelta(minutes=15 * i)
    return Candle(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=t,
        close_time=t + timedelta(minutes=15),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )


def _bearish_origin(tf=Timeframe.H1):
    # Bearish no-wick origin: imbalance ABOVE. Body [105, 110], far edge (origin high) 111.
    return OriginZone(
        id=f"NW:BEARISH:{tf.value}",
        kind=ReversalOriginKind.NO_WICK,
        timeframe=tf,
        direction=Direction.BEARISH,
        body_top=110.0,
        body_bottom=105.0,
        far_edge=111.0,
        weight=CFG.origin_weights.get(tf.value, 0.0),
        known_at=T0,
    )


def _states(result):
    return [(e.state, e.detail) for e in result.events]


def test_bearish_reversal_discovers_rebalances_and_reacts():
    origin = _bearish_origin()
    candles = [
        _bar(0, 103, 104, 102, 103.5),  # below the zone -> REBALANCE_WATCH
        _bar(1, 103.5, 106, 103, 105.8),  # high 106 >= body_bottom 105 -> REBALANCE_TOUCH
        _bar(2, 105, 108, 104, 104.2),  # upper rejection wick, closes back below 105 -> REACTION
    ]
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG)
    assert [s for s, _ in _states(result)] == [
        SetupState.REBALANCE_WATCH,
        SetupState.REBALANCE_TOUCH,
        SetupState.REACTION,
    ]
    setup = result.setups[0]
    assert setup.direction is Direction.BEARISH and setup.state is SetupState.REACTION
    assert setup.rebalanced_at is not None and setup.reaction_at is not None and not setup.terminal


def test_bullish_reversal_mirror():
    # Bullish no-wick origin: imbalance BELOW. Body [90, 95], far edge (origin low) 89.
    origin = OriginZone(
        id="NW:BULLISH:H1",
        kind=ReversalOriginKind.NO_WICK,
        timeframe=Timeframe.H1,
        direction=Direction.BULLISH,
        body_top=95.0,
        body_bottom=90.0,
        far_edge=89.0,
        weight=0.5,
        known_at=T0,
    )
    candles = [
        _bar(0, 97, 98, 96, 97),  # above the zone -> REBALANCE_WATCH
        _bar(1, 96, 97, 94, 94.5),  # low 94 <= body_top 95 -> REBALANCE_TOUCH
        _bar(2, 95, 96, 92, 95.8),  # lower rejection wick, closes back above 95 -> REACTION
    ]
    result = analyze_reversals([origin], candles, HtfBias.RANGING, [], CFG)
    assert result.setups[0].state is SetupState.REACTION
    assert [s for s, _ in _states(result)][-1] is SetupState.REACTION


def test_reaction_via_opposite_displacement_close():
    origin = _bearish_origin()
    candles = [
        _bar(0, 103, 106, 103, 105.5),  # touches immediately -> REBALANCE_TOUCH
        _bar(1, 105.5, 106, 101, 101.5),  # no big wick, but a bearish displacement lands here
    ]
    disp = [
        DisplacementEvent(
            id="D",
            direction=Direction.BEARISH,
            grade=DisplacementGrade.STRONG,
            magnitude_atr=3.0,
            leg_start=candles[1].open_time,
            time=candles[1].open_time,
            candle_count=1,
            avg_body_pct=0.9,
        )
    ]
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, disp, CFG)
    assert result.setups[0].state is SetupState.REACTION


def test_invalidated_when_close_beyond_far_edge():
    origin = _bearish_origin()
    candles = [_bar(0, 108, 112, 107, 111.5)]  # closes 111.5 above far edge 111 -> INVALIDATED
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG)
    assert result.setups[0].state is SetupState.INVALIDATED and result.setups[0].terminal


def test_expires_without_rebalance():
    origin = _bearish_origin()
    cfg = replace(CFG, rebalance_window_bars=2)
    candles = [_bar(i, 100, 101, 99, 100.5) for i in range(5)]  # never reaches the zone
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], cfg)
    assert result.setups[0].state is SetupState.EXPIRED


def test_expires_without_reaction():
    origin = _bearish_origin()
    cfg = replace(CFG, reaction_window_bars=1)
    candles = [
        _bar(0, 103, 106, 103, 105.5),  # REBALANCE_TOUCH
        _bar(1, 105.5, 106, 105, 105.5),  # no reaction
        _bar(2, 105.5, 106, 105, 105.5),  # window exceeded -> EXPIRED
    ]
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], cfg)
    assert result.setups[0].state is SetupState.EXPIRED


def test_bias_gate_blocks_htf_origin_in_a_directional_trend():
    # H1 origin with a directional (BULLISH) bias -> blocked (no setup discovered).
    assert bias_gate_allows(_bearish_origin(Timeframe.H1), HtfBias.BULLISH, CFG) is False
    result = analyze_reversals(
        [_bearish_origin(Timeframe.H1)], [_bar(0, 103, 106, 103, 105.5)], HtfBias.BULLISH, [], CFG
    )
    assert result.setups == []


def test_bias_gate_allows_d1_origin_even_in_a_directional_trend():
    # A D1 origin outranks the bias and is always allowed.
    assert bias_gate_allows(_bearish_origin(Timeframe.D1), HtfBias.BULLISH, CFG) is True
    result = analyze_reversals(
        [_bearish_origin(Timeframe.D1)], [_bar(0, 103, 106, 103, 105.5)], HtfBias.BULLISH, [], CFG
    )
    assert len(result.setups) == 1 and result.setups[0].state is SetupState.REBALANCE_TOUCH


# --- Phase D2: confirmation -> ARMED --------------------------------------------------------------
from app.domain.enums import (  # noqa: E402
    IfvgStatus,
    PdArrayEventType,
    PdArrayState,
    PdArrayType,
    ReversalConfirmation,
)
from app.services.pd_arrays.models import PdArrayEvent, PdArrayZone  # noqa: E402


def _zone(zid, ptype, direction, created, top, bottom, status=None):
    return PdArrayZone(
        id=zid,
        type=ptype,
        direction=direction,
        top=top,
        bottom=bottom,
        midpoint=(top + bottom) / 2,
        size_atr=1.0,
        source_times=[created],
        created_at=created,
        known_at=created,
        state=PdArrayState.FRESH,
        fill_pct=0.0,
        ifvg_status=status,
        parent_id=None,
        displacement_grade=None,
        state_changed_at=None,
        invalidated_at=None,
        age_bars=0,
        active=True,
        quality_score=50.0,
    )


def _bearish_to_reaction():
    # candles 0..2 reach REACTION; candle 3 is the confirmation candle.
    return _bearish_origin(), [
        _bar(0, 103, 104, 102, 103.5),
        _bar(1, 103.5, 106, 103, 105.8),
        _bar(2, 105, 108, 104, 104.2),
        _bar(3, 104, 104.5, 103, 104.0),
    ]


def test_armed_by_new_fvg_from_the_reversal_leg():
    origin, candles = _bearish_to_reaction()
    fvg = _zone("FVG:BEARISH:x", PdArrayType.FVG, Direction.BEARISH, candles[3].open_time, 103.0, 102.0)
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg])
    s = result.setups[0]
    assert s.state is SetupState.SETUP_ARMED and s.armed_at is not None
    assert s.confirmations == [ReversalConfirmation.NEW_FVG] and s.confirming_zone_ids == ["FVG:BEARISH:x"]
    assert s.protective_level == origin.far_edge
    assert [e.state for e in result.events][-2:] == [SetupState.CONFIRMATION, SetupState.SETUP_ARMED]


def test_armed_by_fresh_imr():
    origin, candles = _bearish_to_reaction()
    imr = _zone("IMR:BEARISH:x", PdArrayType.IMR, Direction.BEARISH, candles[3].open_time, 103.5, 102.5)
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[imr])
    assert result.setups[0].confirmations == [ReversalConfirmation.IMR]


def test_armed_by_ifvg_flip_at_the_origin():
    origin, candles = _bearish_to_reaction()
    # A confirmed IFVG overlapping the origin body [105,110], in the reversal (BEARISH) direction.
    ifvg = _zone(
        "IFVG:BEARISH:x",
        PdArrayType.IFVG,
        Direction.BEARISH,
        candles[1].open_time,
        109.0,
        106.0,
        status=IfvgStatus.CONFIRMED_IFVG,
    )
    ev = PdArrayEvent(
        id="e",
        zone_id="IFVG:BEARISH:x",
        zone_type=PdArrayType.IFVG,
        direction=Direction.BEARISH,
        type=PdArrayEventType.IFVG_CONFIRMED,
        time=candles[3].open_time,
        price=104.0,
        detail="",
    )
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[ifvg], pd_events=[ev])
    assert result.setups[0].confirmations == [ReversalConfirmation.IFVG_FLIP]


def test_ifvg_flip_not_at_origin_does_not_confirm():
    origin, candles = _bearish_to_reaction()
    # A confirmed IFVG far below the origin body (no overlap) must NOT arm the reversal.
    ifvg = _zone(
        "IFVG:BEARISH:far",
        PdArrayType.IFVG,
        Direction.BEARISH,
        candles[1].open_time,
        100.0,
        98.0,
        status=IfvgStatus.CONFIRMED_IFVG,
    )
    ev = PdArrayEvent(
        id="e",
        zone_id="IFVG:BEARISH:far",
        zone_type=PdArrayType.IFVG,
        direction=Direction.BEARISH,
        type=PdArrayEventType.IFVG_CONFIRMED,
        time=candles[3].open_time,
        price=104.0,
        detail="",
    )
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[ifvg], pd_events=[ev])
    assert result.setups[0].state is SetupState.REACTION  # no valid confirmation on the last candle


def test_wrong_direction_fvg_does_not_confirm():
    origin, candles = _bearish_to_reaction()
    fvg = _zone("FVG:BULLISH:x", PdArrayType.FVG, Direction.BULLISH, candles[3].open_time, 103.0, 102.0)
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg])
    assert result.setups[0].state is SetupState.REACTION


def test_expires_without_confirmation():
    origin, candles = _bearish_to_reaction()
    cfg = replace(CFG, flip_window_bars=1)
    extra = [*candles, _bar(4, 104, 104.5, 103, 104), _bar(5, 104, 104.5, 103, 104)]
    result = analyze_reversals([origin], extra, HtfBias.UNCLEAR, [], cfg)
    assert result.setups[0].state is SetupState.EXPIRED


# --- Phase D3: entry plan + target + ATR-scaled stop ----------------------------------------------
from app.domain.enums import LiquidityPoolType, LiquidityScope, LiquiditySide, LiquidityState  # noqa: E402
from app.services.liquidity.models import LiquidityPool  # noqa: E402


def _pool(pid, side, price, ptype, taken=False):
    return LiquidityPool(
        id=pid, type=ptype, side=side, scope=LiquidityScope.EXTERNAL, label=pid, price=price,
        formed_at=T0, known_at=T0, source_times=[], state=LiquidityState.FRESH, touches=0,
        state_changed_at=None, taken=taken, distance_atr=1.0, magnet_score=50.0,
    )


def test_plan_built_at_arm_with_entry_stop_target_rr():
    origin, candles = _bearish_to_reaction()
    fvg = _zone("FVG:BEARISH:x", PdArrayType.FVG, Direction.BEARISH, candles[3].open_time, 103.0, 102.0)
    pools = [_pool("PDL", LiquiditySide.SSL, 100.0, LiquidityPoolType.PDL)]
    result = analyze_reversals(
        [origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg], liquidity_pools=pools, atr=1.0,
    )
    s = result.setups[0]
    assert s.state is SetupState.SETUP_ARMED
    assert s.entry_price == 102.5 and s.entry_zone_id == "FVG:BEARISH:x"  # FVG midpoint
    assert s.stop_price == pytest.approx(111.0 + 0.25 * 1.0)  # far edge + stopBufferAtr*ATR
    assert s.target_price == 100.0 and s.target_pool_id == "PDL"
    assert s.rr == pytest.approx(abs(100.0 - 102.5) / abs(102.5 - 111.25), abs=0.01)


def test_entry_picks_tightest_confirming_structure():
    origin, candles = _bearish_to_reaction()
    t = candles[3].open_time
    fvg = _zone("FVG:wide", PdArrayType.FVG, Direction.BEARISH, t, 103.0, 102.0)   # size 1.0
    imr = _zone("IMR:tight", PdArrayType.IMR, Direction.BEARISH, t, 103.5, 103.0)  # size 0.5 (tighter)
    result = analyze_reversals([origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg, imr], atr=1.0)
    s = result.setups[0]
    assert s.entry_zone_id == "IMR:tight" and s.entry_price == 103.25


def test_target_nearest_opposite_side_tier_tiebreak():
    origin, candles = _bearish_to_reaction()
    fvg = _zone("FVG:BEARISH:x", PdArrayType.FVG, Direction.BEARISH, candles[3].open_time, 103.0, 102.0)
    # Two SSL draws at the same distance below entry; the higher tier (PDL=day) beats the session low.
    pools = [
        _pool("SESS", LiquiditySide.SSL, 100.0, LiquidityPoolType.NY_AM_LOW),
        _pool("PDL", LiquiditySide.SSL, 100.0, LiquidityPoolType.PDL),
    ]
    result = analyze_reversals(
        [origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg], liquidity_pools=pools, atr=1.0,
    )
    assert result.setups[0].target_pool_id == "PDL"


def test_no_target_when_no_opposite_side_pool():
    origin, candles = _bearish_to_reaction()
    fvg = _zone("FVG:BEARISH:x", PdArrayType.FVG, Direction.BEARISH, candles[3].open_time, 103.0, 102.0)
    # Only a BSL pool (wrong side for a bearish reversal) -> no target, no rr.
    pools = [_pool("PDH", LiquiditySide.BSL, 120.0, LiquidityPoolType.PDH)]
    result = analyze_reversals(
        [origin], candles, HtfBias.UNCLEAR, [], CFG, pd_zones=[fvg], liquidity_pools=pools, atr=1.0,
    )
    s = result.setups[0]
    assert s.entry_price is not None and s.target_price is None and s.rr is None


# --- Phase D4a: ARMED -> ENTRY_ZONE -> BLOCKED + chase guard ---------------------------------------


def _armed_setup(extra, cfg=CFG, pools=None):
    origin, base = _bearish_to_reaction()  # arms on candle 3 via a bearish NEW_FVG (entry 102.5)
    candles = base + extra
    fvg = _zone("FVG:BEARISH:x", PdArrayType.FVG, Direction.BEARISH, base[3].open_time, 103.0, 102.0)
    pools = pools if pools is not None else [_pool("PDL", LiquiditySide.SSL, 100.0, LiquidityPoolType.PDL)]
    return analyze_reversals(
        [origin], candles, HtfBias.UNCLEAR, [], cfg, pd_zones=[fvg], liquidity_pools=pools, atr=1.0
    )


def test_armed_reaches_blocked_on_entry_retest():
    result = _armed_setup([_bar(4, 101, 103, 101, 101.5)])  # high 103 >= entry 102.5, low 101 > target 100
    s = result.setups[0]
    assert s.state is SetupState.BLOCKED
    assert [e.state for e in result.events][-2:] == [SetupState.ENTRY_ZONE_TOUCHED, SetupState.BLOCKED]


def test_chase_guard_entry_missed_when_target_hit_first():
    result = _armed_setup([_bar(4, 101, 101.5, 99, 100)])  # low 99 <= target 100, entry 102.5 not touched
    assert result.setups[0].state is SetupState.ENTRY_MISSED and result.setups[0].terminal


def test_armed_expires_without_entry_retest():
    cfg = replace(CFG, entry_window_bars=1)
    result = _armed_setup([_bar(4, 101, 101.5, 100.5, 101), _bar(5, 101, 101.5, 100.5, 101)], cfg=cfg)
    assert result.setups[0].state is SetupState.EXPIRED
