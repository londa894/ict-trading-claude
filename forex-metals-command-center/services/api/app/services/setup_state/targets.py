"""Target eligibility for setup objectives.

The setup engine targets the NEAREST untaken pool beyond price. Nothing requires that
pool to be far enough away to be worth trading, but the entry plan requires TP1 to be
at least `minTp1Atr` (1.0) ATR of reward. A target inside that radius therefore cannot
produce a tradeable plan: the engine confirms an entry model, builds a plan, and then
discards it with "TP1 closer than 1.0 ATR (do not chase)".

Applying a distance floor at selection makes the objective consistent with the reward
rule, so setups aim at liquidity that is actually tradeable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.liquidity.models import LiquidityPool


def eligible_targets[P: "LiquidityPool"](
    pools: list[P], *, price: float, atr: float, min_atr: float
) -> list[P]:
    """Pools far enough from `price` to be a tradeable objective.

    Fails OPEN: a non-positive floor, or a non-positive ATR (possible early in a
    series), keeps every candidate rather than silently starving the engine of targets.
    """
    if min_atr <= 0.0 or atr <= 0.0:
        return list(pools)
    floor = min_atr * atr
    return [p for p in pools if abs(p.price - price) >= floor]
