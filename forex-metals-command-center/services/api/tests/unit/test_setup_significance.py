"""Setups should only spawn from sweeps of SIGNIFICANT liquidity pools.

Measured on real XAUUSD M15: ~88% of sweeps are plain SWING_HIGH/SWING_LOW, while
key levels and session extremes are ~12%. These tests pin the filter, including that
it is OFF by default so the shipped strategy is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.enums import LiquidityEventType, LiquidityPoolType, LiquiditySide
from app.services.liquidity.models import LiquidityEvent
from app.services.setup_state.models import SetupConfig
from app.services.setup_state.significance import SIGNIFICANT_POOL_TYPES, sweep_is_significant


def event(pool_type: LiquidityPoolType) -> LiquidityEvent:
    return LiquidityEvent(
        id="e1",
        pool_id="p1",
        pool_type=pool_type,
        side=LiquiditySide.BSL,
        type=LiquidityEventType.SWEEP,
        price=2000.0,
        time=datetime(2024, 3, 7, 14, 0, tzinfo=UTC),
        extreme=2001.0,
        close=1999.0,
    )


def test_filter_is_off_by_default_so_behaviour_is_unchanged():
    assert SetupConfig.from_spec().significant_sweeps_only is False


def test_with_the_filter_off_even_a_minor_swing_qualifies():
    assert sweep_is_significant(event(LiquidityPoolType.SWING_LOW), False)


@pytest.mark.parametrize(
    "pool_type",
    [
        LiquidityPoolType.PWH,
        LiquidityPoolType.PWL,
        LiquidityPoolType.PDH,
        LiquidityPoolType.PDL,
        LiquidityPoolType.EQH,
        LiquidityPoolType.EQL,
        LiquidityPoolType.ASIA_HIGH,
        LiquidityPoolType.LONDON_LOW,
        LiquidityPoolType.NY_AM_HIGH,
        LiquidityPoolType.NY_PM_LOW,
    ],
)
def test_key_levels_and_session_extremes_qualify(pool_type):
    assert sweep_is_significant(event(pool_type), True)


@pytest.mark.parametrize("pool_type", [LiquidityPoolType.SWING_HIGH, LiquidityPoolType.SWING_LOW])
def test_plain_swings_are_excluded_when_filtering(pool_type):
    """The 88% bucket: ordinary swings must not start setups under the filter."""
    assert not sweep_is_significant(event(pool_type), True)


def test_every_pool_type_is_deliberately_classified():
    """A new pool type must be an explicit decision, not silently excluded."""
    unclassified = set(LiquidityPoolType) - SIGNIFICANT_POOL_TYPES
    assert unclassified == {LiquidityPoolType.SWING_HIGH, LiquidityPoolType.SWING_LOW}
