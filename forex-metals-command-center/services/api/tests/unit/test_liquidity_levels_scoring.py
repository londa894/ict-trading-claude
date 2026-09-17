from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.domain.enums import (
    DolConfidence,
    LiquidityEventType,
    LiquidityPoolType,
    LiquidityScope,
    LiquiditySide,
    LiquidityState,
    QualifierStatus,
    StructureLevel,
    TrendDirection,
)
from app.services.liquidity.key_levels import key_levels
from app.services.liquidity.models import LiquidityEvent, LiquidityPool
from app.services.liquidity.qualifiers import qualify_level
from app.services.liquidity.scoring import magnet_scores, select_dol
from app.services.structure.engine import analyze_level
from tests.liquidity_helpers import d1_candles, liq_cfg
from tests.structure_helpers import UPTREND_THEN_MSS, cfg, hlc_candles


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# Week 1: Mon 2024-01-08 .. Fri 2024-01-12, week 2: Mon 15 .. Tue 16 (current, incomplete)
WEEK1 = [(utc(2024, 1, d), 2000 + d, 1990 + d) for d in range(8, 13)]
WEEK2 = [(utc(2024, 1, 15), 2040, 1985), (utc(2024, 1, 16), 2030, 2001)]


def by_type(levels, t):
    return [lv for lv in levels if lv.type is t]


def test_pdh_pdl_known_only_at_their_day_close():
    d1 = d1_candles(WEEK1 + WEEK2)
    as_of = utc(2024, 1, 16, 22)
    levels = key_levels(d1, as_of, days=3, weeks=2)
    pdh = by_type(levels, LiquidityPoolType.PDH)
    assert [lv.price for lv in pdh] == [2012, 2040, 2030]  # last 3 closed days
    assert [lv.known_at for lv in pdh] == [utc(2024, 1, 12, 22), utc(2024, 1, 15, 22), utc(2024, 1, 16, 22)]
    assert by_type(levels, LiquidityPoolType.PDL)[-1].price == 2001
    earlier = key_levels(d1, utc(2024, 1, 16, 21, 59), days=3, weeks=2)  # Tuesday not closed yet
    assert 2030 not in [lv.price for lv in by_type(earlier, LiquidityPoolType.PDH)]


def test_pwh_pwl_complete_week_known_at_friday_close():
    d1 = d1_candles(WEEK1 + WEEK2)
    levels = key_levels(d1, utc(2024, 1, 16, 22), days=0, weeks=2)
    [pwh] = by_type(levels, LiquidityPoolType.PWH)
    [pwl] = by_type(levels, LiquidityPoolType.PWL)
    assert (pwh.price, pwl.price) == (2012, 1998)
    assert pwh.known_at == utc(2024, 1, 12, 22)
    # The current, incomplete week produces nothing.
    assert all("2024-01-15" not in lv.label for lv in levels)


def test_holiday_friday_week_is_known_at_next_weeks_first_close():
    d1 = d1_candles(WEEK1[:4] + WEEK2)  # no Friday in week 1
    levels = key_levels(d1, utc(2024, 1, 16, 22), days=0, weeks=1)
    [pwh] = by_type(levels, LiquidityPoolType.PWH)
    assert pwh.price == 2011 and pwh.known_at == utc(2024, 1, 15, 22)
    before = key_levels(d1, utc(2024, 1, 15, 21), days=0, weeks=1)  # can't prove week complete yet
    assert by_type(before, LiquidityPoolType.PWH) == []


# --- magnet score ----------------------------------------------------------------------------------


@dataclass
class P:
    id: str
    type: LiquidityPoolType
    side: LiquiditySide
    price: float
    scope: LiquidityScope = LiquidityScope.EXTERNAL
    touches: int = 0
    member_count: int = 1


def test_magnet_components_and_caps():
    c = liq_cfg()
    pools = [
        P("pwh", LiquidityPoolType.PWH, LiquiditySide.BSL, 101.0),  # 1 ATR away
        P(
            "int", LiquidityPoolType.SWING_HIGH, LiquiditySide.BSL, 150.0, scope=LiquidityScope.INTERNAL
        ),  # 50 ATR
        P("eqh", LiquidityPoolType.EQH, LiquiditySide.BSL, 101.1, member_count=6, touches=3),
    ]
    scores = magnet_scores(pools, last_close=100.0, atr=1.0, trend=TrendDirection.BULLISH, cfg=c)
    m = c.magnet
    # PWH: type 30 + proximity 30*(1-1/10)=27 + stack (eqh within 0.25 ATR) 5 + trend 10
    assert scores["pwh"] == pytest.approx(30 + 27 + 5 + 10)
    # internal swing far away: type 10 + trend 10 (proximity 0, no stack)
    assert scores["int"] == pytest.approx(m.type["INTERNAL_SWING"] + m.trend_alignment)
    # cluster capped at 15
    assert scores["eqh"] == pytest.approx(25 + 15 + 30 * (1 - 1.1 / 10) + 5 + 10)
    assert all(0 <= s <= 100 for s in scores.values())
    assert magnet_scores(pools, 100.0, 0.0, TrendDirection.NONE, c) == {}


