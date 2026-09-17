"""Hand-built displacement / FVG / IFVG scenarios with exact expected results."""

import pytest

from app.domain.enums import (
    Direction,
    DisplacementGrade,
    IfvgStatus,
    PdArrayState,
    PdArrayType,
    QualifierStatus,
    StructureLevel,
    TrendDirection,
)
from app.services.pd_arrays.displacement import detect_displacements
from app.services.pd_arrays.fvg import detect_fvgs
from app.services.pd_arrays.models import DisplacementEvent
from app.services.pd_arrays.qualifiers import qualify_displacement
from app.services.structure.engine import analyze_level
from tests.pd_helpers import BASE, mirror, ohlc_candles, pd_cfg
from tests.structure_helpers import UPTREND_THEN_MSS, cfg, hlc_candles

G = DisplacementGrade


def grades(rows, **cfg_overrides):
    candles = ohlc_candles(rows)
    idx = {c.open_time: i for i, c in enumerate(candles)}
    return [
        (idx[d.time], d.direction, d.grade) for d in detect_displacements(candles, pd_cfg(**cfg_overrides))
    ]


# --- displacement --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candle", "expected"),
    [
        ((10.0, 10.95, 9.95, 10.9), []),  # 0.9 ATR: below WEAK
        ((10.0, 11.1, 9.95, 11.05), [G.WEAK]),
        ((10.0, 11.6, 9.95, 11.55), [G.MODERATE]),
        ((10.0, 12.1, 9.95, 12.05), [G.STRONG]),
        ((10.0, 13.1, 9.95, 13.05), [G.EXCEPTIONAL]),
    ],
)
def test_single_candle_grades_use_atr_before_the_leg(candle, expected):
    result = grades([*BASE, candle])
    assert [g for _, _, g in result] == expected
    assert all(i == 14 and d is Direction.BULLISH for i, d, _ in result)


def test_strong_move_with_poor_body_quality_is_capped_at_moderate():
    # body 2.05 of range 3.75 (55%): qualifies (>= 50%) but below the 65% needed for STRONG+
    assert grades([*BASE, (10.0, 13.7, 9.95, 12.05)]) == [(14, Direction.BULLISH, G.MODERATE)]


def test_multi_candle_leg_emits_on_first_grade_and_upgrades_only():
    rows = [
        *BASE,
        (10.0, 10.85, 9.95, 10.8),
        (10.8, 11.65, 10.75, 11.6),
        (11.6, 12.45, 11.55, 12.4),
        (12.4, 13.25, 12.35, 13.2),
    ]
    assert grades(rows) == [(15, Direction.BULLISH, G.MODERATE), (16, Direction.BULLISH, G.STRONG)]


def test_small_body_candle_breaks_the_leg_and_opposite_direction_is_separate():
    rows = [
        *BASE,
        (10.0, 10.85, 9.95, 10.8),
        (10.8, 11.8, 10.2, 10.9),  # doji-ish: breaks the run
        (10.9, 11.75, 10.85, 11.7),
    ]
    assert grades(rows) == []  # neither leg reaches 1 ATR on its own
    assert grades(mirror([*BASE, (10.0, 12.1, 9.95, 12.05)])) == [(14, Direction.BEARISH, G.STRONG)]


def test_no_grade_without_prior_candles():
    assert grades([(10.0, 13.1, 9.95, 13.05)]) == []


# --- FVG -----------------------------------------------------------------------------------------

# a: high 10.5 | b: displacement | c: low 11.0  ->  bullish FVG [10.5, 11.0], size 0.5 ATR
FVG_ROWS = [*BASE, (10.0, 10.5, 9.5, 10.4), (10.4, 12.2, 10.3, 12.1), (12.1, 12.6, 11.0, 12.5)]


def zones(rows, trend=TrendDirection.NONE, **overrides):
    candles = ohlc_candles(rows)
    c = pd_cfg(**overrides)
    result = detect_fvgs(candles, detect_displacements(candles, c), trend, c)
    return candles, result


def only(result, zone_type=PdArrayType.FVG):
    found = [z for z in result.zones if z.type is zone_type]
    assert len(found) == 1, found
    return found[0]


def kinds(candles, result, zone_id):
    idx = {c.open_time: i for i, c in enumerate(candles)}
    return [(idx[e.time], e.type.value) for e in result.events if e.zone_id == zone_id]


