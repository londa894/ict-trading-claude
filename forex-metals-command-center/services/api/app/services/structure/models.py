"""Market-structure output models (spec STEP 2, structure half)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    BreakConfirmation,
    DataQuality,
    Direction,
    HtfBias,
    MtfAlignment,
    QualifierStatus,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
    StructureState,
    SwingKind,
    SwingLabel,
    Timeframe,
    TrendDirection,
)


@dataclass(frozen=True)
class StructureConfig:
    pivot_length: dict[StructureLevel, int]
    equal_tolerance_atr: float
    atr_period: int
    ranging_bars_without_break: dict[StructureLevel, int]
    min_candles: int

    @classmethod
    def from_spec(cls) -> StructureConfig:
        s = load_spec("structure")
        return cls(
            pivot_length={StructureLevel(k): int(v) for k, v in s["pivotLength"].items()},
            equal_tolerance_atr=float(s["equalToleranceAtr"]),
            atr_period=int(s["atrPeriod"]),
            ranging_bars_without_break={
                StructureLevel(k): int(v) for k, v in s["rangingBarsWithoutBreak"].items()
            },
            min_candles=int(s["minCandles"]),
        )


class Swing(ApiModel):
    id: str
    level: StructureLevel
    kind: SwingKind
    label: SwingLabel
    price: float
    time: datetime  # open time of the pivot candle
    confirmed_at: datetime  # close time of the candle that confirmed the pivot (known from then on)
    broken_at: datetime | None  # open time of the candle whose close broke it
    broken_by: str | None  # event id


class StructureEvent(ApiModel):
    id: str
    level: StructureLevel
    type: StructureEventType
    direction: Direction
    status: StructureEventStatus
    confirmation: BreakConfirmation
    price: float  # the broken swing level
    time: datetime  # open time of the breaking candle
    broken_swing_id: str
    broken_swing_time: datetime
    trend_before: TrendDirection
    ambiguous: bool
    # Filled by the Liquidity (Phase 3) and Displacement (Phase 4) engines.
    liquidity_qualifier: QualifierStatus
    displacement_qualifier: QualifierStatus


class LevelStructure(ApiModel):
    level: StructureLevel
    pivot_length: int
    state: StructureState
    trend: TrendDirection
    swings: list[Swing]
    events: list[StructureEvent]
    protected_high: Swing | None
    protected_low: Swing | None
    bars_since_last_break: int | None


class StructureAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime | None  # close time of the latest closed candle analysed
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    internal: LevelStructure | None
    external: LevelStructure | None
    events: list[StructureEvent]  # chronological log, both levels
    provider_error: str | None
    strategy_version: str
    generated_at: datetime


class TimeframeStructureState(ApiModel):
    timeframe: Timeframe
    state: StructureState | None
    trend: TrendDirection | None
    quality: DataQuality
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]


class MtfStructureResponse(ApiModel):
    symbol: str
    alignment: MtfAlignment
    htf_bias: HtfBias
    eligible_for_decision: bool
    timeframes: list[TimeframeStructureState]
    strategy_version: str
    generated_at: datetime
