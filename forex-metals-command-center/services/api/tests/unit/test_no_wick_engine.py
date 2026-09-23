"""Hand-built No Wick scenarios: classification, strength, scoring components, zones (synthetic prices)."""

from types import SimpleNamespace

import pytest

from app.domain.enums import (
    Direction,
    DisplacementGrade,
    LiquidityEventType,
    LiquiditySide,
    NoWickClassification,
    NoWickContextFactor,
    NoWickStrength,
    NoWickZoneEventType,
    NoWickZoneState,
    PdArrayEventType,
    PdArrayType,
    ScoreComponentStatus,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
)
from app.services.no_wick.engine import NoWickInputs, analyze_no_wick
from app.services.no_wick.features import compute_features
from app.services.no_wick.models import NoWickConfig
from app.services.no_wick.scoring import relevance
from tests.pd_helpers import BASE, mirror, ohlc_candles

C = NoWickClassification
E = NoWickZoneEventType
S = NoWickZoneState
F = NoWickContextFactor
CFG = NoWickConfig.from_spec()
NONE = NoWickInputs(None, None, None, None, None)
EMPTY = NoWickInputs([], [], [], [], [])

# Zone-source candle at index 14 (ATR = 1): bullish, no upper wick, lower wick 0.8, body 1.2 (60%), STRONG.
# close_level 12, open 10.8, origin extreme 10 -> 25% 11.7, 50% 11.4, 75% 11.1.
SOURCE = (10.8, 12.0, 10.0, 12.0)


def run(rows, inputs=EMPTY):
    candles = ohlc_candles(rows)
    return candles, analyze_no_wick(candles, inputs, CFG)


def event_at(result, candles, index):
    t = candles[index].open_time
    return next((e for e in result.events if e.time == t), None)


def zone_sequence(result, zone_id):
    return [e.type for e in result.zone_events if e.zone_id == zone_id]


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize(
    ("row", "bull", "bear", "strength", "tags"),
    [
        ((10, 11, 10, 11), C.TRUE_BULLISH_MARUBOZU, C.TRUE_BEARISH_MARUBOZU, "MEANINGFUL", "OD"),
        ((10.1, 11, 10, 10.95), C.NEAR_BULLISH_MARUBOZU, C.NEAR_BEARISH_MARUBOZU, "MEANINGFUL", "D"),
        ((10, 12, 10, 11.2), C.BULLISH_NO_LOWER_WICK, C.BEARISH_NO_UPPER_WICK, "STRONG", "O"),
        (SOURCE, C.BULLISH_NO_UPPER_WICK, C.BEARISH_NO_LOWER_WICK, "STRONG", "D"),
        ((10, 11.6, 10, 11.6), C.TRUE_BULLISH_MARUBOZU, C.TRUE_BEARISH_MARUBOZU, "EXCEPTIONAL", "OD"),
    ],
)
def test_shapes_strength_and_tags(row, bull, bear, strength, tags, flip):
    rows = [*BASE, row]
    candles, result = run(mirror(rows) if flip else rows)
    e = event_at(result, candles, 14)
    assert e is not None
    assert e.direction is (Direction.BEARISH if flip else Direction.BULLISH)
    assert e.shape is (bear if flip else bull) and e.classification is e.shape
    assert e.strength is NoWickStrength(strength)
    expected_tags = [
        t for code, t in (("O", C.NO_ORIGIN_SIDE_WICK), ("D", C.NO_DESTINATION_SIDE_WICK)) if code in tags
    ]
    assert e.tags == expected_tags


def test_small_body_is_insignificant_and_creates_no_zone():
    candles, result = run([*BASE, (10, 10.5, 10, 10.5)])  # body 0.5 ATR
    e = event_at(result, candles, 14)
    assert e.classification is C.INSIGNIFICANT_NO_WICK and e.shape is C.TRUE_BULLISH_MARUBOZU
    assert e.strength is NoWickStrength.INSIGNIFICANT and e.relevance_score == 0.0
    assert e.zone_id is None and result.zones == []


def test_five_percent_origin_wick_qualifies_but_a_larger_rejection_does_not():
    candles, at_limit = run([*BASE, (10.1, 12, 10, 11.3)])  # lower wick exactly 5% of range
    assert event_at(at_limit, candles, 14).shape is C.BULLISH_NO_LOWER_WICK
    candles, above = run([*BASE, (10.12, 12, 10, 11.3)])  # 6%: a real rejection wick, no upper-side escape
    assert event_at(above, candles, 14) is None


