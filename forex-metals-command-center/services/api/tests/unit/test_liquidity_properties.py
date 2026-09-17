"""Property tests over random series: no lookahead, valid state transitions, event invariants."""

import re

import pytest

from app.domain.enums import (
    LiquidityEventType,
    LiquidityPoolType,
    LiquiditySide,
    StructureLevel,
    TrendDirection,
)
from app.services.liquidity.engine import analyze_liquidity
from app.services.liquidity.models import KeyLevel, LiquidityConfig
from app.services.structure.engine import analyze_level
from app.services.structure.models import StructureConfig
from tests.structure_helpers import random_walk_candles

SEEDS = [1, 2, 3, 7, 11]


def key_levels_for(candles):
    """Synthetic 'previous period' levels every 60 candles, known at the period's last close."""
    levels = []
    for start in range(0, len(candles) - 60, 60):
        period = candles[start : start + 60]
        for t, price in (
            (LiquidityPoolType.PDH, max(c.high for c in period)),
            (LiquidityPoolType.PDL, min(c.low for c in period)),
        ):
            levels.append(
                KeyLevel(
                    type=t,
                    price=price,
                    period_start=period[0].open_time,
                    known_at=period[-1].close_time,
                    label=f"{t.value} {start}",
                )
            )
    return levels


def analyse(candles, levels, s_cfg, l_cfg):
    internal = analyze_level(candles, StructureLevel.INTERNAL, s_cfg)
    external = analyze_level(candles, StructureLevel.EXTERNAL, s_cfg)
    return analyze_liquidity(candles, internal, external, levels, external.trend, l_cfg)


@pytest.mark.parametrize("seed", SEEDS)
def test_no_lookahead_prefix_agrees_with_full_history(seed):
    candles = random_walk_candles(240, seed)
    levels = key_levels_for(candles)
    s_cfg, l_cfg = StructureConfig.from_spec(), LiquidityConfig.from_spec()
    full = analyse(candles, levels, s_cfg, l_cfg)
    for k in range(10, len(candles) + 1, 7):
        prefix = analyse(candles[:k], levels, s_cfg, l_cfg)
        last = candles[k - 1]
        assert prefix.events == [e for e in full.events if e.time <= last.open_time], k
        expected_ids = {p.id for p in full.pools if p.known_at <= last.close_time}
        assert {p.id for p in prefix.pools} == expected_ids, k


# per pool: touches, then either a sweep (optionally failed) or a break (optionally run/reclaimed)
GRAMMAR = re.compile(r"^(T)*(S(F)?|B(R|C)?)?$")
CODE = {
    LiquidityEventType.TOUCH: "T",
    LiquidityEventType.SWEEP: "S",
    LiquidityEventType.SWEEP_FAILED: "F",
    LiquidityEventType.BREAK: "B",
    LiquidityEventType.RUN: "R",
    LiquidityEventType.RECLAIM: "C",
}


@pytest.mark.parametrize("seed", SEEDS)
def test_event_invariants_and_transition_grammar(seed):
    candles = random_walk_candles(400, seed)
    result = analyse(
        candles, key_levels_for(candles), StructureConfig.from_spec(), LiquidityConfig.from_spec()
    )
    by_time = {c.open_time: c for c in candles}
    pools = {p.id: p for p in result.pools}
    per_pool: dict[str, str] = {}
    for e in result.events:
        c = by_time[e.time]
        up = e.side is LiquiditySide.BSL
        price = e.price

        def beyond(v: float, up: bool = up, price: float = price) -> bool:
            return v > price if up else v < price

        if e.type is LiquidityEventType.SWEEP:
            assert beyond(e.extreme) and not beyond(c.close)
        elif e.type is LiquidityEventType.BREAK:
            assert beyond(c.close)
        elif e.type is LiquidityEventType.RECLAIM:
            assert not beyond(c.close)
        elif e.type in (LiquidityEventType.RUN, LiquidityEventType.SWEEP_FAILED):
            assert beyond(c.close)
        elif e.type is LiquidityEventType.TOUCH:
            assert not beyond(e.extreme)
        assert pools[e.pool_id].known_at <= e.time, "pool used before it was known"
        per_pool[e.pool_id] = per_pool.get(e.pool_id, "") + CODE[e.type]
    assert per_pool, "scenario produced no events"
    for pid, seq in per_pool.items():
        assert GRAMMAR.match(seq), (pid, seq)
    counts = {code: sum(seq.count(code) for seq in per_pool.values()) for code in "TSFBRC"}
    assert counts["S"] > 0 and counts["B"] > 0, counts  # both sweep and break paths are exercised
    for p in result.pools:
        assert (p.magnet_score is None) == p.taken
        assert p.magnet_score is None or 0 <= p.magnet_score <= 100


def test_trend_input_only_changes_scores_not_events():
    candles = random_walk_candles(300, 4)
    s_cfg, l_cfg = StructureConfig.from_spec(), LiquidityConfig.from_spec()
    internal = analyze_level(candles, StructureLevel.INTERNAL, s_cfg)
    external = analyze_level(candles, StructureLevel.EXTERNAL, s_cfg)
    bull = analyze_liquidity(candles, internal, external, [], TrendDirection.BULLISH, l_cfg)
    bear = analyze_liquidity(candles, internal, external, [], TrendDirection.BEARISH, l_cfg)
    assert bull.events == bear.events
    assert [p.id for p in bull.pools] == [p.id for p in bear.pools]