def pool(pid, side, price, score, distance, taken=False):
    return LiquidityPool(
        id=pid,
        type=LiquidityPoolType.SWING_HIGH if side is LiquiditySide.BSL else LiquidityPoolType.SWING_LOW,
        side=side,
        scope=LiquidityScope.EXTERNAL,
        label=pid,
        price=price,
        formed_at=utc(2024, 1, 9),
        known_at=utc(2024, 1, 9),
        source_times=[],
        state=LiquidityState.SWEPT if taken else LiquidityState.FRESH,
        touches=0,
        state_changed_at=None,
        taken=taken,
        distance_atr=distance,
        magnet_score=None if taken else score,
    )


def test_dol_primary_secondary_distinct_and_confidence_buckets():
    c = liq_cfg()
    pools = [
        pool("a", LiquiditySide.BSL, 110.0, 80, 5),
        pool("a-dup", LiquiditySide.BSL, 110.1, 79, 5.1),  # same price zone -> not a secondary
        pool("b", LiquiditySide.BSL, 115.0, 60, 7),
        pool("s", LiquiditySide.SSL, 90.0, 50, 5),
        pool("gone", LiquiditySide.SSL, 95.0, 99, 2, taken=True),
    ]
    dol = select_dol(pools, atr=1.0, cfg=c)
    assert dol.primary.pool_id == "a" and dol.secondary.pool_id == "b"
    assert dol.margin == 30 and dol.confidence is DolConfidence.HIGH
    for opposite, expected in (
        (62, DolConfidence.MODERATE),
        (70, DolConfidence.LOW),
        (75, DolConfidence.UNCLEAR),
    ):
        pools[3] = pool("s", LiquiditySide.SSL, 90.0, opposite, 5)
        assert select_dol(pools, 1.0, c).confidence is expected


def test_two_sided_unclear_and_empty_and_zero_atr():
    c = liq_cfg()
    two_sided = [pool("h", LiquiditySide.BSL, 101, 55, 1), pool("l", LiquiditySide.SSL, 99, 52, 1)]
    dol = select_dol(two_sided, 1.0, c)
    assert dol.confidence is DolConfidence.UNCLEAR and "Two-sided" in dol.reason
    assert select_dol([], 1.0, c).confidence is DolConfidence.UNCLEAR
    assert select_dol(two_sided, 0.0, c).primary is None
    one_sided = select_dol([pool("h", LiquiditySide.BSL, 101, 40, 1)], 1.0, c)
    assert one_sided.margin == 40 and one_sided.confidence is DolConfidence.HIGH


# --- qualifiers ------------------------------------------------------------------------------------


def liq_event(candles, index, side, kind=LiquidityEventType.SWEEP):
    return LiquidityEvent(
        id=f"x{index}{side}",
        pool_id="p",
        pool_type=LiquidityPoolType.SWING_HIGH,
        side=side,
        type=kind,
        price=1.0,
        time=candles[index].open_time,
        extreme=1.0,
        close=1.0,
    )


def test_liquidity_qualifier_present_absent_and_no_lookahead():
    candles = hlc_candles(UPTREND_THEN_MSS)  # CHoCH bearish @13, MSS bearish @14, BOS bullish @5, @9
    level = analyze_level(candles, StructureLevel.EXTERNAL, cfg())
    times = [c.open_time for c in candles]
    idx = {c.open_time: i for i, c in enumerate(candles)}

    q = qualify_level(level, [liq_event(candles, 12, LiquiditySide.BSL)], times, lookback_bars=20)
    status = {(idx[e.time], e.type.value, e.status.value): e.liquidity_qualifier for e in q.events}
    assert status[(13, "CHOCH", "CONFIRMED")] is QualifierStatus.PRESENT
    assert status[(14, "MSS", "CONFIRMED")] is QualifierStatus.PRESENT
    assert status[(5, "BOS", "CONFIRMED")] is QualifierStatus.ABSENT  # bullish needs SSL taken
    assert all(e.displacement_qualifier is QualifierStatus.NOT_EVALUATED for e in q.events)

    short = qualify_level(level, [liq_event(candles, 12, LiquiditySide.BSL)], times, lookback_bars=1)
    assert {
        (idx[e.time], e.type.value): e.liquidity_qualifier
        for e in short.events
        if e.status.value == "CONFIRMED"
    }[(14, "MSS")] is QualifierStatus.ABSENT

    later = qualify_level(level, [liq_event(candles, 14, LiquiditySide.BSL)], times, lookback_bars=20)
    choch = next(e for e in later.events if e.type.value == "CHOCH" and e.status.value == "CONFIRMED")
    assert choch.liquidity_qualifier is QualifierStatus.ABSENT  # a sweep after the event never qualifies it

    touches_only = qualify_level(
        level, [liq_event(candles, 12, LiquiditySide.BSL, LiquidityEventType.TOUCH)], times, lookback_bars=20
    )
    assert all(e.liquidity_qualifier is QualifierStatus.ABSENT for e in touches_only.events)
    assert qualify_level(None, [], times, 20) is None
