"""Property tests over random candle series: no lookahead, internal consistency, determinism."""

import pytest

from app.domain.enums import StructureEventStatus, StructureLevel
from app.services.structure.engine import analyze_level
from app.services.structure.models import StructureConfig
from tests.structure_helpers import cfg, random_walk_candles

SEEDS = [1, 2, 3, 7, 11]
LEVEL_CONFIGS = [
    ("pivot1", cfg(pivot=1)),
    ("pivot3", cfg(pivot=3)),
    ("spec-defaults", StructureConfig.from_spec()),
]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(("name", "config"), LEVEL_CONFIGS)
@pytest.mark.parametrize("level", list(StructureLevel))
def test_no_lookahead_every_prefix_agrees_with_full_history(seed, name, config, level):
    candles = random_walk_candles(260, seed)
    full = analyze_level(candles, level, config)
    for k in range(5, len(candles) + 1, 3):
        prefix = analyze_level(candles[:k], level, config)
        last_open = candles[k - 1].open_time
        last_close = candles[k - 1].close_time

        # Events known by candle k-1 are exactly the full-history events up to that candle.
        expected_events = [e for e in full.events if e.time <= last_open]
        assert prefix.events == expected_events, (name, level, k)

        # Swings known by then are identical, except that breaks happening later are not yet known.
        expected_swings = [s for s in full.swings if s.confirmed_at <= last_close]
        assert [s.id for s in prefix.swings] == [s.id for s in expected_swings]
        for p, f in zip(prefix.swings, expected_swings, strict=True):
            assert (p.label, p.price, p.time, p.confirmed_at) == (f.label, f.price, f.time, f.confirmed_at)
            if f.broken_at is not None and f.broken_at <= last_open:
                assert (p.broken_at, p.broken_by) == (f.broken_at, f.broken_by)
            else:
                assert p.broken_at is None and p.broken_by is None


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("level", list(StructureLevel))
def test_consistency_invariants(seed, level):
    candles = random_walk_candles(400, seed)
    lvl = analyze_level(candles, level, StructureConfig.from_spec())
    times = {c.open_time for c in candles}
    swings = {s.id: s for s in lvl.swings}

    assert len({e.id for e in lvl.events}) == len(lvl.events)
    assert [e.time for e in lvl.events] == sorted(e.time for e in lvl.events)
    broken_by_counts: dict[str, int] = {}
    for e in lvl.events:
        assert e.time in times and e.broken_swing_id in swings
        swing = swings[e.broken_swing_id]
        assert swing.confirmed_at <= e.time, "event used a swing before it was confirmed"
        if e.status is StructureEventStatus.CONFIRMED:
            candle = next(c for c in candles if c.open_time == e.time)
            if e.direction == "BULLISH":
                assert candle.close > e.price
            else:
                assert candle.close < e.price
            broken_by_counts[e.broken_swing_id] = broken_by_counts.get(e.broken_swing_id, 0) + 1
        else:
            candle = next(c for c in candles if c.open_time == e.time)
            assert (candle.high > e.price) if e.direction == "BULLISH" else (candle.low < e.price)
    assert all(n == 1 for n in broken_by_counts.values()), "a swing was broken more than once"
    assert not any(e.ambiguous for e in lvl.events)  # unreachable with close-confirmation (guard only)
    for prot in (lvl.protected_high, lvl.protected_low):
        assert prot is None or prot.broken_at is None


def test_same_input_same_output():
    candles = random_walk_candles(300, 5)
    config = StructureConfig.from_spec()
    assert (
        analyze_level(candles, StructureLevel.EXTERNAL, config).model_dump()
        == analyze_level(candles, StructureLevel.EXTERNAL, config).model_dump()
    )