def test_one_sided_shape_needs_a_real_body():
    candles, result = run([*BASE, (10, 12, 10, 10.8)])  # no lower wick but body only 40%
    assert event_at(result, candles, 14) is None


@pytest.mark.parametrize("row", [(10, 10, 10, 10), (10, 11, 9, 10)])
def test_zero_range_and_doji_are_not_events(row):
    candles, result = run([*BASE, row])
    assert event_at(result, candles, 14) is None
    if row[1] == row[2]:
        f = result.features[14]
        assert f.body_pct is None and f.upper_wick_pct is None and f.close_location_pct is None


def test_first_candle_without_atr_is_never_classified():
    candles, result = run([(10, 11, 10, 11)])
    assert result.events == [] and result.features[0].body_atr is None
    # From the second candle ATR averages the prior true ranges available (same rule as displacement).
    candles, result = run([(10, 11, 10, 11), (11, 12, 11, 12)])
    assert [e.time for e in result.events] == [candles[1].open_time]


def test_features_use_only_prior_candles():
    candles = ohlc_candles([*BASE, (10, 13, 10, 13)])
    f = compute_features(candles, CFG)[14]
    assert f.body_atr == pytest.approx(3.0) and f.range_to_median == pytest.approx(3.0)
    assert f.body_to_median is None  # prior bodies are all zero


def test_inside_bar_halves_relevance():
    candles, result = run([*BASE, (10, 12, 9, 11.5), (10.2, 11.2, 10.2, 11.2)])
    e = event_at(result, candles, 15)
    assert e.inside_bar and e.strength is NoWickStrength.MEANINGFUL
    plain = relevance(e.candle_quality_score, e.context_score, e.strength, False, CFG)
    assert e.relevance_score == pytest.approx(plain * CFG.inside_bar_multiplier, abs=0.1)


def test_news_driven_is_never_assigned_and_session_news_never_scored():
    _, result = run([*BASE, (10, 11, 10, 11), (11, 12.5, 11, 12.5), (12.5, 12.5, 11, 11)])
    assert result.events
    for e in result.events:
        assert C.NEWS_DRIVEN_NO_WICK not in (e.classification, e.shape, *e.tags)
        by_factor = {c.factor: c for c in e.context_components}
        assert by_factor[F.SESSION].status is ScoreComponentStatus.NOT_EVALUATED
        assert by_factor[F.NEWS].status is ScoreComponentStatus.NOT_EVALUATED


def test_missing_engines_are_not_evaluated_not_zero_scored():
    candles, result = run([*BASE, (10, 11, 10, 11)], NONE)
    e = event_at(result, candles, 14)
    assert {c.status for c in e.context_components} == {ScoreComponentStatus.NOT_EVALUATED}
    assert e.context_score == 0.0
    _, evaluated = run([*BASE, (10, 11, 10, 11)], EMPTY)
    statuses = {c.factor: c.status for c in evaluated.events[0].context_components}
    assert all(
        s is ScoreComponentStatus.EVALUATED for f, s in statuses.items() if f not in (F.SESSION, F.NEWS)
    )
    assert len(statuses) == len(NoWickContextFactor)


def _structure(index, candles, type_=StructureEventType.BOS, direction=Direction.BULLISH):
    return SimpleNamespace(
        time=candles[index].open_time,
        status=StructureEventStatus.CONFIRMED,
        level=StructureLevel.EXTERNAL,
        type=type_,
        direction=direction,
    )


