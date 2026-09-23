"""Reversal setup (REVERSAL_NO_WICK_IFVG) models and configuration (reversal spec section 6).

A counter-bias setup that complements the bias-following continuation model: an HTF no-wick (or IMR)
imbalance is the origin; price rebalances into it, reacts (rejection wick or opposite displacement),
a confirmation flips in the reversal direction, and the setup arms. Direction is the origin candle's
body direction (the counter-bias push whose continuation the setup trades after the rebalance).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    DataQuality,
    Direction,
    DisplacementGrade,
    HtfBias,
    ReversalConfirmation,
    ReversalOriginKind,
    SetupState,
    SetupType,
    Timeframe,
)


@dataclass(frozen=True)
class ReversalConfig:
    origin_timeframes: tuple[Timeframe, ...]
    origin_weights: dict[str, float]
    origin_min_body_pct: float
    origin_max_wick_pct: float
    always_allowed_origin_timeframes: frozenset[Timeframe]  # bias gate: D1 origin is always allowed
    allowed_biases: frozenset[HtfBias]  # bias gate: otherwise only these (transitioning/unclear)
    rebalance_window_bars: int  # bars after the origin is known to reach a rebalance touch
    reaction_window_bars: int  # bars after the rebalance touch to see a reaction
    reaction_wick_pct: float  # min rejection-wick fraction of the reaction candle's range
    reaction_displacement_min_grade: DisplacementGrade  # opposite displacement close alternative
    flip_window_bars: int  # bars after the reaction to see a confirmation (IFVG_FLIP / NEW_FVG / IMR)
    entry_window_bars: int  # bars after ARM to retest the entry zone (else EXPIRED)
    stop_buffer_atr: float
    enabled: bool  # A/B master switch: when false the reversal setup never reaches the live verdict

    @classmethod
    def from_spec(cls) -> ReversalConfig:
        s = load_spec("reversal")
        o, rb, rx = s["origin"], s["rebalance"], s["reaction"]
        return cls(
            origin_timeframes=tuple(Timeframe(x) for x in o["timeframes"]),
            origin_weights={k: float(v) for k, v in o["weights"].items()},
            origin_min_body_pct=float(o["minBodyPct"]),
            origin_max_wick_pct=float(o["maxWickPct"]),
            always_allowed_origin_timeframes=frozenset(
                Timeframe(x) for x in s["biasGate"]["alwaysAllowedOriginTimeframes"]
            ),
            allowed_biases=frozenset(HtfBias(x) for x in s["biasGate"]["allowedBiases"]),
            rebalance_window_bars=int(rb["windowBars"]),
            reaction_window_bars=int(rx["windowBars"]),
            reaction_wick_pct=float(rx["wickPct"]),
            reaction_displacement_min_grade=DisplacementGrade(rx["displacementMinGrade"]),
            flip_window_bars=int(s["confirmation"]["flipWindowBars"]),
            entry_window_bars=int(s["entry"]["windowBars"]),
            stop_buffer_atr=float(s["stopBufferAtr"]),
            enabled=bool(s.get("enabled", False)),
        )


class OriginZone(ApiModel):
    """An HTF no-wick / IMR imbalance the reversal rebalances into.

    `direction` is the reversal direction (the origin candle's body direction). The rebalance zone is
    the origin candle body [body_bottom, body_top]; `far_edge` is the origin extreme beyond which a
    close invalidates the setup (origin high for a bearish reversal, origin low for a bullish one)."""

    id: str
    kind: ReversalOriginKind
    timeframe: Timeframe
    direction: Direction
    body_top: float
    body_bottom: float
    far_edge: float
    weight: float
    known_at: datetime


class ReversalEvent(ApiModel):
    id: str
    setup_id: str
    direction: Direction
    state: SetupState
    time: datetime  # open time of the candle whose close produced the transition
    price: float
    detail: str


class ReversalSetup(ApiModel):
    id: str
    setup_type: SetupType  # always REVERSAL_NO_WICK_IFVG
    direction: Direction
    state: SetupState
    terminal: bool
    trading_day: date
    origin: OriginZone
    discovered_at: datetime
    state_changed_at: datetime
    rebalanced_at: datetime | None
    reaction_at: datetime | None
    armed_at: datetime | None
    confirmations: list[ReversalConfirmation]  # which confirmation(s) fired (for scoring/journaling)
    confirming_zone_ids: list[str]  # the PD-array zones that armed the setup (entry structures)
    protective_level: float | None  # the origin far edge (structural invalidation)
    # Plan (built at ARM; never an authorization — the verdict path still gates it):
    entry_price: float | None  # limit at the confirming structure's midpoint (or IMR structural level)
    entry_zone_id: str | None
    stop_price: float | None  # beyond the origin far edge + stopBufferAtr x ATR
    target_price: float | None  # nearest opposite-side draw (reversal-exempt DOL, section 5 tiering)
    target_pool_id: str | None
    target_label: str | None
    rr: float | None  # reward:risk of the plan
    reason: str | None  # why INVALIDATED / EXPIRED


class ReversalAnalysis(ApiModel):
    symbol: str
    timeframe: Timeframe  # the decision timeframe the reversal is tracked on
    as_of: datetime | None
    candle_count: int
    quality: DataQuality
    is_synthetic: bool
    enabled: bool  # the A/B flag: false = the reversal never reaches the live verdict
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    bias: HtfBias  # HTF structure bias used by the bias gate
    origin_count: int
    current: ReversalSetup | None  # the furthest-advanced non-terminal setup, if any
    setups: list[ReversalSetup]
    events: list[ReversalEvent]
    provider_error: str | None
    strategy_version: str
    generated_at: datetime