def test_bullish_fvg_detection_levels_and_creating_displacement():
    candles, result = zones(FVG_ROWS)
    z = only(result)
    assert (z.direction, z.bottom, z.top, z.midpoint) == (Direction.BULLISH, 10.5, 11.0, 10.75)
    assert z.source_times == [candles[14].open_time, candles[15].open_time, candles[16].open_time]
    assert z.known_at == candles[16].close_time and z.state is PdArrayState.FRESH and z.active
    assert z.displacement_grade is G.MODERATE  # candle 15: 1.7 ATR
    assert kinds(candles, result, z.id) == [(16, "CREATED")]
    assert z.quality_score is not None and 0 < z.quality_score <= 100


def test_bearish_fvg_mirror():
    _, result = zones(mirror(FVG_ROWS))
    z = only(result)
    assert (z.direction, z.bottom, z.top) == (Direction.BEARISH, 9.0, 9.5)


def test_tiny_gap_is_rejected():
    rows = [*BASE, (10.0, 10.5, 9.5, 10.4), (10.4, 12.2, 10.3, 12.1), (12.1, 12.6, 10.52, 12.5)]  # gap 0.02
    assert zones(rows)[1].zones == []


@pytest.mark.parametrize(
    ("mitigation", "state", "fill", "events"),
    [
        ([(12.5, 12.6, 11.01, 12.4)], PdArrayState.TOUCHED, 0.0, ["TOUCHED"]),
        ([(12.5, 12.6, 10.9, 12.4)], PdArrayState.PARTIAL, 20.0, ["PARTIAL_FILL"]),
        (
            [(12.5, 12.6, 10.9, 12.4), (12.4, 12.5, 10.75, 12.3)],
            PdArrayState.HALF,
            50.0,
            ["PARTIAL_FILL", "HALF_FILL"],
        ),
        ([(12.5, 12.6, 10.45, 11.2)], PdArrayState.FULL, 100.0, ["FULL_FILL"]),  # wick through, close inside
        ([(12.5, 12.6, 11.5, 12.4)], PdArrayState.FRESH, 0.0, []),
    ],
)
def test_mitigation_states_by_wick_penetration(mitigation, state, fill, events):
    candles, result = zones([*FVG_ROWS, *mitigation])
    z = only(result)
    assert z.state is state and z.fill_pct == fill
    assert [k for _, k in kinds(candles, result, z.id)] == ["CREATED", *events]
    assert z.active is (state is not PdArrayState.FULL)
    assert (z.quality_score is None) is (not z.active)
    assert not [x for x in result.zones if x.type is PdArrayType.IFVG]  # a wick never inverts


def test_close_through_invalidates_and_spawns_only_a_potential_ifvg():
    # candle 18 closes back inside the zone and keeps high >= 11.0 so it forms no new FVG
    # weak close-through (0.75 body, no displacement) so nothing confirms the inversion yet
    candles, result = zones([*FVG_ROWS, (11.2, 11.3, 10.35, 10.45), (10.45, 11.05, 10.4, 10.6)])
    fvg = only(result)
    assert (
        fvg.state is PdArrayState.INVALIDATED
        and fvg.invalidated_at == candles[17].open_time
        and not fvg.active
    )
    ifvg = only(result, PdArrayType.IFVG)
    assert ifvg.direction is Direction.BEARISH and ifvg.parent_id == fvg.id
    assert ifvg.ifvg_status is IfvgStatus.POTENTIAL_IFVG and not ifvg.active


def test_ifvg_confirmed_by_inversion_displacement():
    candles, result = zones([*FVG_ROWS, (12.5, 12.55, 10.1, 10.15)])  # 2.35 body, ~2.1 ATR bearish
    ifvg = only(result, PdArrayType.IFVG)
    assert ifvg.ifvg_status is IfvgStatus.CONFIRMED_IFVG and ifvg.active
    assert ifvg.displacement_grade is G.STRONG
    assert [k for _, k in kinds(candles, result, ifvg.id)] == ["IFVG_POTENTIAL", "IFVG_CONFIRMED"]
    assert next(e for e in result.events if e.type.value == "IFVG_CONFIRMED").detail == "displacement"


def test_ifvg_confirmed_by_acceptance_closes():
    rows = [*FVG_ROWS, (11.2, 11.3, 10.35, 10.45), (10.45, 10.5, 10.0, 10.1)]
    candles, result = zones(rows)
    ifvg = only(result, PdArrayType.IFVG)
    assert kinds(candles, result, ifvg.id) == [(17, "IFVG_POTENTIAL"), (18, "IFVG_CONFIRMED")]
    assert next(e for e in result.events if e.type.value == "IFVG_CONFIRMED").detail == "acceptance"
    assert ifvg.displacement_grade is None


