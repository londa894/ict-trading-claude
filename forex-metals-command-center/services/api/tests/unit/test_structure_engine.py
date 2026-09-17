from app.domain.enums import (
    BreakConfirmation,
    Direction,
    QualifierStatus,
    StructureEventStatus,
    StructureLevel,
    StructureState,
    SwingKind,
    SwingLabel,
    TrendDirection,
)
from app.domain.enums import StructureEventType as T
from app.services.structure.engine import analyze_level
from app.services.structure.swings import detect_swings
from tests.structure_helpers import UPTREND_THEN_MSS, cfg, hlc_candles

EXT = StructureLevel.EXTERNAL


def confirmed(level):
    return [e for e in level.events if e.status is StructureEventStatus.CONFIRMED]


def summary(events, candles):
    idx = {c.open_time: i for i, c in enumerate(candles)}
    return [(idx[e.time], e.type, e.direction, e.price) for e in events]


# --- swings ---------------------------------------------------------------------------------------


def test_swings_are_confirmed_only_after_right_side_closes():
    candles = hlc_candles(UPTREND_THEN_MSS)
    swings = detect_swings(candles, pivot=1, equal_tolerance_atr=0.0, atr_period=14)
    highs = [(s.index, s.confirm_index, s.price, s.label) for s in swings if s.kind is SwingKind.HIGH]
    lows = [(s.index, s.confirm_index, s.price, s.label) for s in swings if s.kind is SwingKind.LOW]
    assert highs == [
        (1, 2, 11.0, SwingLabel.NONE),
        (5, 6, 11.5, SwingLabel.HH),
        (9, 10, 12.0, SwingLabel.HH),
        (12, 13, 11.5, SwingLabel.LH),
    ]
    assert lows == [
        (3, 4, 8.8, SwingLabel.NONE),
        (7, 8, 10.0, SwingLabel.HL),
        (11, 12, 10.6, SwingLabel.HL),
    ]


def test_last_candle_can_never_be_a_confirmed_pivot():
    candles = hlc_candles([(1, 0.5, 0.8), (2, 1, 1.5), (3, 2, 2.8)])  # rising: last candle has no right side
    assert detect_swings(candles, 1, 0.0, 14) == []


def test_equal_highs_first_one_wins_and_eh_label_uses_tolerance():
    # pivot 2, highs 10, 11, 12, 11, 12, 11, 10: index2=12 is a pivot (right max 12 is not above it);
    # index4=12 is not (left max 12, so not strictly above) -> the first of equal highs wins.
    rows = [
        (10, 9, 9.5),
        (11, 9.5, 10),
        (12, 10, 11),
        (11, 10, 10.5),
        (12, 10, 11),
        (11, 10, 10.5),
        (10, 9, 9.5),
    ]
    highs = [s for s in detect_swings(hlc_candles(rows), 2, 0.0, 14) if s.kind is SwingKind.HIGH]
    assert [s.index for s in highs] == [2]

    rows = [(10, 9, 9.5), (12, 10, 11), (11, 10, 10.5), (12.05, 10, 11), (11, 10, 10.5)]
    highs = [s for s in detect_swings(hlc_candles(rows), 1, 0.5, 14) if s.kind is SwingKind.HIGH]
    assert [(s.index, s.label) for s in highs] == [(1, SwingLabel.NONE), (3, SwingLabel.EH)]


# --- breaks ---------------------------------------------------------------------------------------


def test_uptrend_bos_then_choch_then_mss():
    candles = hlc_candles(UPTREND_THEN_MSS)
    lvl = analyze_level(candles, EXT, cfg())
    assert summary(confirmed(lvl), candles) == [
        (5, T.BOS, Direction.BULLISH, 11.0),
        (9, T.BOS, Direction.BULLISH, 11.5),
        (13, T.CHOCH, Direction.BEARISH, 10.6),
        (14, T.MSS, Direction.BEARISH, 10.0),
    ]
    ev = confirmed(lvl)
    assert ev[0].trend_before is TrendDirection.NONE
    assert ev[2].trend_before is TrendDirection.BULLISH
    assert all(e.confirmation is BreakConfirmation.CANDLE_CLOSE for e in ev)
    assert all(
        e.liquidity_qualifier is QualifierStatus.NOT_EVALUATED
        and e.displacement_qualifier is QualifierStatus.NOT_EVALUATED
        for e in lvl.events
    )
    assert lvl.trend is TrendDirection.BEARISH
    assert lvl.state is StructureState.BEARISH  # labels formed before the MSS don't count as ranging
    assert lvl.protected_low is None
    assert lvl.protected_high is not None and lvl.protected_high.price == 11.5


def test_state_is_transitioning_between_choch_and_resolution():
    candles = hlc_candles(UPTREND_THEN_MSS[:14])
    lvl = analyze_level(candles, EXT, cfg())
    assert lvl.state is StructureState.TRANSITIONING
    assert lvl.trend is TrendDirection.BULLISH
    assert lvl.protected_low is not None and lvl.protected_low.price == 10.0


def test_choch_that_fails_resumes_with_bos_and_keeps_protected_low():
    rows = [*UPTREND_THEN_MSS[:14], (11.8, 10.5, 11.7)]  # close 11.7 > latest swing high 11.5
    candles = hlc_candles(rows)
    lvl = analyze_level(candles, EXT, cfg())
    last = confirmed(lvl)[-1]
    assert (last.type, last.direction, last.price) == (T.BOS, Direction.BULLISH, 11.5)
    assert lvl.state is StructureState.BULLISH
    assert (
        lvl.protected_low is not None and lvl.protected_low.price == 10.0
    )  # fallback: latest low was broken


