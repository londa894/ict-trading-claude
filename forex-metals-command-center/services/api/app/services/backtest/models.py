"""Backtesting V1 models (Phase 18). Research only: never an authorization or a performance promise."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, is_utc
from app.domain.enums import (
    BacktestStatus,
    BacktestTradeStatus,
    Direction,
    EntryMode,
    ExitReason,
    SampleSizeLabel,
    TradeResult,
)
from app.services.analytics.models import Breakdown, Drawdown, EquityPoint, GroupStats


@dataclass(frozen=True)
class BacktestConfig:
    max_range_days: int
    max_variants: int
    max_segments: int
    max_concurrent_positions: int
    monte_carlo_resamples: int
    monte_carlo_seed: int
    progress_every_steps: int
    max_cost_multiplier: float

    @classmethod
    def from_spec(cls) -> BacktestConfig:
        s = load_spec("backtest")
        cfg = cls(
            max_range_days=int(s["maxRangeDays"]),
            max_variants=int(s["maxVariants"]),
            max_segments=int(s["maxSegments"]),
            max_concurrent_positions=int(s["maxConcurrentPositions"]),
            monte_carlo_resamples=int(s["monteCarlo"]["resamples"]),
            monte_carlo_seed=int(s["monteCarlo"]["seed"]),
            progress_every_steps=int(s["progressEverySteps"]),
            max_cost_multiplier=float(s["maxCostMultiplier"]),
        )
        if cfg.max_concurrent_positions != 1:
            raise ValueError("backtesting V1 simulates one position at a time")
        if (
            cfg.max_range_days < 1
            or cfg.max_variants < 1
            or cfg.max_segments < 1
            or cfg.monte_carlo_resamples < 0
        ):
            raise ValueError("backtest limits invalid")
        return cfg


CFG_LIMITS = BacktestConfig.from_spec()


def _utc(value: datetime, name: str) -> datetime:
    if not is_utc(value):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


class BacktestVariant(ApiModel):
    name: str = Field(default="A", pattern=r"^[A-Za-z0-9_-]{1,16}$")
    entry_mode: EntryMode = EntryMode.STANDARD
    cost_multiplier: float = Field(default=1.0, ge=0, le=CFG_LIMITS.max_cost_multiplier)


class BacktestRequest(ApiModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    start: datetime
    end: datetime
    variants: list[BacktestVariant] = Field(
        default_factory=lambda: [BacktestVariant()], min_length=1, max_length=CFG_LIMITS.max_variants
    )
    segments: int = Field(default=1, ge=1, le=CFG_LIMITS.max_segments)
    out_of_sample_from: datetime | None = None

    @field_validator("start", "end", "out_of_sample_from")
    @classmethod
    def _times_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value, "time") if value is not None else None

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.end - self.start > timedelta(days=CFG_LIMITS.max_range_days):
            raise ValueError(f"range longer than {CFG_LIMITS.max_range_days} days")
        if self.out_of_sample_from is not None and not self.start < self.out_of_sample_from < self.end:
            raise ValueError("outOfSampleFrom must lie inside the range")
        if len({v.name for v in self.variants}) != len(self.variants):
            raise ValueError("variant names must be unique")
        return self


class BacktestTrade(ApiModel):
    variant: str
    status: BacktestTradeStatus
    setup_id: str
    setup_type: str
    model: str
    direction: Direction
    confirmed_at: datetime  # close of the setup-timeframe candle that confirmed the plan
    limit_price: float
    stop: float
    target: float
    fill_price: float | None
    filled_at: datetime | None
    exit_price: float | None
    exited_at: datetime | None
    exit_reason: ExitReason | None
    result: TradeResult | None
    r_multiple: float | None  # after assumed spread and slippage
    net_r_multiple: float | None  # after assumed commission too
    mfe_r: float | None
    mae_r: float | None
    ambiguous: bool
    detail: str


class Funnel(ApiModel):
    steps: int
    ineligible_steps: int
    ineligible_reasons: dict[str, int] = Field(
        default_factory=dict
    )  # over the ineligible steps (added in 0.18)
    setups_discovered: int
    states_reached: dict[str, int]
    plans_confirmed: int
    plans_skipped_overlap: int
    fills: int
    expired: int
    closed: int
    open_at_end: int


class SegmentStats(ApiModel):
    name: str
    start: datetime
    end: datetime
    stats: GroupStats


class MonteCarlo(ApiModel):
    resamples: int
    seed: int
    trades: int
    label: SampleSizeLabel
    total_r_p05: float
    total_r_p50: float
    total_r_p95: float
    max_drawdown_r_p50: float
    max_drawdown_r_p95: float
    detail: str


class VariantResult(ApiModel):
    variant: BacktestVariant
    funnel: Funnel
    stats: GroupStats
    drawdown: Drawdown
    equity_curve: list[EquityPoint]
    breakdowns: list[Breakdown]
    segments: list[SegmentStats]
    in_sample: GroupStats | None
    out_of_sample: GroupStats | None
    monte_carlo: MonteCarlo | None
    ambiguous_trades: int
    trades: list[BacktestTrade]


class BacktestData(ApiModel):
    provider: str
    is_synthetic: bool
    execution_bars: int
    step_bars: int
    first_bar: datetime | None
    last_bar: datetime | None


class BacktestResult(ApiModel):
    variants: list[VariantResult]
    data: BacktestData
    disclosures: list[str]
    config_hash: str
    completed_at: datetime


class BacktestProgress(ApiModel):
    variant: str | None
    steps_done: int
    steps_total: int
    pct: float


class BacktestRun(ApiModel):
    id: str
    status: BacktestStatus
    request: BacktestRequest
    progress: BacktestProgress
    error: str | None
    result: BacktestResult | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    integrity: str  # VERIFIED / TAMPERED / UNREADABLE (older format) / NOT_APPLICABLE (no result yet)
    authority: str  # always RESEARCH_ONLY
    strategy_version: str


class BacktestRunRow(ApiModel):
    id: str
    symbol: str
    start: datetime
    end: datetime
    status: BacktestStatus
    pct: float
    variants: list[str]
    closed_trades: dict[str, int]
    net_total_r: dict[str, float | None]
    created_at: datetime
    strategy_version: str


class BacktestStoreInfo(ApiModel):
    available: bool
    backend: str
    reason: str | None
    running_id: str | None


class BacktestListResponse(ApiModel):
    runs: list[BacktestRunRow]
    store: BacktestStoreInfo
    generated_at: datetime