def test_context_components_score_only_facts_at_or_before_the_candle():
    rows = [*BASE, (10, 11, 10, 11), (11, 11.5, 10.8, 11.4)]
    candles = ohlc_candles(rows)
    t = lambda i: candles[i].open_time  # noqa: E731
    inputs = NoWickInputs(
        structure_events=[
            _structure(5, candles),  # trend BULLISH before the candle
            _structure(14, candles, StructureEventType.MSS),  # break on the candle
            _structure(15, candles, StructureEventType.BOS, Direction.BEARISH),  # later: must not matter
        ],
        liquidity_events=[
            SimpleNamespace(time=t(12), side=LiquiditySide.SSL, type=LiquidityEventType.SWEEP),
            SimpleNamespace(time=t(14), side=LiquiditySide.BSL, type=LiquidityEventType.SWEEP),
        ],
        displacements=[
            SimpleNamespace(time=t(14), direction=Direction.BULLISH, grade=DisplacementGrade.STRONG)
        ],
        pd_events=[
            # FVG completed by the NEXT candle: must never add points to candle 14 (no lookahead).
            SimpleNamespace(
                time=t(15),
                type=PdArrayEventType.CREATED,
                zone_type=PdArrayType.FVG,
                direction=Direction.BULLISH,
            )
        ],
        pd_zones=[
            SimpleNamespace(id="known", created_at=t(13), invalidated_at=None, bottom=10.2, top=10.4),
            SimpleNamespace(id="later", created_at=t(15), invalidated_at=None, bottom=10.2, top=10.4),
            SimpleNamespace(id="dead", created_at=t(13), invalidated_at=t(14), bottom=10.2, top=10.4),
        ],
    )
    result = analyze_no_wick(candles, inputs, CFG)
    e = event_at(result, candles, 14)
    points = {c.factor: c.points for c in e.context_components}
    assert points[F.TREND] == CFG.c_trend
    assert points[F.STRUCTURE] == CFG.c_structure["MSS"]
    assert points[F.LIQUIDITY] == CFG.c_liquidity
    assert points[F.DISPLACEMENT] == CFG.c_displacement["STRONG"]
    assert points[F.FVG] == 0.0
    assert e.context_score == min(100.0, sum(points.values()))
    zone = next(z for z in result.zones if z.id == e.zone_id)
    assert zone.fvg_overlap_ids == ["known"] and zone.ob_overlap is ScoreComponentStatus.NOT_EVALUATED

    same_candle_fvg = NoWickInputs(
        [],
        [],
        [],
        [
            SimpleNamespace(
                time=t(14),
                type=PdArrayEventType.CREATED,
                zone_type=PdArrayType.FVG,
                direction=Direction.BULLISH,
            )
        ],
        [],
    )
    e2 = event_at(analyze_no_wick(candles, same_candle_fvg, CFG), candles, 14)
    assert {c.factor: c.points for c in e2.context_components}[F.FVG] == CFG.c_fvg


def test_liquidity_outside_lookback_or_wrong_side_scores_zero():
    candles = ohlc_candles([*BASE, (10, 11, 10, 11)])
    far = SimpleNamespace(time=candles[10].open_time, side=LiquiditySide.SSL, type=LiquidityEventType.SWEEP)
    touch = SimpleNamespace(time=candles[14].open_time, side=LiquiditySide.SSL, type=LiquidityEventType.TOUCH)
    result = analyze_no_wick(candles, NoWickInputs([], [far, touch], [], [], []), CFG)
    assert {c.factor: c.points for c in result.events[0].context_components}[F.LIQUIDITY] == 0.0


