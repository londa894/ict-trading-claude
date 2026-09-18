"""Setups should only spawn from sweeps of SIGNIFICANT liquidity pools.

Measured on real XAUUSD M15: ~88% of all sweeps are ordinary SWING_HIGH/SWING_LOW
pools, while the levels ICT methodology treats as meaningful (PDH/PDL, PWH/PWL,
session extremes, equal highs/lows) are ~12%. The liquidity spec already ranks
INTERNAL_SWING lowest (weight 10 vs 25-30 for daily/weekly), but the setup engine
accepts every sweep equally.

magnet_score cannot be used for this: it is None for taken pools, and a swept pool
is taken, so every swept pool is unscored. Filtering is therefore by pool TYPE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.enums import LiquidityPoolType

if TYPE_CHECKING:
    from app.services.liquidity.models import LiquidityEvent

# Pools that represent liquidity a desk would actually hunt, in the spec's own ranking order.
SIGNIFICANT_POOL_TYPES: frozenset[LiquidityPoolType] = frozenset(
    {
        LiquidityPoolType.PWH,
        LiquidityPoolType.PWL,
        LiquidityPoolType.PDH,
        LiquidityPoolType.PDL,
        LiquidityPoolType.EQH,
        LiquidityPoolType.EQL,
        LiquidityPoolType.ASIA_HIGH,
        LiquidityPoolType.ASIA_LOW,
        LiquidityPoolType.LONDON_HIGH,
        LiquidityPoolType.LONDON_LOW,
        LiquidityPoolType.NY_AM_HIGH,
        LiquidityPoolType.NY_AM_LOW,
        LiquidityPoolType.NY_PM_HIGH,
        LiquidityPoolType.NY_PM_LOW,
    }
)


def sweep_is_significant(event: LiquidityEvent, restrict: bool) -> bool:
    """True when this sweep may start a setup.

    With `restrict` off every sweep qualifies, so the shipped strategy is unchanged.
    With it on, only sweeps of key levels and session extremes qualify: plain swing
    highs and lows are excluded as noise.
    """
    if not restrict:
        return True
    return event.pool_type in SIGNIFICANT_POOL_TYPES
