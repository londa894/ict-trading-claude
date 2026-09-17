"""Liquidity output models and configuration (spec STEP 2, liquidity half)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    DolConfidence,
    LiquidityEventType,
    LiquidityPoolType,
    LiquidityScope,
    LiquiditySide,
    LiquidityState,
    Timeframe,
)


@dataclass(frozen=True)
class MagnetWeights:
    type: dict[str, float]
    cluster_max: float
    cluster_per_member: float
    touch_points: float
    proximity_max: float
    proximity_zero_atr: float
    stack_max: float
    stack_per_pool: float
    stack_tolerance_atr: float
    trend_alignment: float


@dataclass(frozen=True)
class LiquidityConfig:
    touch_tolerance_atr: float
    approach_distance_atr: float
    equal_tolerance_atr: float
    equal_min_bars_apart: int
    sweep_failure_window_bars: int
    break_acceptance_window_bars: int
    run_extension_atr: float
    key_level_days: int
    key_level_weeks: int
    magnet: MagnetWeights
    dol_high_margin: float
    dol_moderate_margin: float
    dol_low_margin: float
    dol_distinct_price_atr: float
    qualifier_lookback_bars: int
    atr_period: int

    @classmethod
    def from_spec(cls) -> LiquidityConfig:
        s = load_spec("liquidity")
        m = s["magnet"]
        return cls(
            touch_tolerance_atr=float(s["touchToleranceAtr"]),
            approach_distance_atr=float(s["approachDistanceAtr"]),
            equal_tolerance_atr=float(s["equal"]["toleranceAtr"]),
            equal_min_bars_apart=int(s["equal"]["minBarsApart"]),
            sweep_failure_window_bars=int(s["sweep"]["failureWindowBars"]),
            break_acceptance_window_bars=int(s["break"]["acceptanceWindowBars"]),
            run_extension_atr=float(s["break"]["runExtensionAtr"]),
            key_level_days=int(s["keyLevels"]["days"]),
            key_level_weeks=int(s["keyLevels"]["weeks"]),
            magnet=MagnetWeights(
                type={k: float(v) for k, v in m["type"].items()},
                cluster_max=float(m["clusterMax"]),
                cluster_per_member=float(m["clusterPerMember"]),
                touch_points=float(m["touchPoints"]),
                proximity_max=float(m["proximityMax"]),
                proximity_zero_atr=float(m["proximityZeroAtr"]),
                stack_max=float(m["stackMax"]),
                stack_per_pool=float(m["stackPerPool"]),
                stack_tolerance_atr=float(m["stackToleranceAtr"]),
                trend_alignment=float(m["trendAlignment"]),
            ),
            dol_high_margin=float(s["dol"]["highMargin"]),
            dol_moderate_margin=float(s["dol"]["moderateMargin"]),
            dol_low_margin=float(s["dol"]["lowMargin"]),
            dol_distinct_price_atr=float(s["dol"]["distinctPriceAtr"]),
            qualifier_lookback_bars=int(s["qualifierLookbackBars"]),
            atr_period=int(load_spec("structure")["atrPeriod"]),
        )


class KeyLevel(ApiModel):
    """A completed-period extreme (PDH/PDL/PWH/PWL). Unknown before `known_at`."""

    type: LiquidityPoolType
    price: float
    period_start: datetime
    known_at: datetime
    label: str


class LiquidityPool(ApiModel):
    id: str
    type: LiquidityPoolType
    side: LiquiditySide
    scope: LiquidityScope
    label: str
    price: float
    formed_at: datetime  # pivot candle open time / period start
    known_at: datetime  # from this instant the pool exists
    source_times: list[datetime]  # member pivot times (EQ clusters: all members)
    state: LiquidityState
    touches: int
    state_changed_at: datetime | None
    taken: bool
    distance_atr: float | None  # |price - last close| / ATR at the last candle
    magnet_score: float | None  # only for untaken pools


class LiquidityEvent(ApiModel):
    id: str
    pool_id: str
    pool_type: LiquidityPoolType
    side: LiquiditySide
    type: LiquidityEventType
    price: float  # pool price at the time of the event
    time: datetime  # open time of the candle that produced the event
    extreme: float  # candle high (BSL) or low (SSL)
    close: float


class DolTarget(ApiModel):
    pool_id: str
    type: LiquidityPoolType
    side: LiquiditySide
    label: str
    price: float
    magnet_score: float
    distance_atr: float


class DolSelection(ApiModel):
    primary: DolTarget | None
    secondary: DolTarget | None
    confidence: DolConfidence
    margin: float | None
    reason: str


class LiquidityAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    key_levels_available: bool
    pools: list[LiquidityPool]
    events: list[LiquidityEvent]
    dol: DolSelection | None
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
