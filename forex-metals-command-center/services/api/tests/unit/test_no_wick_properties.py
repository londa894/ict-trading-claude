"""Property tests: no lookahead (features, events, scores, components, zones) and zone lifecycle grammar."""

import re
from collections import Counter

import pytest

from app.domain.enums import (
    Direction,
    NoWickClassification,
    NoWickStrength,
    NoWickZoneEventType,
    TrendDirection,
)
from app.services.no_wick.engine import NoWickInputs, analyze_no_wick
from app.services.no_wick.models import NoWickConfig
from app.services.pd_arrays.displacement import detect_displacements
from app.services.pd_arrays.fvg import detect_fvgs
from app.services.pd_arrays.models import PdArrayConfig
from tests.structure_helpers import random_walk_candles

SEEDS = [1, 2, 3, 7, 11]
CFG = NoWickConfig.from_spec()
PD = PdArrayConfig.from_spec()


def analyse(candles):
    displacements = detect_displacements(candles, PD)
    fvgs = detect_fvgs(candles, displacements, TrendDirection.NONE, PD)
    inputs = NoWickInputs(None, None, displacements, fvgs.events, fvgs.zones)
    return analyze_no_wick(candles, inputs, CFG)


@pytest.mark.parametrize("seed", SEEDS)
def test_no_lookahead_prefix_agrees_with_full_history(seed):
    candles = random_walk_candles(260, seed)
    full = analyse(candles)
    for k in range(2, len(candles) + 1, 7):
        prefix = analyse(candles[:k])
        last = candles[k - 1]
        assert prefix.features == full.features[:k], k
        assert prefix.events == [e for e in full.events if e.time <= last.open_time], k
        assert prefix.zone_events == [e for e in full.zone_events if e.time <= last.open_time], k
        assert [z.id for z in prefix.zones] == [z.id for z in full.zones if z.known_at <= last.close_time], k


CODE = {
    NoWickZoneEventType.TOUCHED: "T",
    NoWickZoneEventType.REBALANCE_25: "a",
    NoWickZoneEventType.REBALANCE_50: "b",
    NoWickZoneEventType.REBALANCE_75: "c",
    NoWickZoneEventType.FULLY_REBALANCED: "U",
    NoWickZoneEventType.REACTED: "R",
    NoWickZoneEventType.FAILED: "F",
    NoWickZoneEventType.INVALIDATED: "I",
}
# Close beyond the open/origin implies the wick crossed the whole body, so FAILED/INVALIDATED follow U.
GRAMMAR = re.compile(r"^((T|Ta|Tab|Tabc)R?|TabcU(R|F|FI|I)?)?$")


@pytest.mark.parametrize("seed", SEEDS)
def test_zone_grammar_and_invariants(seed):
    candles = random_walk_candles(500, seed)
    result = analyse(candles)
    by_time = {c.open_time: c for c in candles}
    sequences: dict[str, str] = {z.id: "" for z in result.zones}
    zones = {z.id: z for z in result.zones}
    for e in result.zone_events:
        z, c = zones[e.zone_id], by_time[e.time]
        sequences[e.zone_id] += CODE[e.type]
        assert e.time > z.created_at
        bullish = z.direction is Direction.BULLISH
        if e.type is NoWickZoneEventType.INVALIDATED:
            assert (c.close < z.origin_extreme) if bullish else (c.close > z.origin_extreme)
    for zid, seq in sequences.items():
        assert GRAMMAR.match(seq), (zid, seq)
    for z in result.zones:
        lo, hi = sorted((z.close_level, z.open_level))
        assert (
            lo <= z.level_75 <= z.level_50 <= z.level_25 <= hi
            or lo <= z.level_25 <= z.level_50 <= z.level_75 <= hi
        )
        assert 0.0 <= z.rebalance_pct <= 100.0
    for e in result.events:
        assert NoWickClassification.NEWS_DRIVEN_NO_WICK not in (e.classification, *e.tags)
        assert 0.0 <= e.candle_quality_score <= 100.0 and 0.0 <= e.context_score <= 100.0
        assert 0.0 <= e.relevance_score <= 100.0
        assert (e.zone_id is not None) == (e.strength is not NoWickStrength.INSIGNIFICANT)


def test_random_walks_exercise_the_engine():
    shapes: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    for seed in SEEDS:
        result = analyse(random_walk_candles(500, seed))
        shapes.update(e.shape.value for e in result.events)
        shapes.update(e.strength.value for e in result.events)
        kinds.update(e.type.value for e in result.zone_events)
    assert shapes["MEANINGFUL"] and shapes["INSIGNIFICANT"]
    assert kinds["TOUCHED"] and kinds["FULLY_REBALANCED"] and kinds["INVALIDATED"]
