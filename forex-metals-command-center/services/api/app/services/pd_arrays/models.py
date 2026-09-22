"""Displacement + FVG/IFVG models and configuration (spec STEP 2 displacement, STEP 3 FVG/IFVG)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    Direction,
    DisplacementGrade,
    IfvgStatus,
    PdArrayEventType,
    PdArrayState,
    PdArrayType,
    Timeframe,
)


@dataclass(frozen=True)
class PdArrayConfig:
    atr_period: int
    min_body_pct: float
    max_leg_candles: int
    grades_atr: dict[DisplacementGrade, float]
    strong_min_avg_body_pct: float
    qualifier_min_grade: DisplacementGrade
    qualifier_lookback_bars: int
    imr_min_grade: DisplacementGrade  # min displacement grade for an IMR (the "expansion only" gate)
    fvg_min_size_atr: float
    fvg_touch_tolerance_atr: float
    fvg_touch_max_fill_pct: float
    fvg_half_fill_pct: float
    ifvg_confirm_window_bars: int
    ifvg_acceptance_closes: int
    ifvg_acceptance_extension_atr: float
    ifvg_displacement_min_grade: DisplacementGrade
    quality_size_max: float
    quality_size_full_atr: float
    quality_displacement: dict[str, float]
    quality_freshness: dict[str, float]
    quality_trend_alignment: float

    @classmethod
    def from_spec(cls) -> PdArrayConfig:
        s = load_spec("pd_arrays")
        d, f, i, q = s["displacement"], s["fvg"], s["ifvg"], s["quality"]
        imr = s.get("imr", {"minGrade": d["qualifierMinGrade"]})
        if f["invalidationRule"] != "CLOSE_THROUGH":
            raise ValueError("only CLOSE_THROUGH invalidation is implemented")
        return cls(
            atr_period=int(load_spec("structure")["atrPeriod"]),
            min_body_pct=float(d["minBodyPct"]),
            max_leg_candles=int(d["maxLegCandles"]),
            grades_atr={DisplacementGrade(k): float(v) for k, v in d["gradesAtr"].items()},
            strong_min_avg_body_pct=float(d["strongMinAvgBodyPct"]),
            qualifier_min_grade=DisplacementGrade(d["qualifierMinGrade"]),
            qualifier_lookback_bars=int(d["qualifierLookbackBars"]),
            imr_min_grade=DisplacementGrade(imr["minGrade"]),
            fvg_min_size_atr=float(f["minSizeAtr"]),
            fvg_touch_tolerance_atr=float(f["touchToleranceAtr"]),
            fvg_touch_max_fill_pct=float(f["touchMaxFillPct"]),
            fvg_half_fill_pct=float(f["halfFillPct"]),
            ifvg_confirm_window_bars=int(i["confirmWindowBars"]),
            ifvg_acceptance_closes=int(i["acceptanceCloses"]),
            ifvg_acceptance_extension_atr=float(i["acceptanceExtensionAtr"]),
            ifvg_displacement_min_grade=DisplacementGrade(i["displacementMinGrade"]),
            quality_size_max=float(q["sizeMax"]),
            quality_size_full_atr=float(q["sizeFullAtr"]),
            quality_displacement={k: float(v) for k, v in q["displacement"].items()},
            quality_freshness={k: float(v) for k, v in q["freshness"].items()},
            quality_trend_alignment=float(q["trendAlignment"]),
        )


class DisplacementEvent(ApiModel):
    id: str
    direction: Direction
    grade: DisplacementGrade
    magnitude_atr: float
    leg_start: datetime  # open time of the first leg candle
    time: datetime  # open time of the candle at which this grade was reached
    candle_count: int
    avg_body_pct: float


class PdArrayZone(ApiModel):
    """FVG or IFVG zone. `midpoint` is the consequent encroachment (CE)."""

    id: str
    type: PdArrayType
    direction: Direction  # BULLISH zones support price from below; BEARISH zones resist from above
    top: float
    bottom: float
    midpoint: float
    size_atr: float
    source_times: list[datetime]  # FVG: the 3 candles; IFVG: the parent FVG's candles
    created_at: datetime  # open time of the candle that created the zone
    known_at: datetime
    state: PdArrayState
    fill_pct: float
    ifvg_status: IfvgStatus | None
    parent_id: str | None
    displacement_grade: DisplacementGrade | None
    state_changed_at: datetime | None
    invalidated_at: datetime | None
    age_bars: int
    active: bool  # usable as a PD array right now (valid, not failed, not full-filled)
    quality_score: float | None  # only for active zones


class PdArrayEvent(ApiModel):
    id: str
    zone_id: str
    zone_type: PdArrayType
    direction: Direction
    type: PdArrayEventType
    time: datetime
    price: float  # the relevant candle extreme or close
    detail: str


class PdArrayAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    displacements: list[DisplacementEvent]
    zones: list[PdArrayZone]
    events: list[PdArrayEvent]
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