FULL_REACT = [
    (12.4, 12.6, 11.7, 12.2),  # 25%
    (12.2, 12.3, 11.4, 12.1),  # 50%
    (12.1, 12.2, 11.1, 12.0),  # 75%
    (12.0, 12.1, 10.8, 11.9),  # 100%
    (11.9, 12.7, 11.8, 12.6),  # close >= close_level + 0.5 ATR within the window -> REACTED
]
FAIL_THEN_INVALIDATE = [
    (12.4, 12.6, 11.7, 12.2),
    (12.0, 12.1, 10.5, 10.6),  # closes below the open: FAILED (not terminal)
    (10.6, 10.7, 9.8, 9.9),  # closes below the origin extreme: INVALIDATED
]
DIRECT_INVALIDATION = [(12.0, 12.0, 9.5, 9.6)]


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize(
    ("after", "sequence", "final"),
    [
        (
            FULL_REACT,
            [E.TOUCHED, E.REBALANCE_25, E.REBALANCE_50, E.REBALANCE_75, E.FULLY_REBALANCED, E.REACTED],
            S.REACTED,
        ),
        (
            FAIL_THEN_INVALIDATE,
            [
                E.TOUCHED,
                E.REBALANCE_25,
                E.REBALANCE_50,
                E.REBALANCE_75,
                E.FULLY_REBALANCED,
                E.FAILED,
                E.INVALIDATED,
            ],
            S.INVALIDATED,
        ),
        (
            DIRECT_INVALIDATION,
            [E.TOUCHED, E.REBALANCE_25, E.REBALANCE_50, E.REBALANCE_75, E.FULLY_REBALANCED, E.INVALIDATED],
            S.INVALIDATED,
        ),
        (FULL_REACT[:2], [E.TOUCHED, E.REBALANCE_25, E.REBALANCE_50], S.HALF_REBALANCED),
        (
            FAIL_THEN_INVALIDATE[:2],
            [E.TOUCHED, E.REBALANCE_25, E.REBALANCE_50, E.REBALANCE_75, E.FULLY_REBALANCED, E.FAILED],
            S.FAILED,
        ),
    ],
)
def test_zone_lifecycle(after, sequence, final, flip):
    rows = [*BASE, SOURCE, *after]
    candles, result = run(mirror(rows) if flip else rows)
    source = event_at(result, candles, 14)
    zone = next(z for z in result.zones if z.id == source.zone_id)
    assert zone_sequence(result, zone.id) == sequence
    assert zone.state is final and zone.active is (final in (S.TOUCHED, S.PARTIAL, S.HALF_REBALANCED))
    assert zone.close_level == pytest.approx(8 if flip else 12)
    assert zone.level_25 == pytest.approx(20 - 11.7 if flip else 11.7)
    assert zone.level_50 == pytest.approx(20 - 11.4 if flip else 11.4)
    assert zone.level_75 == pytest.approx(20 - 11.1 if flip else 11.1)
    assert zone.known_at == candles[14].close_time and zone.age_bars == len(after)


def test_reaction_outside_the_window_is_ignored():
    quiet = [(12.2, 12.4, 12.1, 12.3)] * 6
    rows = [*BASE, SOURCE, (12.4, 12.6, 11.7, 12.2), *quiet, (12.3, 12.9, 12.2, 12.8)]
    candles, result = run(rows)
    zone_id = event_at(result, candles, 14).zone_id
    assert E.REACTED not in zone_sequence(result, zone_id)
    assert next(z for z in result.zones if z.id == zone_id).state is S.PARTIAL


@pytest.mark.parametrize(
    ("last", "state"), [((12.5, 12.8, 12.3, 12.4), S.APPROACHING), ((13.0, 13.6, 12.9, 13.5), S.FRESH)]
)
def test_approaching_is_derived_for_the_as_of_view(last, state):
    candles, result = run([*BASE, SOURCE, last])
    zone = next(z for z in result.zones if z.id == event_at(result, candles, 14).zone_id)
    assert zone.state is state and zone.active and zone_sequence(result, zone.id) == []


# --- Forming candle (Step 5: LATE_CANDLE_FADE groundwork) -----------------------------------------
from datetime import UTC, datetime, timedelta  # noqa: E402

from app.domain.candle import Candle  # noqa: E402
from app.domain.enums import DataQuality, NoWickVariant, Timeframe  # noqa: E402
from app.services.no_wick.features import build_forming_candle  # noqa: E402


def _bar(open_time, o, h, low, c, *, tf=Timeframe.M15, closed=True):
    return Candle(
        symbol="XAUUSD",
        timeframe=tf,
        open_time=open_time,
        close_time=open_time + tf.duration,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
        is_closed=closed,
        data_quality=DataQuality.CURRENT,
    )


def test_forming_candle_geometry_and_maturity():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    live_open = t0 + timedelta(minutes=15)
    live = _bar(live_open, 10.5, 12.0, 10.0, 11.7, closed=False)  # range 2.0, body 1.2, upper 0.3, lower 0.5
    now = live_open + timedelta(minutes=9)  # 9/15 = 60% through the M15 bucket
    fc = build_forming_candle(Timeframe.M15, [_bar(t0, 10, 11, 9, 10.5), live], now, CFG)
    assert fc is not None
    assert fc.maturity_pct == pytest.approx(60.0)
    assert fc.direction is Direction.BULLISH
    assert (fc.open, fc.high, fc.low, fc.close) == (10.5, 12.0, 10.0, 11.7)
    assert fc.range == pytest.approx(2.0) and fc.body == pytest.approx(1.2)
    assert fc.upper_wick == pytest.approx(0.3) and fc.lower_wick == pytest.approx(0.5)
    assert fc.body_pct == pytest.approx(0.6)
    assert fc.close_location_pct == pytest.approx((11.7 - 10.0) / 2.0 * 100)


