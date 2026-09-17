"""Risk management & position sizing models (spec STEP 6, Phase 9). Broker-free; nothing places a trade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, require_utc
from app.domain.enums import (
    Blocker,
    Direction,
    PositionSizeStatus,
    RiskLock,
    RiskProfileName,
    RiskStatus,
    RiskWarning,
    Timeframe,
)
from app.domain.instrument import InstrumentSpec, get_instrument

LIMIT_FIELDS = (
    "risk_per_trade_pct",
    "daily_risk_limit_pct",
    "weekly_risk_limit_pct",
    "max_open_risk_pct",
    "max_trades_per_day",
    "max_positions",
    "max_consecutive_losses",
)


class RiskLimits(ApiModel):
    risk_per_trade_pct: float = Field(gt=0)
    daily_risk_limit_pct: float = Field(gt=0)
    weekly_risk_limit_pct: float = Field(gt=0)
    max_open_risk_pct: float = Field(gt=0)
    max_trades_per_day: int = Field(ge=1)
    max_positions: int = Field(ge=1)
    max_consecutive_losses: int = Field(ge=1)

    def exceeds(self, cap: RiskLimits) -> list[str]:
        return [f for f in LIMIT_FIELDS if getattr(self, f) > getattr(cap, f)]


@dataclass(frozen=True)
class RiskConfig:
    profiles: dict[RiskProfileName, RiskLimits]
    hard_limits: RiskLimits
    volatility_timeframe: Timeframe
    atr_period: int
    baseline_bars: int
    volatility_lock_ratio: float
    max_spread_to_stop_pct: float
    spec_tolerance_pct: float
    conversion_max_age_hours: float

    @classmethod
    def from_spec(cls) -> RiskConfig:
        s = load_spec("risk")
        hard = RiskLimits.model_validate(s["hardLimits"])
        profiles = {RiskProfileName(k): RiskLimits.model_validate(v) for k, v in s["profiles"].items()}
        if RiskProfileName.CUSTOM in profiles or set(profiles) != set(RiskProfileName) - {
            RiskProfileName.CUSTOM
        }:
            raise ValueError("risk presets must be exactly CONSERVATIVE, STANDARD and AGGRESSIVE")
        for name, limits in profiles.items():
            if limits.exceeds(hard):
                raise ValueError(f"risk preset {name} exceeds the hard limits: {limits.exceeds(hard)}")
        v = s["volatility"]
        return cls(
            profiles=profiles,
            hard_limits=hard,
            volatility_timeframe=Timeframe(v["timeframe"]),
            atr_period=int(v["atrPeriod"]),
            baseline_bars=int(v["baselineBars"]),
            volatility_lock_ratio=float(v["lockRatio"]),
            max_spread_to_stop_pct=float(s["maxSpreadToStopPct"]),
            spec_tolerance_pct=float(s["specConsistencyTolerancePct"]),
            conversion_max_age_hours=float(s["conversionMaxAgeHours"]),
        )


# --- manual account profile & state (server-side file, never committed) ------------------------------------


class PropRules(ApiModel):
    starting_balance: float = Field(gt=0)
    max_daily_drawdown_pct: float = Field(gt=0, le=100)
    max_total_drawdown_pct: float = Field(gt=0, le=100)


class AccountProfile(ApiModel):
    """Spec STEP 6 AccountProfile. Presets take their limits from risk.json; CUSTOM must set all of them."""

    balance: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    profile: RiskProfileName
    risk_per_trade_pct: float | None = Field(default=None, gt=0)
    daily_risk_limit_pct: float | None = Field(default=None, gt=0)
    weekly_risk_limit_pct: float | None = Field(default=None, gt=0)
    max_open_risk_pct: float | None = Field(default=None, gt=0)
    max_trades_per_day: int | None = Field(default=None, ge=1)
    max_positions: int | None = Field(default=None, ge=1)
    max_consecutive_losses: int | None = Field(default=None, ge=1)
    leverage: float | None = Field(default=None, gt=0)
    prop_rules: PropRules | None = None

    @model_validator(mode="after")
    def _limits_match_profile(self) -> Self:
        given = [f for f in LIMIT_FIELDS if getattr(self, f) is not None]
        if self.profile is RiskProfileName.CUSTOM and len(given) != len(LIMIT_FIELDS):
            raise ValueError("a CUSTOM profile must set every limit")
        if self.profile is not RiskProfileName.CUSTOM and given:
            raise ValueError(
                "preset profiles take their limits from the strategy spec; use CUSTOM to set limits"
            )
        return self


class OpenPosition(ApiModel):
    symbol: str
    direction: Direction
    risk_amount: float = Field(ge=0)  # remaining risk to stop, account currency
    in_loss: bool

    @field_validator("symbol")
    @classmethod
    def _known(cls, value: str) -> str:
        if get_instrument(value) is None:
            raise ValueError("unknown symbol")
        return value.upper()


class AccountState(ApiModel):
    """Manual state for one trading day (New York 17:00 roll). A state from another trading day is stale."""

    trading_day: date
    realized_pnl_today: float
    realized_pnl_week: float
    trades_today: int = Field(ge=0)
    consecutive_losses: int = Field(ge=0)
    open_positions: list[OpenPosition] = Field(default_factory=list)


class ConversionRate(ApiModel):
    """1 `base` = `rate` `quote`."""

    base: str = Field(pattern=r"^[A-Z]{3}$")
    quote: str = Field(pattern=r"^[A-Z]{3}$")
    rate: float = Field(gt=0)
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "as_of")


class RiskProfileFile(ApiModel):
    account: AccountProfile
    state: AccountState
    instrument_specs: dict[str, InstrumentSpec] = Field(default_factory=dict)
    conversion_rates: list[ConversionRate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _specs_keyed_by_symbol(self) -> Self:
        for key, spec in self.instrument_specs.items():
            if key.upper() != spec.symbol.upper() or get_instrument(key) is None:
                raise ValueError("instrument specs must be keyed by their own known symbol")
        return self


@dataclass(frozen=True)
class TradeInput:
    direction: Direction
    entry: float
    stop: float


# --- outputs -------------------------------------------------------------------------------------------


class RiskBudget(ApiModel):
    """Account-currency amounts. The effective budget is the smallest cap; losses only ever shrink it."""

    risk_per_trade_amount: float
    daily_remaining: float
    weekly_remaining: float
    open_risk: float
    open_risk_remaining: float
    prop_remaining: float | None
    effective_risk_amount: float


class RiskLockItem(ApiModel):
    lock: RiskLock
    detail: str


class VolatilityState(ApiModel):
    timeframe: Timeframe
    atr: float
    baseline_atr: float
    ratio: float
    lock_ratio: float


class PositionSize(ApiModel):
    direction: Direction
    entry: float
    stop: float
    price_distance: float
    points: float | None  # price distance / tick size
    pips: float | None  # price distance / platform pip size, when configured
    spread: float | None
    sizing_distance: float  # price distance + known spread
    risk_per_volume: float | None  # account currency lost at the stop (incl. spread) per 1.0 volume
    volume: float | None
    min_volume: float | None
    volume_step: float | None
    risk_amount: float | None  # actual, incl. spread
    risk_amount_without_spread: float | None
    risk_pct: float | None
    margin_required: float | None
    detail: str


class RiskAssessment(ApiModel):
    """Deterministic risk assessment. Risk has final veto; a clean assessment never authorizes a trade."""

    symbol: str
    status: RiskStatus
    profile: RiskProfileName | None
    currency: str | None
    profile_error: str | None
    limits: RiskLimits | None
    budget: RiskBudget | None
    locks: list[RiskLockItem]
    warnings: list[RiskWarning]
    volatility: VolatilityState | None
    position: PositionSize | None
    size_status: PositionSizeStatus | None
    blockers: list[Blocker]
    news: str
    authority: str
    strategy_version: str
    generated_at: datetime


class RiskCalculationRequest(ApiModel):
    """What-if calculation. Stored nowhere; the state is optional (account locks are then not checked)."""

    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    direction: Direction
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    account: AccountProfile
    state: AccountState | None = None
    instrument_spec: InstrumentSpec | None = None
    conversion_rate: float | None = Field(default=None, gt=0)  # 1 quote currency = rate account currency
