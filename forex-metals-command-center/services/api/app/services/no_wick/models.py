"""No Wick Architecture V1 models and configuration (spec STEP 13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    Direction,
    NoWickClassification,
    NoWickContextFactor,
    NoWickStrength,
    NoWickVariant,
    NoWickZoneEventType,
    NoWickZoneState,
    ScoreComponentStatus,
    Timeframe,
)


@dataclass(frozen=True)
class NoWickConfig:
    atr_period: int
    true_body_pct: float
    true_max_wick_pct: float
    near_body_pct: float
    near_max_wick_pct: float
    one_sided_max_wick_pct: float
    one_sided_min_body_pct: float
    meaningful_body_atr: float
    strong_body_atr: float
    exceptional_body_atr: float
    median_lookback: int
    q_origin_wick: float
    q_destination_wick: float
    q_body_pct: float
    q_body_atr: float
    c_displacement: dict[str, float]
    c_structure: dict[str, float]
    c_liquidity: float
    c_liquidity_lookback_bars: int
    c_fvg: float
    c_trend: float
    c_session: dict[str, float]
    relevance_quality_weight: float
    relevance_context_weight: float
    inside_bar_multiplier: float
    zone_touch_tolerance_atr: float
    zone_approach_distance_atr: float
    zone_reaction_atr: float
    zone_reaction_window_bars: int
    variant_late_min_maturity_pct: float
    variant_early_max_maturity_pct: float
    variant_max_wick_pct: float
    variant_min_body_pct: float
    variant_origin_weights: dict[str, float]

    @classmethod
    def from_spec(cls) -> NoWickConfig:
        s = load_spec("no_wick")
        c, st, q, ctx, r, z = (
            s[k] for k in ("classification", "strength", "quality", "context", "relevance", "zone")
        )
        v = s["variants"]
        return cls(
            atr_period=int(load_spec("structure")["atrPeriod"]),
            true_body_pct=float(c["trueBodyPct"]),
            true_max_wick_pct=float(c["trueMaxWickPct"]),
            near_body_pct=float(c["nearBodyPct"]),
            near_max_wick_pct=float(c["nearMaxWickPct"]),
            one_sided_max_wick_pct=float(c["oneSidedMaxWickPct"]),
            one_sided_min_body_pct=float(c["oneSidedMinBodyPct"]),
            meaningful_body_atr=float(st["meaningfulBodyAtr"]),
            strong_body_atr=float(st["strongBodyAtr"]),
            exceptional_body_atr=float(st["exceptionalBodyAtr"]),
            median_lookback=int(s["medianLookback"]),
            q_origin_wick=float(q["originWick"]),
            q_destination_wick=float(q["destinationWick"]),
            q_body_pct=float(q["bodyPct"]),
            q_body_atr=float(q["bodyAtr"]),
            c_displacement={k: float(v) for k, v in ctx["displacement"].items()},
            c_structure={k: float(v) for k, v in ctx["structure"].items()},
            c_liquidity=float(ctx["liquidity"]),
            c_liquidity_lookback_bars=int(ctx["liquidityLookbackBars"]),
            c_fvg=float(ctx["fvg"]),
            c_trend=float(ctx["trend"]),
            c_session={k: float(v) for k, v in ctx["session"].items()},
            relevance_quality_weight=float(r["qualityWeight"]),
            relevance_context_weight=float(r["contextWeight"]),
            inside_bar_multiplier=float(r["insideBarMultiplier"]),
            zone_touch_tolerance_atr=float(z["touchToleranceAtr"]),
            zone_approach_distance_atr=float(z["approachDistanceAtr"]),
            zone_reaction_atr=float(z["reactionAtr"]),
            zone_reaction_window_bars=int(z["reactionWindowBars"]),
            variant_late_min_maturity_pct=float(v["lateCandleMinMaturityPct"]),
            variant_early_max_maturity_pct=float(v["earlyCandleMaxMaturityPct"]),
            variant_max_wick_pct=float(v["maxWickPct"]),
            variant_min_body_pct=float(v["minBodyPct"]),
            variant_origin_weights={k: float(val) for k, val in v["originTimeframeWeights"].items()},
        )


class CandleFeatures(ApiModel):
    """Computed for EVERY closed candle. Ratios are null when undefined (zero range / no prior candles)."""

    time: datetime
    range: float
    body: float
    upper_wick: float
    lower_wick: float
    body_pct: float | None
    upper_wick_pct: float | None
    lower_wick_pct: float | None
    body_atr: float | None
    range_atr: float | None
    body_to_median: float | None
    range_to_median: float | None
    close_location_pct: float | None


class ScoreComponent(ApiModel):
    factor: NoWickContextFactor
    status: ScoreComponentStatus
    points: float
    max_points: float
    detail: str


class NoWickEvent(ApiModel):
    id: str
    direction: Direction
    shape: NoWickClassification  # the candle's no-wick shape (one of the 8 directional classes)
    classification: NoWickClassification  # == shape, or INSIGNIFICANT_NO_WICK when the body is too small
    tags: list[NoWickClassification]  # NO_ORIGIN_SIDE_WICK / NO_DESTINATION_SIDE_WICK when present
    strength: NoWickStrength
    time: datetime
    open: float
    high: float
    low: float
    close: float
    body_pct: float
    upper_wick_pct: float
    lower_wick_pct: float
    body_atr: float
    close_location_pct: float
    inside_bar: bool
    candle_quality_score: float
    context_score: float
    relevance_score: float
    context_components: list[ScoreComponent]
    zone_id: str | None


class NoWickZone(ApiModel):
    """Rebalance zone over the body of a MEANINGFUL+ no-wick candle, measured from the close (destination)."""

    id: str
    event_id: str
    direction: Direction  # BULLISH zones support from below the close; BEARISH zones resist from above
    close_level: float
    level_25: float
    level_50: float
    level_75: float
    open_level: float  # full body
    origin_extreme: float  # candle low (bullish) / high (bearish)
    fvg_overlap_ids: list[str]  # FVGs known at the event candle close that overlap the body
    ob_overlap: ScoreComponentStatus  # NOT_EVALUATED until order blocks exist
    state: NoWickZoneState
    rebalance_pct: float
    created_at: datetime
    known_at: datetime
    state_changed_at: datetime | None
    age_bars: int
    active: bool
    relevance_score: float


class NoWickZoneEvent(ApiModel):
    id: str
    zone_id: str
    direction: Direction
    type: NoWickZoneEventType
    time: datetime
    price: float
    detail: str


class FormingCandle(ApiModel):
    """The currently-forming (not-yet-closed) candle on this timeframe, with how far through its
    bucket it is and its running geometry.

    Read-only context for the reversal playbook's LATE_CANDLE_FADE / EARLY_CANDLE_CONTINUATION
    variants (spec section 1). It is NEVER fed into a verdict or a no-wick event — the decision
    engine stays strictly closed-bar; this only describes the live bar as it stands right now.
    Ratios follow CandleFeatures (fraction 0..1, null when range is zero)."""

    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    as_of: datetime  # the clock at which this snapshot was taken
    maturity_pct: float  # 0..100, wall-clock elapsed through the bucket
    direction: Direction | None  # BULLISH close>open, BEARISH close<open, null when flat
    open: float
    high: float
    low: float
    close: float  # last traded price so far
    range: float
    body: float
    upper_wick: float
    lower_wick: float
    body_pct: float | None
    upper_wick_pct: float | None
    lower_wick_pct: float | None
    close_location_pct: float | None  # (close - low) / range * 100
    variant: NoWickVariant | None  # section 1 forming-candle signal; null when neither variant applies
    signal_direction: Direction | None  # fade / continuation direction of the variant
    origin_weight: float  # origin-timeframe directional-quality weight (0 when the TF is not weighted)


class NoWickAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    features: list[CandleFeatures]
    events: list[NoWickEvent]
    zones: list[NoWickZone]
    zone_events: list[NoWickZoneEvent]
    forming: FormingCandle | None  # the live bar (context only); null when the last bar is closed
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
