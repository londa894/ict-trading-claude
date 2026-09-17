"""No Wick rebalance zones (deterministic, candle-sequential).

Zone over the body of a MEANINGFUL+ no-wick candle, measured from the close (destination) toward the open:
    bullish: close_level = close (top), open_level = open, origin_extreme = low   (support from above)
    bearish: close_level = close (bottom), open_level = open, origin_extreme = high (resistance from below)
    references: 25% / 50% / 75% of the body from the close, full body = open, origin side = extreme.

Evaluated from the candle after creation, in this order per candle:
 1. depth: rebalance% = wick penetration past close_level / body * 100 (monotonic max). Crossing each
    reference emits TOUCHED (edge within tolerance) -> REBALANCE_25 -> REBALANCE_50 -> REBALANCE_75 ->
    FULLY_REBALANCED, in order, once each. States: TOUCHED (<25) / PARTIAL (>=25) / HALF_REBALANCED (>=50)
    / FULLY_REBALANCED (>=100).
 2. INVALIDATED (terminal): close beyond the origin extreme.
 3. FAILED: close beyond the open (full body lost) - may still be invalidated later.
 4. REACTED (terminal): after a touch and within `reactionWindowBars` of the first touch, a close back
    beyond the close level by `reactionAtr` x ATR(at creation), unless the zone already FAILED.
APPROACHING is derived for the as-of view only (untouched and within `approachDistanceAtr`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.candle import Candle
from app.domain.enums import Direction, NoWickZoneEventType, NoWickZoneState

E = NoWickZoneEventType
S = NoWickZoneState
REFERENCES: list[tuple[float, NoWickZoneEventType, NoWickZoneState]] = [
    (0.0, E.TOUCHED, S.TOUCHED),
    (25.0, E.REBALANCE_25, S.PARTIAL),
    (50.0, E.REBALANCE_50, S.HALF_REBALANCED),
    (75.0, E.REBALANCE_75, S.HALF_REBALANCED),
    (100.0, E.FULLY_REBALANCED, S.FULLY_REBALANCED),
]
TERMINAL = frozenset({S.REACTED, S.INVALIDATED})


@dataclass
class ZoneRuntime:
    id: str
    event_id: str
    direction: Direction
    close_level: float
    open_level: float
    origin_extreme: float
    created_index: int
    atr_at_creation: float
    relevance_score: float
    fvg_overlap_ids: list[str] = field(default_factory=list)
    state: NoWickZoneState = S.FRESH
    rebalance_pct: float = 0.0
    references_hit: int = 0  # how many REFERENCES were emitted
    first_touch_index: int | None = None
    state_index: int | None = None
    failed: bool = False

    @property
    def body(self) -> float:
        return abs(self.close_level - self.open_level)

    def level(self, pct: float) -> float:
        sign = -1.0 if self.direction is Direction.BULLISH else 1.0
        return self.close_level + sign * self.body * pct / 100


Emit = Callable[[ZoneRuntime, NoWickZoneEventType, int, float, str], None]


def evaluate_zone(
    z: ZoneRuntime, c: Candle, i: int, touch_tol: float, reaction_atr: float, window: int, emit: Emit
) -> None:
    if z.state in TERMINAL or i <= z.created_index or z.body <= 0:
        return
    bullish = z.direction is Direction.BULLISH
    penetration = (z.close_level - c.low) if bullish else (c.high - z.close_level)
    if penetration >= -touch_tol:
        z.rebalance_pct = max(z.rebalance_pct, round(min(100.0, max(0.0, penetration / z.body * 100)), 2))
        while z.references_hit < len(REFERENCES) and z.rebalance_pct >= REFERENCES[z.references_hit][0]:
            _, kind, state = REFERENCES[z.references_hit]
            z.references_hit += 1
            if z.first_touch_index is None:
                z.first_touch_index = i
            if not z.failed:
                z.state = state
            emit(z, kind, i, c.low if bullish else c.high, f"rebalance {z.rebalance_pct:.1f}%")

    beyond_origin = c.close < z.origin_extreme if bullish else c.close > z.origin_extreme
    beyond_open = c.close < z.open_level if bullish else c.close > z.open_level
    if beyond_origin:
        z.state = S.INVALIDATED
        emit(z, E.INVALIDATED, i, c.close, "close beyond origin extreme")
        return
    if beyond_open and not z.failed:
        z.failed = True
        z.state = S.FAILED
        emit(z, E.FAILED, i, c.close, "close beyond open (full body lost)")
        return
    if z.failed or z.first_touch_index is None or i - z.first_touch_index > window:
        return
    reacted = (
        (c.close >= z.close_level + reaction_atr) if bullish else (c.close <= z.close_level - reaction_atr)
    )
    if reacted:
        z.state = S.REACTED
        emit(z, E.REACTED, i, c.close, "closed back beyond the close level")