def test_ifvg_fails_when_reclaimed():
    candles, result = zones([*FVG_ROWS, (11.2, 11.3, 10.35, 10.45), (10.45, 11.4, 10.4, 11.2)])
    ifvg = only(result, PdArrayType.IFVG)
    assert ifvg.ifvg_status is IfvgStatus.FAILED_IFVG and not ifvg.active
    assert kinds(candles, result, ifvg.id)[-1] == (18, "IFVG_FAILED")
    assert result.events[-1].detail == "reclaimed"


def test_ifvg_fails_when_window_expires():
    inside = [(10.6, 10.8, 10.55, 10.6)] * 6  # closes stay inside the zone: no acceptance, no reclaim
    candles, result = zones([*FVG_ROWS, (11.2, 11.3, 10.35, 10.45), *inside], ifvg_confirm_window_bars=5)
    ifvg = only(result, PdArrayType.IFVG)
    assert kinds(candles, result, ifvg.id) == [(17, "IFVG_POTENTIAL"), (23, "IFVG_FAILED")]
    assert result.events[-1].detail == "expired"


def test_confirmed_ifvg_is_mitigated_from_its_own_side_and_can_be_invalidated():
    rows = [*FVG_ROWS, (12.5, 12.55, 10.1, 10.15), (10.15, 10.75, 10.05, 10.3), (10.3, 11.3, 10.2, 11.2)]
    candles, result = zones(rows)
    original = next(z for z in result.zones if z.type is PdArrayType.FVG and z.direction is Direction.BULLISH)
    ifvg = next(z for z in result.zones if z.parent_id == original.id)
    assert kinds(candles, result, ifvg.id) == [
        (17, "IFVG_POTENTIAL"),
        (17, "IFVG_CONFIRMED"),
        (18, "HALF_FILL"),
        (19, "INVALIDATED"),
    ]
    assert ifvg.state is PdArrayState.INVALIDATED and ifvg.ifvg_status is IfvgStatus.CONFIRMED_IFVG
    # Candle 18 (high 10.75 < candle 16 low 11.0) forms a new bearish FVG; candle 19 inverts it.
    # Every IFVG must come from an FVG: an IFVG never inverts again.
    assert all(
        z.parent_id and z.parent_id.startswith("FVG:") for z in result.zones if z.type is PdArrayType.IFVG
    )
    assert not [z for z in result.zones if z.parent_id == ifvg.id]


def test_trend_alignment_only_changes_quality():
    _, aligned = zones(FVG_ROWS, trend=TrendDirection.BULLISH)
    _, against = zones(FVG_ROWS, trend=TrendDirection.BEARISH)
    assert aligned.events == against.events
    assert only(aligned).quality_score == only(against).quality_score + 10


# --- displacement qualifier ------------------------------------------------------------------------


def disp(candles, index, direction, grade=G.STRONG):
    t = candles[index].open_time
    return DisplacementEvent(
        id=f"d{index}",
        direction=direction,
        grade=grade,
        magnitude_atr=2.0,
        leg_start=t,
        time=t,
        candle_count=1,
        avg_body_pct=0.9,
    )


def test_displacement_qualifier_present_absent_grade_and_no_lookahead():
    candles = hlc_candles(UPTREND_THEN_MSS)  # bearish CHoCH @13, bearish MSS @14, bullish BOS @5, @9
    level = analyze_level(candles, StructureLevel.EXTERNAL, cfg())
    times = [c.open_time for c in candles]
    idx = {c.open_time: i for i, c in enumerate(candles)}

    def status(events, lookback=3, min_grade=G.MODERATE):
        q = qualify_displacement(level, events, times, lookback, min_grade)
        return {
            (idx[e.time], e.type.value): e.displacement_qualifier
            for e in q.events
            if e.status.value == "CONFIRMED"
        }

    s = status([disp(candles, 14, Direction.BEARISH)])
    assert s[(14, "MSS")] is QualifierStatus.PRESENT  # displacement on the break candle
    assert s[(13, "CHOCH")] is QualifierStatus.ABSENT  # happened after the CHoCH -> never counts
    assert s[(5, "BOS")] is QualifierStatus.ABSENT  # wrong direction
    assert status([disp(candles, 14, Direction.BEARISH, G.WEAK)])[(14, "MSS")] is QualifierStatus.ABSENT
    assert (
        status([disp(candles, 10, Direction.BEARISH)])[(14, "MSS")] is QualifierStatus.ABSENT
    )  # 4 bars earlier
    assert (
        status([disp(candles, 11, Direction.BEARISH)])[(14, "MSS")] is QualifierStatus.PRESENT
    )  # 3 bars earlier
    assert qualify_displacement(None, [], times, 3, G.MODERATE) is None
