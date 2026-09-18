"""Target selection must not pick pools that are too close to trade.

target() locks the NEAREST untaken pool beyond price, with no distance floor. The
entry plan then requires TP1 >= minTp1Atr (1.0) ATR of reward, so a target nearer
than that can never produce a tradeable plan: the engine builds a plan and rejects
it with "TP1 closer than 1.0 ATR (do not chase)".

`minTargetAtr` puts a floor on target selection so the objective is consistent with
the reward rule. Default 0.0 (off) keeps the shipped strategy unchanged.
"""

from __future__ import annotations

from app.services.setup_state.models import SetupConfig
from app.services.setup_state.targets import eligible_targets


class FakePool:
    def __init__(self, pool_id: str, price: float) -> None:
        self.id = pool_id
        self.price = price


def test_default_config_has_no_floor_so_behaviour_is_unchanged():
    assert SetupConfig.from_spec().min_target_atr == 0.0


def test_no_floor_keeps_every_candidate():
    pools = [FakePool("near", 2001.0), FakePool("far", 2050.0)]
    kept = eligible_targets(pools, price=2000.0, atr=10.0, min_atr=0.0)
    assert [p.id for p in kept] == ["near", "far"]


def test_floor_drops_pools_closer_than_the_minimum():
    """A pool 1.0 away with ATR 10 is 0.1 ATR out: unreachable as a 1.0 ATR reward."""
    pools = [FakePool("near", 2001.0), FakePool("far", 2050.0)]
    kept = eligible_targets(pools, price=2000.0, atr=10.0, min_atr=1.0)
    assert [p.id for p in kept] == ["far"]


def test_floor_is_inclusive_at_exactly_the_minimum():
    pools = [FakePool("exact", 2010.0)]
    assert len(eligible_targets(pools, price=2000.0, atr=10.0, min_atr=1.0)) == 1


def test_floor_applies_symmetrically_below_price():
    """Bearish setups target pools below price; distance is absolute."""
    pools = [FakePool("near", 1999.0), FakePool("far", 1950.0)]
    kept = eligible_targets(pools, price=2000.0, atr=10.0, min_atr=1.0)
    assert [p.id for p in kept] == ["far"]


def test_a_non_positive_atr_disables_the_floor_rather_than_dropping_everything():
    """ATR can be 0 early in a series; failing open keeps current behaviour."""
    pools = [FakePool("near", 2001.0)]
    assert len(eligible_targets(pools, price=2000.0, atr=0.0, min_atr=1.0)) == 1


def test_returns_empty_when_no_pool_clears_the_floor():
    pools = [FakePool("near", 2001.0)]
    assert eligible_targets(pools, price=2000.0, atr=10.0, min_atr=1.0) == []
