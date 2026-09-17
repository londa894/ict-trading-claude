"""LiquidityMagnetScore (0-100) and draw-on-liquidity selection.

The score ranks untaken pools by how strongly they are likely to attract price *as a rule of thumb*;
it is a deterministic weighting, not a probability. Components (weights in liquidity.json):
    type        PWH/PWL > PDH/PDL = EQH/EQL > external swing = session high/low > internal swing
    cluster     EQ member count + touch episodes (capped)
    proximity   linear decay to 0 at `proximityZeroAtr` ATRs from the last close
    stack       other untaken pools within `stackToleranceAtr` (confluence, capped)
    trend       BSL in a bullish / SSL in a bearish external structure trend

DOL confidence uses the score margin between the primary DOL and the best pool on the opposite side;
a small margin means two-sided liquidity -> UNCLEAR (DOL_UNCLEAR, WAIT).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.enums import (
    DolConfidence,
    LiquidityPoolType,
    LiquidityScope,
    LiquiditySide,
    TrendDirection,
)
from app.services.liquidity.models import DolSelection, DolTarget, LiquidityConfig, LiquidityPool


class _ScorablePool(Protocol):
    id: str
    type: LiquidityPoolType
    side: LiquiditySide
    scope: LiquidityScope
    price: float
    touches: int

    @property
    def member_count(self) -> int: ...


def _type_key(p: _ScorablePool) -> str:
    if p.type in (LiquidityPoolType.SWING_HIGH, LiquidityPoolType.SWING_LOW):
        return "EXTERNAL_SWING" if p.scope is LiquidityScope.EXTERNAL else "INTERNAL_SWING"
    if p.type.value.endswith(("_HIGH", "_LOW")):
        return "SESSION"
    return p.type.value


def magnet_scores(
    untaken: Sequence[_ScorablePool],
    last_close: float,
    atr: float,
    trend: TrendDirection,
    cfg: LiquidityConfig,
) -> dict[str, float]:
    m = cfg.magnet
    scores: dict[str, float] = {}
    if atr <= 0:
        return scores
    for p in untaken:
        type_points = m.type.get(_type_key(p), 0.0)
        extra_members = max(0, p.member_count - 1)
        cluster = min(m.cluster_max, m.cluster_per_member * extra_members + m.touch_points * p.touches)
        distance = abs(p.price - last_close) / atr
        proximity = m.proximity_max * max(0.0, 1.0 - distance / m.proximity_zero_atr)
        stacked = sum(
            1 for o in untaken if o.id != p.id and abs(o.price - p.price) <= m.stack_tolerance_atr * atr
        )
        stack = min(m.stack_max, m.stack_per_pool * stacked)
        aligned = (p.side is LiquiditySide.BSL and trend is TrendDirection.BULLISH) or (
            p.side is LiquiditySide.SSL and trend is TrendDirection.BEARISH
        )
        total = type_points + cluster + proximity + stack + (m.trend_alignment if aligned else 0.0)
        scores[p.id] = round(max(0.0, min(100.0, total)), 1)
    return scores


def _target(p: LiquidityPool) -> DolTarget:
    assert p.magnet_score is not None and p.distance_atr is not None
    return DolTarget(
        pool_id=p.id,
        type=p.type,
        side=p.side,
        label=p.label,
        price=p.price,
        magnet_score=p.magnet_score,
        distance_atr=p.distance_atr,
    )


def select_dol(pools: Sequence[LiquidityPool], atr: float, cfg: LiquidityConfig) -> DolSelection:
    candidates = sorted(
        (p for p in pools if not p.taken and p.magnet_score is not None and p.distance_atr is not None),
        key=lambda p: (-(p.magnet_score or 0.0), p.distance_atr or 0.0, p.id),
    )
    if atr <= 0:
        return DolSelection(
            primary=None,
            secondary=None,
            confidence=DolConfidence.UNCLEAR,
            margin=None,
            reason="ATR is zero; distances undefined",
        )
    if not candidates:
        return DolSelection(
            primary=None,
            secondary=None,
            confidence=DolConfidence.UNCLEAR,
            margin=None,
            reason="No untaken liquidity pools",
        )
    primary = candidates[0]
    secondary = next(
        (p for p in candidates[1:] if abs(p.price - primary.price) > cfg.dol_distinct_price_atr * atr), None
    )
    opposite = next((p for p in candidates if p.side is not primary.side), None)
    primary_score = primary.magnet_score or 0.0
    margin = round(primary_score - (opposite.magnet_score or 0.0 if opposite else 0.0), 1)
    if margin >= cfg.dol_high_margin:
        confidence = DolConfidence.HIGH
    elif margin >= cfg.dol_moderate_margin:
        confidence = DolConfidence.MODERATE
    elif margin >= cfg.dol_low_margin:
        confidence = DolConfidence.LOW
    else:
        confidence = DolConfidence.UNCLEAR
    if confidence is DolConfidence.UNCLEAR:
        reason = (
            f"Two-sided liquidity: {primary.side.value} {primary_score} vs "
            f"{opposite.side.value if opposite else '-'} {opposite.magnet_score if opposite else '-'} "
            f"(margin {margin} < {cfg.dol_low_margin})"
        )
    else:
        reason = f"{primary.label} leads the opposite side by {margin} points"
    return DolSelection(
        primary=_target(primary),
        secondary=_target(secondary) if secondary else None,
        confidence=confidence,
        margin=margin,
        reason=reason,
    )
