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
    LiquidityEligibility,
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


_WEEK_LEVELS = frozenset({LiquidityPoolType.PWH, LiquidityPoolType.PWL})
_DAY_LEVELS = frozenset({LiquidityPoolType.PDH, LiquidityPoolType.PDL})
_SESSION_LEVELS = frozenset(
    {
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


def significance_rank(pool_type: LiquidityPoolType) -> int:
    """Significant-liquidity tier (spec section 5): week > day > session > everything else.

    Used as a deterministic tiebreak when eligible pools score alike (higher timeframe wins), and by the
    setup layer to score session/day/week-aligned "silver bullet" macro windows."""
    if pool_type in _WEEK_LEVELS:
        return 3
    if pool_type in _DAY_LEVELS:
        return 2
    if pool_type in _SESSION_LEVELS:
        return 1
    return 0


def _counter_trend_external(p: LiquidityPool, trend: TrendDirection) -> bool:
    """External liquidity against the HTF trend — not a bias-aligned continuation draw (spec section 5).

    Internal pools are always eligible (pullback liquidity); the filter only excludes external liquidity
    on the wrong side of a directional trend."""
    if p.scope is not LiquidityScope.EXTERNAL:
        return False
    if trend is TrendDirection.BULLISH:
        return p.side is LiquiditySide.SSL  # external lows are not a draw in an uptrend
    if trend is TrendDirection.BEARISH:
        return p.side is LiquiditySide.BSL  # external highs are not a draw in a downtrend
    return False


def _eligibility(
    trend: TrendDirection, reversal_exempt: bool, primary: LiquidityPool | None
) -> LiquidityEligibility:
    if reversal_exempt:
        return LiquidityEligibility.REVERSAL_EXEMPT
    if trend is TrendDirection.NONE:
        return LiquidityEligibility.UNRESTRICTED
    if primary is not None and primary.scope is LiquidityScope.EXTERNAL:
        return LiquidityEligibility.EXTERNAL_TREND_ALIGNED
    return LiquidityEligibility.INTERNAL_ONLY


def select_dol(
    pools: Sequence[LiquidityPool],
    atr: float,
    cfg: LiquidityConfig,
    trend: TrendDirection = TrendDirection.NONE,
    *,
    reversal_exempt: bool = False,
) -> DolSelection:
    """Pick the draw-on-liquidity target.

    For bias-aligned continuation (the default), counter-trend external pools are ineligible: in an
    uptrend the draw is external highs (+ internal pullback liquidity), in a downtrend external lows.
    A reversal setup passes ``reversal_exempt=True`` to bypass the filter and target the opposite draw.
    """
    scored = [p for p in pools if not p.taken and p.magnet_score is not None and p.distance_atr is not None]
    filter_on = not reversal_exempt and trend is not TrendDirection.NONE
    eligible = [p for p in scored if not _counter_trend_external(p, trend)] if filter_on else scored
    # Rank by magnet score, then significant-liquidity tier (week > day > session), then proximity.
    candidates = sorted(
        eligible,
        key=lambda p: (-(p.magnet_score or 0.0), -significance_rank(p.type), p.distance_atr or 0.0, p.id),
    )
    if atr <= 0:
        return DolSelection(
            primary=None,
            secondary=None,
            confidence=DolConfidence.UNCLEAR,
            eligibility=_eligibility(trend, reversal_exempt, None),
            margin=None,
            reason="ATR is zero; distances undefined",
        )
    if not candidates:
        no_draw = "No untaken trend-aligned liquidity pools" if filter_on else "No untaken liquidity pools"
        return DolSelection(
            primary=None,
            secondary=None,
            confidence=DolConfidence.UNCLEAR,
            eligibility=_eligibility(trend, reversal_exempt, None),
            margin=None,
            reason=no_draw,
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
        eligibility=_eligibility(trend, reversal_exempt, primary),
        margin=margin,
        reason=reason,
    )
