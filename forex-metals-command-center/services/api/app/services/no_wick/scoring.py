"""CandleQualityScore, ContextScore and NoWickRelevanceScore, kept separate (spec STEP 13).

CandleQualityScore (0-100) uses only the candle: origin wick, destination wick, body %, body/ATR.
ContextScore (0-100) uses only facts known at the candle's close (no lookahead). Each factor is reported as a
component; a factor whose source engine is missing is NOT_EVALUATED (never silently scored as 0):
    DISPLACEMENT  same-direction displacement event emitted ON this candle (grade points)
    STRUCTURE     same-direction confirmed BOS/CHoCH/MSS ON this candle (best type points)
    LIQUIDITY     opposite-side SWEEP/RECLAIM within `liquidityLookbackBars` up to and including this candle
    FVG           same-direction FVG CREATED ON this candle (it is the gap's third candle)
    TREND         external structure trend as of this candle (last confirmed BOS/MSS direction)
    SESSION       time quality of the candle's open time (IDEAL/ACCEPTABLE points)
    NEWS          NOT_EVALUATED until Phase 13
An FVG completed by a LATER candle can never add points: only facts at or before the candle count.
NoWickRelevanceScore = qualityWeight*quality + contextWeight*context, x insideBarMultiplier for inside bars,
0 for INSIGNIFICANT events. None of these scores is a probability, and none authorizes a trade.
"""

from __future__ import annotations

from app.domain.enums import (
    Direction,
    NoWickContextFactor,
    NoWickStrength,
    ScoreComponentStatus,
)
from app.services.no_wick.features import Classification
from app.services.no_wick.models import CandleFeatures, NoWickConfig, ScoreComponent

F = NoWickContextFactor
EVALUATED = ScoreComponentStatus.EVALUATED
NOT_EVALUATED = ScoreComponentStatus.NOT_EVALUATED


def candle_quality(cls: Classification, f: CandleFeatures, cfg: NoWickConfig) -> float:
    origin = cfg.q_origin_wick * (1 - min(1.0, cls.origin_wick_pct / cfg.one_sided_max_wick_pct))
    destination = cfg.q_destination_wick * (1 - min(1.0, cls.destination_wick_pct / cfg.near_max_wick_pct))
    body = cfg.q_body_pct * max(0.0, min(1.0, ((f.body_pct or 0) - 0.5) / 0.5))
    strength = cfg.q_body_atr * min(1.0, (f.body_atr or 0) / cfg.exceptional_body_atr)
    return round(origin + destination + body + strength, 1)


def component(
    factor: NoWickContextFactor, points: float | None, max_points: float, detail: str
) -> ScoreComponent:
    if points is None:
        return ScoreComponent(
            factor=factor, status=NOT_EVALUATED, points=0.0, max_points=max_points, detail=detail
        )
    return ScoreComponent(
        factor=factor, status=EVALUATED, points=points, max_points=max_points, detail=detail
    )


def not_evaluated(factor: NoWickContextFactor, why: str) -> ScoreComponent:
    return component(factor, None, 0.0, why)


def context_total(components: list[ScoreComponent]) -> float:
    return round(min(100.0, sum(c.points for c in components if c.status is EVALUATED)), 1)


def relevance(
    quality: float, context: float, strength: NoWickStrength, inside_bar: bool, cfg: NoWickConfig
) -> float:
    if strength is NoWickStrength.INSIGNIFICANT:
        return 0.0
    score = cfg.relevance_quality_weight * quality + cfg.relevance_context_weight * context
    if inside_bar:
        score *= cfg.inside_bar_multiplier
    return round(max(0.0, min(100.0, score)), 1)


def trend_matches(trend: Direction | None, direction: Direction) -> bool:
    return trend is direction