def test_forming_candle_none_when_last_closed_or_empty():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    assert (
        build_forming_candle(Timeframe.M15, [_bar(t0, 10, 11, 9, 10.5)], t0 + timedelta(hours=1), CFG) is None
    )
    assert build_forming_candle(Timeframe.M15, [], t0, CFG) is None


def test_forming_candle_flat_bar_has_no_direction_and_null_ratios():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    fc = build_forming_candle(
        Timeframe.M15, [_bar(t0, 10.0, 10.0, 10.0, 10.0, closed=False)], t0 + timedelta(minutes=3), CFG
    )
    assert fc is not None
    assert fc.direction is None and fc.body_pct is None and fc.close_location_pct is None
    assert fc.maturity_pct == pytest.approx(20.0)


def test_forming_candle_maturity_clamped_to_100_when_overdue():
    # A late/overdue clock (bar should have closed) never reports past 100%.
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    fc = build_forming_candle(
        Timeframe.M15, [_bar(t0, 10, 11, 9, 10.5, closed=False)], t0 + timedelta(hours=2), CFG
    )
    assert fc is not None and fc.maturity_pct == 100.0


# --- No-wick variants (Phase C, spec section 1) ---------------------------------------------------


def test_late_candle_fade_bullish_no_lower_wick_fades_down():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    # 80% mature bullish bar, no lower wick (missing) -> fade toward the missing lower wick = BEARISH.
    bar = _bar(t0, 10.0, 11.9, 10.0, 11.8, closed=False)  # range 1.9, body 1.8, lower 0.0, upper 0.1
    fc = build_forming_candle(Timeframe.M15, [bar], t0 + timedelta(minutes=12), CFG)
    assert fc.variant is NoWickVariant.LATE_CANDLE_FADE
    assert fc.signal_direction is Direction.BEARISH
    assert fc.origin_weight == pytest.approx(0.2)  # M15 weight from spec


def test_late_candle_fade_bearish_no_upper_wick_fades_up():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    # 80% mature bearish bar, no upper wick -> fade up toward the missing upper wick = BULLISH.
    bar = _bar(t0, 11.9, 11.9, 10.0, 10.1, closed=False)  # range 1.9, body 1.8, upper 0.0, lower 0.1
    fc = build_forming_candle(Timeframe.D1, [bar], t0 + timedelta(hours=20), CFG)
    assert fc.variant is NoWickVariant.LATE_CANDLE_FADE and fc.signal_direction is Direction.BULLISH
    assert fc.origin_weight == pytest.approx(1.0)  # D1 weighted highest


def test_early_candle_continuation_keeps_body_direction():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    # 20% mature bullish bar with a trailing (lower) wick already formed -> continuation, not invalidation.
    bar = _bar(t0, 10.3, 11.0, 10.0, 10.9, closed=False)  # range 1.0, body 0.6, lower 0.3, upper 0.1
    fc = build_forming_candle(Timeframe.M15, [bar], t0 + timedelta(minutes=3), CFG)
    assert fc.variant is NoWickVariant.EARLY_CANDLE_CONTINUATION
    assert fc.signal_direction is Direction.BULLISH


def test_no_variant_mid_life():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    bar = _bar(t0, 10.0, 11.9, 10.0, 11.8, closed=False)
    fc = build_forming_candle(Timeframe.M15, [bar], t0 + timedelta(minutes=8), CFG)  # ~53% mature
    assert fc.variant is None and fc.signal_direction is None


def test_late_marubozu_fades_against_the_body():
    t0 = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
    # 80% mature bullish marubozu (no wick either side) -> fade back toward the origin = BEARISH.
    bar = _bar(t0, 10.0, 11.9, 10.0, 11.9, closed=False)  # lower 0, upper 0, body 1.9
    fc = build_forming_candle(Timeframe.M15, [bar], t0 + timedelta(minutes=12), CFG)
    assert fc.variant is NoWickVariant.LATE_CANDLE_FADE and fc.signal_direction is Direction.BEARISH
