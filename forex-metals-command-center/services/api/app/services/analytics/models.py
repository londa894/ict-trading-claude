"""Analytics V1 models (Phase 17). Recorded history only: never a probability, forecast or trade signal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import AnalyticsSource, Direction, ProcessClassification, RuleViolation, SampleSizeLabel


@dataclass(frozen=True)
class AnalyticsConfig:
    limited_from: int
    moderate_from: int
    stronger_from: int
    best_group_min_label: SampleSizeLabel
    equity_curve_max_points: int
    disclaimer: str

    @classmethod
    def from_spec(cls) -> AnalyticsConfig:
        s = load_spec("analytics")
        t = s["sampleSizeLabels"]
        cfg = cls(
            limited_from=int(t["LIMITED"]),
            moderate_from=int(t["MODERATE"]),
            stronger_from=int(t["STRONGER_EVIDENCE"]),
            best_group_min_label=SampleSizeLabel(s["bestGroupMinLabel"]),
            equity_curve_max_points=int(s["equityCurveMaxPoints"]),
            disclaimer=str(s["disclaimer"]),
        )
        if not 1 <= cfg.limited_from < cfg.moderate_from < cfg.stronger_from:
            raise ValueError("sample-size thresholds must increase")
        return cfg


@dataclass(frozen=True)
class Sample:
    """One closed record normalised for analytics (journal trade or paper sim)."""

    id: str
    source: AnalyticsSource
    symbol: str
    direction: Direction
    closed_at: datetime
    r: float | None  # journal: R; paper: net R after assumed costs
    win: bool
    breakeven: bool
    result: str
    classification: ProcessClassification
    violations: tuple[RuleViolation, ...]
    duration_minutes: float
    mfe_r: float | None
    mae_r: float | None
    entry_efficiency: float | None
    exit_efficiency: float | None
    session: str
    setup_type: str
    timeframe: str
    day_of_week: str
    no_wick: str | None
    liquidity_event: str | None
    dol: str | None
    entry_price: float
    mfe_price: float | None
    synthetic: bool
    strategy_version: str
    extra: dict[str, str] = field(default_factory=dict)


class GroupStats(ApiModel):
    key: str
    count: int
    label: SampleSizeLabel
    wins: int
    losses: int
    breakeven: int
    win_rate: float | None
    r_count: int  # samples with a defined R
    avg_r: float | None
    total_r: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    expectancy_r: float | None  # win rate x avg win R + (1 - win rate) x avg non-win R, over samples with R
    profit_factor: float | None  # sum of positive R / |sum of negative R|; null without losing R


class Breakdown(ApiModel):
    dimension: str
    groups: list[GroupStats]
    best: str | None
    best_reason: str


class Drawdown(ApiModel):
    max_drawdown_r: float
    peak_at: datetime | None
    trough_at: datetime | None
    recovered_at: datetime | None
    recovery_trades: int | None  # trades from the trough until the peak was regained


class EquityPoint(ApiModel):
    at: datetime
    cumulative_r: float


class ProcessStats(ApiModel):
    classifications: dict[str, int]
    violation_counts: dict[str, int]
    with_violations: GroupStats
    without_violations: GroupStats


class DolAccuracy(ApiModel):
    evaluated: int
    reached: int
    rate: float | None
    label: SampleSizeLabel
    aligned: GroupStats
    opposed: GroupStats
    unknown: int
    detail: str


class Unavailable(ApiModel):
    available: bool
    reason: str


class AnalyticsFilters(ApiModel):
    source: AnalyticsSource
    symbol: str | None
    start: datetime | None
    end: datetime | None
    include_synthetic: bool
    strategy_version: str | None


class ExcludedCounts(ApiModel):
    tampered: int
    synthetic: int
    not_closed: int
    filtered: int


class AnalyticsReport(ApiModel):
    filters: AnalyticsFilters
    available: bool
    reason: str | None
    overall: GroupStats
    drawdown: Drawdown
    avg_duration_minutes: float | None
    median_duration_minutes: float | None
    avg_mfe_r: float | None
    avg_mae_r: float | None
    avg_entry_efficiency: float | None
    avg_exit_efficiency: float | None
    breakdowns: list[Breakdown]
    process: ProcessStats
    dol: DolAccuracy
    alert_usefulness: Unavailable
    decision_records: dict[str, int]
    excluded: ExcludedCounts
    includes_synthetic: bool
    strategy_versions: list[str]
    equity_curve: list[EquityPoint]
    disclaimer: str
    authority: str  # always DESCRIPTIVE_ONLY
    strategy_version: str
    generated_at: datetime
