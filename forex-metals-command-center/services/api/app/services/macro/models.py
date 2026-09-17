"""Basic macro models (spec STEP 7, Phase 14). Macro modifies score/confidence/narrative; it never blocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, require_utc
from app.domain.enums import (
    CorrelationRegime,
    Direction,
    EventImportance,
    MacroBias,
    MacroSeriesId,
    MacroState,
    SeriesDirection,
)


@dataclass(frozen=True)
class DriverConfig:
    series: MacroSeriesId
    relationship: int
    weight: float
    fallback: MacroSeriesId | None


@dataclass(frozen=True)
class MacroConfig:
    lookback: int
    volatility_window: int
    min_observations: int
    flat_z: float
    max_age_days: int
    required: tuple[MacroSeriesId, ...]
    strongly_supportive: float
    supportive: float
    correlation_series: MacroSeriesId
    correlation_observations: int
    correlation_min_abs: float
    inverted_weight_factor: float
    surprise_weight: float
    surprise_lookback_hours: float
    surprise_min_importance: EventImportance
    usd_positive: tuple[str, ...]
    usd_negative: tuple[str, ...]
    drivers: dict[str, tuple[DriverConfig, ...]]

    @classmethod
    def from_spec(cls) -> MacroConfig:
        s = load_spec("macro")
        drivers: dict[str, tuple[DriverConfig, ...]] = {}
        for symbol, items in s["drivers"].items():
            parsed = tuple(
                DriverConfig(
                    MacroSeriesId(d["series"]),
                    int(d["relationship"]),
                    float(d["weight"]),
                    MacroSeriesId(d["fallback"]) if d.get("fallback") else None,
                )
                for d in items
            )
            if any(d.relationship not in (-1, 1) or d.weight <= 0 for d in parsed):
                raise ValueError(f"{symbol}: relationship must be +1/-1 and weight positive")
            drivers[symbol] = parsed
        t, c, sp = s["thresholds"], s["correlation"], s["surprise"]
        if not 0 < float(t["supportive"]) < float(t["stronglySupportive"]) <= 1:
            raise ValueError("macro thresholds must satisfy 0 < supportive < stronglySupportive <= 1")
        news_window = float(load_spec("news")["listBeforeHours"])
        if float(sp["lookbackHours"]) > news_window:
            raise ValueError("surprise.lookbackHours cannot exceed news listBeforeHours")
        if s["lookbackObservations"] >= s["minObservations"]:
            raise ValueError("minObservations must exceed lookbackObservations")
        return cls(
            lookback=int(s["lookbackObservations"]),
            volatility_window=int(s["volatilityObservations"]),
            min_observations=int(s["minObservations"]),
            flat_z=float(s["flatZ"]),
            max_age_days=int(s["maxAgeDays"]),
            required=tuple(MacroSeriesId(x) for x in s["requiredSeries"]),
            strongly_supportive=float(t["stronglySupportive"]),
            supportive=float(t["supportive"]),
            correlation_series=MacroSeriesId(c["series"]),
            correlation_observations=int(c["observations"]),
            correlation_min_abs=float(c["minAbs"]),
            inverted_weight_factor=float(c["invertedWeightFactor"]),
            surprise_weight=float(sp["weight"]),
            surprise_lookback_hours=float(sp["lookbackHours"]),
            surprise_min_importance=EventImportance(sp["minImportance"]),
            usd_positive=tuple(str(x).lower() for x in sp["usdPositiveKeywords"]),
            usd_negative=tuple(str(x).lower() for x in sp["usdNegativeKeywords"]),
            drivers=drivers,
        )


class MacroObservation(ApiModel):
    date: date
    value: float


class MacroSeries(ApiModel):
    id: MacroSeriesId
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=20)
    observations: list[MacroObservation]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        dates = [o.date for o in self.observations]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("observations must be in increasing date order without duplicates")
        return self


class MacroFile(ApiModel):
    source: str = Field(min_length=1, max_length=200)
    fetched_at: datetime
    series: list[MacroSeries]

    @field_validator("fetched_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "fetched_at")

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [s.id for s in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("each series id may appear once")
        return self


@dataclass(frozen=True)
class MacroSnapshot:
    provider: str
    source: str
    is_synthetic: bool
    fetched_at: datetime
    series: dict[MacroSeriesId, MacroSeries]


class SeriesTrend(ApiModel):
    id: MacroSeriesId
    last_date: date
    last_value: float
    change: float  # over the lookback
    z_score: float | None  # change / (daily-change stdev x sqrt(lookback))
    direction: SeriesDirection
    stale: bool


class DriverContribution(ApiModel):
    series: str  # the series actually used (fallback when the primary is missing), or USD_DATA_SURPRISE
    configured: str
    relationship: int
    weight: float  # after any correlation adjustment
    direction: SeriesDirection | None
    contribution: float | None  # signed share of the bullish score; None = not evaluated
    detail: str


class CorrelationInfo(ApiModel):
    series: MacroSeriesId
    observations: int
    coefficient: float | None
    regime: CorrelationRegime
    detail: str


class MacroAssessment(ApiModel):
    """Deterministic macro context for one market. Bias is for the market rising; state is vs the setup."""

    symbol: str
    bias: MacroBias
    score: float | None  # -1 .. +1, positive supports the market rising
    state: MacroState | None  # versus `direction`; None when there is no open setup direction
    direction: Direction | None
    drivers: list[DriverContribution]
    series: list[SeriesTrend]
    correlation: CorrelationInfo | None
    provider: str
    source: str | None
    is_synthetic: bool
    available: bool
    fetched_at: datetime | None
    reason: str | None
    warnings: list[str]
    thresholds: dict[str, float]
    strategy_version: str
    generated_at: datetime


class MacroSeriesResponse(ApiModel):
    """Raw macro series as the provider supplied them (for inspection; the engine reads the same snapshot)."""

    provider: str
    is_synthetic: bool
    available: bool
    reason: str | None
    source: str | None
    fetched_at: datetime | None
    series: list[MacroSeries]
    generated_at: datetime
