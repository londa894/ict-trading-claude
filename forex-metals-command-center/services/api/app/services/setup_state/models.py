"""Core setup state machine models and configuration (spec STEP 5 lifecycle, Phase 7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    Direction,
    LiquidityEventType,
    LiquidityPoolType,
    Po3Phase,
    QualifierStatus,
    SetupState,
    SetupStep,
    SetupStepStatus,
    SetupType,
    StructureEventType,
    StructureLevel,
    Timeframe,
)
from app.services.entry.models import EntryPlan


@dataclass(frozen=True)
class SetupConfig:
    setup_timeframe: Timeframe
    setup_bars: int
    bias_timeframes: tuple[Timeframe, ...]
    bias_bars: int
    forming_approach_atr: float
    sweep_event_types: frozenset[LiquidityEventType]
    mss_break_types: frozenset[StructureEventType]
    mss_require_displacement: bool
    mss_window_bars: int
    zone_grace_bars: int
    retracement_approach_atr: float
    retracement_window_bars: int
    expire_at_trading_day_end: bool
    po3_manipulation_adr_pct: float
    po3_distribution_adr_pct: float
    atr_period: int

    @classmethod
    def from_spec(cls) -> SetupConfig:
        s = load_spec("setup")
        mss, ret = s["mss"], s["retracement"]
        breaks = frozenset(StructureEventType(x) for x in mss["breakTypes"])
        if StructureEventType.BOS in breaks:
            raise ValueError("a setup confirmation break must be a reversal (MSS/CHOCH), not a BOS")
        return cls(
            setup_timeframe=Timeframe(s["setupTimeframe"]),
            setup_bars=int(s["setupBars"]),
            bias_timeframes=tuple(Timeframe(x) for x in s["bias"]["timeframes"]),
            bias_bars=int(s["bias"]["bars"]),
            forming_approach_atr=float(s["formingApproachAtr"]),
            sweep_event_types=frozenset(LiquidityEventType(x) for x in s["sweepEventTypes"]),
            mss_break_types=breaks,
            mss_require_displacement=bool(mss["requireDisplacement"]),
            mss_window_bars=int(mss["windowBars"]),
            zone_grace_bars=int(ret["zoneGraceBars"]),
            retracement_approach_atr=float(ret["approachAtr"]),
            retracement_window_bars=int(ret["windowBars"]),
            expire_at_trading_day_end=bool(s["expireAtTradingDayEnd"]),
            po3_manipulation_adr_pct=float(s["po3"]["manipulationAdrPct"]),
            po3_distribution_adr_pct=float(s["po3"]["distributionAdrPct"]),
            atr_period=int(load_spec("structure")["atrPeriod"]),
        )


class BiasPoint(ApiModel):
    """A confirmed external BOS/MSS on a bias timeframe, known at the close of its breaking candle."""

    timeframe: Timeframe
    direction: Direction
    known_at: datetime
    event_id: str


class TargetRef(ApiModel):
    pool_id: str
    pool_type: LiquidityPoolType
    label: str
    price: float


class LiquidityRef(ApiModel):
    pool_id: str
    pool_type: LiquidityPoolType
    event_type: LiquidityEventType
    time: datetime
    extreme: float


class BreakRef(ApiModel):
    event_id: str
    type: StructureEventType
    level: StructureLevel
    time: datetime
    price: float
    displacement_qualifier: QualifierStatus


class SetupStepState(ApiModel):
    step: SetupStep
    status: SetupStepStatus
    detail: str


class Setup(ApiModel):
    id: str
    setup_type: SetupType
    direction: Direction
    state: SetupState
    terminal: bool
    trading_day: date
    discovered_at: datetime
    state_changed_at: datetime
    target: TargetRef | None
    liquidity_event: LiquidityRef | None
    mss: BreakRef | None
    protective_level: float | None
    zone_ids: list[str]
    touched_zone_id: str | None
    reason: str | None  # why INVALIDATED / EXPIRED
    next_required_event: str | None
    steps: list[SetupStepState]
    entry_plan: EntryPlan | None  # Phase 8: confirmed plan (never an authorization)


class SetupEvent(ApiModel):
    id: str
    setup_id: str
    direction: Direction
    state: SetupState
    time: datetime  # open time of the candle whose close produced the transition
    price: float
    detail: str


class BiasState(ApiModel):
    direction: Direction | None
    timeframes: list[Timeframe]
    latest: list[BiasPoint]  # latest confirmed point per timeframe, as of the analysis


class Po3State(ApiModel):
    trading_day: date | None
    phase: Po3Phase
    daily_open: float | None
    adr: float | None
    detail: str


class SetupAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    bias: BiasState
    current_state: (
        SetupState | None
    )  # the open setup's state; BLOCKED when data is ineligible; None = no setup
    current: Setup | None
    setups: list[Setup]
    events: list[SetupEvent]
    po3: Po3State
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
