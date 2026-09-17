"""Property tests: no lookahead, lifecycle grammar and price invariants for displacement/FVG/IFVG."""

import re
from collections import Counter

import pytest

from app.domain.enums import Direction, PdArrayEventType, PdArrayType, TrendDirection
from app.services.pd_arrays.displacement import detect_displacements
from app.services.pd_arrays.fvg import detect_fvgs
from app.services.pd_arrays.models import PdArrayConfig
from tests.structure_helpers import random_walk_candles

SEEDS = [1, 2, 3, 7, 11]


def analyse(candles, cfg):
    displacements = detect_displacements(candles, cfg)
    return displacements, detect_fvgs(candles, displacements, TrendDirection.NONE, cfg)


@pytest.mark.parametrize("seed", SEEDS)
def test_no_lookahead_prefix_agrees_with_full_history(seed):
    cfg = PdArrayConfig.from_spec()
    candles = random_walk_candles(260, seed)
    full_disp, full = analyse(candles, cfg)
    for k in range(3, len(candles) + 1, 5):
        disp, prefix = analyse(candles[:k], cfg)
        last = candles[k - 1]
        assert disp == [d for d in full_disp if d.time <= last.open_time], k
        assert prefix.events == [e for e in full.events if e.time <= last.open_time], k
        assert {z.id for z in prefix.zones} == {z.id for z in full.zones if z.known_at <= last.close_time}, k


CODE = {
    PdArrayEventType.CREATED: "C",
    PdArrayEventType.TOUCHED: "T",
    PdArrayEventType.PARTIAL_FILL: "P",
    PdArrayEventType.HALF_FILL: "H",
    PdArrayEventType.FULL_FILL: "F",
    PdArrayEventType.INVALIDATED: "I",
    PdArrayEventType.IFVG_POTENTIAL: "O",
    PdArrayEventType.IFVG_CONFIRMED: "X",
    PdArrayEventType.IFVG_FAILED: "Z",
}
FVG_GRAMMAR = re.compile(r"^CT?P?H?F?I?$")
IFVG_GRAMMAR = re.compile(r"^O(XT?P?H?F?I?|Z)?$")


def _stats(seed):
    cfg = PdArrayConfig.from_spec()
    candles = random_walk_candles(500, seed)
    return cfg, candles, *analyse(candles, cfg)


@pytest.mark.parametrize("seed", SEEDS)
def test_lifecycle_grammar_and_price_invariants(seed):
    cfg, candles, displacements, result = _stats(seed)
    by_time = {c.open_time: c for c in candles}
    zones = {z.id: z for z in result.zones}
    sequences: dict[str, str] = {}
    for e in result.events:
        z, c = zones[e.zone_id], by_time[e.time]
        sequences[e.zone_id] = sequences.get(e.zone_id, "") + CODE[e.type]
        bullish = z.direction is Direction.BULLISH
        if e.type is PdArrayEventType.INVALIDATED:
            assert (c.close < z.bottom) if bullish else (c.close > z.top)
        if e.type is PdArrayEventType.FULL_FILL:
            assert (c.low <= z.bottom) if bullish else (c.high >= z.top)
        assert z.known_at <= e.time or e.type in (
            PdArrayEventType.CREATED,
            PdArrayEventType.IFVG_POTENTIAL,
            PdArrayEventType.IFVG_CONFIRMED,
            PdArrayEventType.INVALIDATED,
        )
    for zid, seq in sequences.items():
        grammar = FVG_GRAMMAR if zones[zid].type is PdArrayType.FVG else IFVG_GRAMMAR
        assert grammar.match(seq), (zid, seq)
    for z in result.zones:
        assert z.bottom < z.top and z.bottom <= z.midpoint <= z.top
        assert 0 <= z.fill_pct <= 100
        assert (z.quality_score is None) is (not z.active)
        if z.type is PdArrayType.FVG:
            a, _, c = (by_time[t] for t in z.source_times)
            assert (c.low > a.high) if z.direction is Direction.BULLISH else (c.high < a.low)
    for d in displacements:
        assert d.magnitude_atr >= cfg.grades_atr[d.grade] or d.grade.value == "MODERATE"
        assert d.avg_body_pct >= cfg.min_body_pct


def test_random_data_exercises_every_path():
    grades, events = Counter(), Counter()
    for seed in SEEDS:
        _, _, displacements, result = _stats(seed)
        grades.update(d.grade.value for d in displacements)
        events.update(e.type.value for e in result.events)
    assert {"WEAK", "MODERATE", "STRONG"} <= set(grades), grades
    assert set(CODE) - {PdArrayEventType(k) for k in events} <= set(), events