def test_direct_mss_when_latest_low_is_the_protected_low():
    rows = [*UPTREND_THEN_MSS[:10], (11.8, 11.0, 11.2), (11.3, 9.5, 9.8)]  # close 9.8 < protected 10.0
    candles = hlc_candles(rows)
    lvl = analyze_level(candles, EXT, cfg())
    last = confirmed(lvl)[-1]
    assert (last.type, last.direction, last.price) == (T.MSS, Direction.BEARISH, 10.0)
    assert lvl.trend is TrendDirection.BEARISH


def test_wick_only_break_is_potential_once_and_never_changes_trend():
    rows = [
        (10.0, 9.0, 9.5),
        (11.0, 9.5, 10.8),
        (10.5, 9.2, 9.4),  # swing high 11.0 confirmed
        (11.2, 9.3, 10.9),  # wick above 11.0, close below -> POTENTIAL
        (11.3, 9.4, 10.8),  # wicks again (and is not itself a pivot) -> no duplicate potential
        (11.6, 10.4, 11.2),  # close above -> CONFIRMED BOS
    ]
    candles = hlc_candles(rows)
    lvl = analyze_level(candles, EXT, cfg())
    pot = [e for e in lvl.events if e.status is StructureEventStatus.POTENTIAL]
    assert summary(pot, candles) == [(3, T.BOS, Direction.BULLISH, 11.0)]
    assert pot[0].confirmation is BreakConfirmation.WICK_ONLY
    assert summary(confirmed(lvl), candles) == [(5, T.BOS, Direction.BULLISH, 11.0)]

    before_confirmation = analyze_level(hlc_candles(rows[:5]), EXT, cfg())
    assert before_confirmation.trend is TrendDirection.NONE
    assert before_confirmation.state is StructureState.UNCLEAR


def test_potential_mss_on_protected_low_wick():
    rows = [
        *UPTREND_THEN_MSS[:13],
        (11.3, 9.9, 10.7),
    ]  # wick below 10.6 target and 10.0 protected, close above
    candles = hlc_candles(rows)
    lvl = analyze_level(candles, EXT, cfg())
    pot = {
        (e.type, e.price)
        for e in lvl.events
        if e.status is StructureEventStatus.POTENTIAL and e.direction is Direction.BEARISH
    }
    assert pot == {(T.MSS, 10.0), (T.CHOCH, 10.6)}
    # Wicks never change the trend. (State is RANGING: LH 11.5 + HL 10.6 formed after the last BOS.)
    assert lvl.trend is TrendDirection.BULLISH
    assert lvl.state is StructureState.RANGING
    assert not [e for e in confirmed(lvl) if e.direction is Direction.BEARISH]


def test_each_swing_broken_at_most_once_and_links_are_consistent():
    candles = hlc_candles(UPTREND_THEN_MSS)
    lvl = analyze_level(candles, EXT, cfg())
    ids = {e.id for e in lvl.events}
    assert len(ids) == len(lvl.events)
    for s in lvl.swings:
        if s.broken_by is not None:
            assert s.broken_by in ids
            ev = next(e for e in lvl.events if e.id == s.broken_by)
            assert ev.status is StructureEventStatus.CONFIRMED and ev.time == s.broken_at
    for e in confirmed(lvl):
        swing = next(s for s in lvl.swings if s.id == e.broken_swing_id)
        assert swing.confirmed_at <= e.time  # the swing was known before the breaking candle opened


def test_unclear_when_insufficient_candles_or_no_trend():
    candles = hlc_candles(UPTREND_THEN_MSS)
    assert analyze_level(candles, EXT, cfg(min_candles=50)).state is StructureState.UNCLEAR
    assert analyze_level(hlc_candles(UPTREND_THEN_MSS[:5]), EXT, cfg()).state is StructureState.UNCLEAR


def test_ranging_after_too_many_bars_without_break():
    candles = hlc_candles(UPTREND_THEN_MSS[:10] + [(11.8, 11.0, 11.4)] * 1 + [(11.7, 11.1, 11.4)] * 6)
    lvl = analyze_level(candles, EXT, cfg(ranging_bars=5))
    assert lvl.bars_since_last_break == 7
    assert lvl.state is StructureState.RANGING


def test_ranging_on_compression_labels_formed_after_last_break():
    rows = [
        *UPTREND_THEN_MSS[:10],  # BOS at 9, swing high 12.0 will confirm at 10
        (11.8, 11.0, 11.2),
        (11.4, 10.6, 10.8),  # swing low 10.6 (HL)
        (11.7, 10.9, 11.5),  # swing high 11.7 (LH) — below 12.0
        (11.5, 10.8, 11.0),
        (11.3, 10.7, 11.2),  # swing low 10.7 (HL) confirmed after break, no close beyond 11.7/10.7 yet
        (11.4, 10.9, 11.1),
    ]
    lvl = analyze_level(hlc_candles(rows), EXT, cfg())
    assert lvl.state is StructureState.RANGING


def test_deterministic_output():
    candles = hlc_candles(UPTREND_THEN_MSS)
    assert analyze_level(candles, EXT, cfg()) == analyze_level(candles, EXT, cfg())


def test_bearish_mirror_scenario():
    mirrored = [(20 - low, 20 - h, 20 - c) for h, low, c in UPTREND_THEN_MSS]
    candles = hlc_candles(mirrored)
    lvl = analyze_level(candles, EXT, cfg())
    assert [(t, d) for _, t, d, _ in summary(confirmed(lvl), candles)] == [
        (T.BOS, Direction.BEARISH),
        (T.BOS, Direction.BEARISH),
        (T.CHOCH, Direction.BULLISH),
        (T.MSS, Direction.BULLISH),
    ]
    assert lvl.state is StructureState.BULLISH
