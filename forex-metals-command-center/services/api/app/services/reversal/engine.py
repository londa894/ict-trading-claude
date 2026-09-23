"""REVERSAL_NO_WICK_IFVG state machine — Phase D step 1: discovery -> rebalance -> reaction.

Candle-sequential, no lookahead. One setup per eligible HTF origin zone that passes the bias gate.
Later steps add CONFIRMATION -> ARMED -> ENTRY_ZONE -> BLOCKED and the entry/target plan.

State flow implemented here:
  DISCOVERED         origin known and the bias gate passed
  REBALANCE_WATCH    a later candle has not yet rebalanced the origin
  REBALANCE_TOUCH    price traded back into the origin body (edge touch is sufficient)
  REACTION           rejection off the rebalance (rejection wick, or opposite-direction displacement)
  terminal EXPIRED   no rebalance within rebalanceWindowBars, or no reaction within reactionWindowBars
  terminal INVALIDATED  a close beyond the origin's far edge (the imbalance broke the wrong way)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    DisplacementGrade,
    HtfBias,
    LiquiditySide,
    PdArrayEventType,
    PdArrayType,
    ReversalConfirmation,
    SetupState,
    SetupType,
)
from app.services.liquidity.models import LiquidityPool
from app.services.liquidity.scoring import significance_rank
from app.services.pd_arrays.models import DisplacementEvent, PdArrayEvent, PdArrayZone
from app.services.reversal.models import OriginZone, ReversalConfig, ReversalEvent, ReversalSetup
from app.services.timeframes.core import trading_day_start

_GRADE_ORDER = (
    DisplacementGrade.WEAK,
    DisplacementGrade.MODERATE,
    DisplacementGrade.STRONG,
    DisplacementGrade.EXCEPTIONAL,
)


def _grade_ge(a: DisplacementGrade, b: DisplacementGrade) -> bool:
    return _GRADE_ORDER.index(a) >= _GRADE_ORDER.index(b)


@dataclass
class _Setup:
    origin: OriginZone
    state: SetupState
    discovered_at: object  # datetime
    state_changed_at: object
    discovered_index: int
    rebalanced_index: int | None = None
    rebalanced_at: object | None = None
    reaction_index: int | None = None
    reaction_at: object | None = None
    armed_index: int | None = None
    armed_at: object | None = None
    confirmations: list[ReversalConfirmation] = field(default_factory=list)
    confirming_zone_ids: list[str] = field(default_factory=list)
    protective_level: float | None = None
    entry_price: float | None = None
    entry_zone_id: str | None = None
    stop_price: float | None = None
    target_price: float | None = None
    target_pool_id: str | None = None
    target_label: str | None = None
    rr: float | None = None
    reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in (SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.ENTRY_MISSED)


@dataclass
class ReversalResult:
    setups: list[ReversalSetup]
    events: list[ReversalEvent]


def bias_gate_allows(origin: OriginZone, bias: HtfBias, cfg: ReversalConfig) -> bool:
    """A counter-bias reversal is allowed only when a D1 origin outranks the bias, or the HTF bias is
    non-directional (transitioning/unclear). Directional bias blocks an H4/H1 origin reversal."""
    if origin.timeframe in cfg.always_allowed_origin_timeframes:
        return True
    return bias in cfg.allowed_biases


def _rebalance_touched(origin: OriginZone, c: Candle) -> bool:
    if origin.direction is Direction.BEARISH:  # imbalance above; price rebalances UP into the body
        return c.high >= origin.body_bottom
    return c.low <= origin.body_top  # bullish: price rebalances DOWN into the body


def _closed_beyond_far_edge(origin: OriginZone, c: Candle) -> bool:
    if origin.direction is Direction.BEARISH:
        return c.close > origin.far_edge  # closed above the origin high
    return c.close < origin.far_edge  # closed below the origin low


def _reaction(origin: OriginZone, c: Candle, disp: DisplacementEvent | None, cfg: ReversalConfig) -> bool:
    """A rejection off the rebalance: a rejection wick on the zone side that closes back out, OR an
    opposite-direction (reversal-direction) displacement close."""
    rng = c.high - c.low
    if rng > 0:
        if origin.direction is Direction.BEARISH:
            upper_wick = c.high - max(c.open, c.close)
            if c.high >= origin.body_bottom and c.close < origin.body_bottom:
                if upper_wick / rng >= cfg.reaction_wick_pct:
                    return True
        else:
            lower_wick = min(c.open, c.close) - c.low
            if c.low <= origin.body_top and c.close > origin.body_top:
                if lower_wick / rng >= cfg.reaction_wick_pct:
                    return True
    if disp is not None and disp.direction is origin.direction:
        if _grade_ge(disp.grade, cfg.reaction_displacement_min_grade):
            return True
    return False


def _overlaps_origin(origin: OriginZone, zone: PdArrayZone) -> bool:
    return zone.bottom <= origin.body_top and zone.top >= origin.body_bottom


def _confirmations_at(
    origin: OriginZone,
    time: object,
    zones_by_created: dict[object, list[PdArrayZone]],
    ifvg_confirmed_at: dict[object, list[PdArrayEvent]],
    zones_by_id: dict[str, PdArrayZone],
) -> list[tuple[ReversalConfirmation, str]]:
    """Confirmations firing at this candle time, in the reversal direction (origin.direction).

    IFVG_FLIP: a confirmed IFVG at (overlapping) the origin. NEW_FVG / IMR: a fresh zone left by the
    reversal displacement leg. Returns (confirmation, zone_id) pairs; empty when none fired."""
    out: list[tuple[ReversalConfirmation, str]] = []
    for e in ifvg_confirmed_at.get(time, []):
        z = zones_by_id.get(e.zone_id)
        if z is not None and z.direction is origin.direction and _overlaps_origin(origin, z):
            out.append((ReversalConfirmation.IFVG_FLIP, z.id))
    for z in zones_by_created.get(time, []):
        if z.direction is not origin.direction:
            continue
        if z.type is PdArrayType.IMR:
            out.append((ReversalConfirmation.IMR, z.id))
        elif z.type is PdArrayType.FVG:
            out.append((ReversalConfirmation.NEW_FVG, z.id))
    return out


def _entry_zone(
    confirming_zone_ids: Sequence[str], zones_by_id: dict[str, PdArrayZone]
) -> PdArrayZone | None:
    """The confirming structure to enter from: the most-recent, then tightest, zone. (IMR uses the same
    zone level as its structural level.)"""
    zones = [zones_by_id[z] for z in confirming_zone_ids if z in zones_by_id]
    if not zones:
        return None
    return max(zones, key=lambda z: (z.created_at, -(z.top - z.bottom)))


def _nearest_target(
    direction: Direction, entry: float, pools: Sequence[LiquidityPool]
) -> LiquidityPool | None:
    """Nearest opposite-side (reversal-direction) draw beyond the entry; ties broken by higher
    significant-liquidity tier. The reversal is REVERSAL_EXEMPT, so no trend filter is applied."""
    want = LiquiditySide.SSL if direction is Direction.BEARISH else LiquiditySide.BSL
    beyond = [
        p
        for p in pools
        if not p.taken
        and p.side is want
        and (p.price < entry if direction is Direction.BEARISH else p.price > entry)
    ]
    if not beyond:
        return None
    return min(beyond, key=lambda p: (abs(p.price - entry), -significance_rank(p.type)))


def analyze_reversals(
    origin_zones: Sequence[OriginZone],
    candles: Sequence[Candle],
    bias: HtfBias,
    displacements: Sequence[DisplacementEvent],
    cfg: ReversalConfig,
    pd_zones: Sequence[PdArrayZone] = (),
    pd_events: Sequence[PdArrayEvent] = (),
    liquidity_pools: Sequence[LiquidityPool] = (),
    atr: float = 0.0,
) -> ReversalResult:
    events: list[ReversalEvent] = []
    setups: list[_Setup] = []
    disp_by_time = {d.time: d for d in displacements}
    zones_by_id = {z.id: z for z in pd_zones}
    zones_by_created: dict[object, list[PdArrayZone]] = {}
    for z in pd_zones:
        zones_by_created.setdefault(z.created_at, []).append(z)
    ifvg_confirmed_at: dict[object, list[PdArrayEvent]] = {}
    for e in pd_events:
        if e.type is PdArrayEventType.IFVG_CONFIRMED:
            ifvg_confirmed_at.setdefault(e.time, []).append(e)

    def emit(s: _Setup, state: SetupState, c: Candle, detail: str) -> None:
        s.state = state
        s.state_changed_at = c.close_time
        events.append(
            ReversalEvent(
                id=f"REV:{s.origin.id}:{state.value}:{c.open_time.isoformat()}",
                setup_id=f"REV:{s.origin.id}",
                direction=s.origin.direction,
                state=state,
                time=c.open_time,
                price=c.close,
                detail=detail,
            )
        )

    # Discover one setup per eligible origin zone (bias gate applied once, at the origin's close).
    for origin in origin_zones:
        if not bias_gate_allows(origin, bias, cfg):
            continue
        idx = next((i for i, c in enumerate(candles) if c.close_time >= origin.known_at), None)
        if idx is None:
            continue
        setups.append(
            _Setup(
                origin=origin,
                state=SetupState.DISCOVERED,
                discovered_at=origin.known_at,
                state_changed_at=origin.known_at,
                discovered_index=idx,
            )
        )

    for s in setups:
        for i in range(s.discovered_index, len(candles)):
            if s.terminal or s.state is SetupState.BLOCKED:
                break  # BLOCKED (plan ready, pending risk/news gates) is the last engine transition
            c = candles[i]
            if _closed_beyond_far_edge(s.origin, c):
                emit(s, SetupState.INVALIDATED, c, "closed beyond the origin far edge")
                s.reason = "imbalance broke the wrong way"
                continue
            if s.rebalanced_index is None:
                if _rebalance_touched(s.origin, c):
                    s.rebalanced_index = i
                    s.rebalanced_at = c.close_time
                    emit(s, SetupState.REBALANCE_TOUCH, c, "rebalanced the origin imbalance")
                elif i - s.discovered_index > cfg.rebalance_window_bars:
                    emit(s, SetupState.EXPIRED, c, "no rebalance within the window")
                    s.reason = "no rebalance within window"
                elif s.state is SetupState.DISCOVERED:
                    emit(s, SetupState.REBALANCE_WATCH, c, "watching for a rebalance of the origin")
            elif s.reaction_index is None:
                if _reaction(s.origin, c, disp_by_time.get(c.open_time), cfg):
                    s.reaction_index = i
                    s.reaction_at = c.close_time
                    emit(s, SetupState.REACTION, c, "rejection off the rebalance")
                elif i - s.rebalanced_index > cfg.reaction_window_bars:
                    emit(s, SetupState.EXPIRED, c, "no reaction within the window")
                    s.reason = "no reaction within window"
            elif s.armed_index is None:
                fired = _confirmations_at(
                    s.origin, c.open_time, zones_by_created, ifvg_confirmed_at, zones_by_id
                )
                if fired:
                    s.confirmations = [k for k, _ in fired]
                    s.confirming_zone_ids = [zid for _, zid in fired]
                    names = "/".join(k.value for k in s.confirmations)
                    emit(s, SetupState.CONFIRMATION, c, f"reversal confirmed by {names}")
                    s.protective_level = s.origin.far_edge
                    _build_plan(s, zones_by_id, liquidity_pools, atr, cfg)
                    s.armed_index = i
                    s.armed_at = c.close_time
                    detail = "reversal armed"
                    if s.entry_price is not None:
                        entry, stop = round(s.entry_price, 5), round(s.stop_price or 0, 5)
                        detail = f"reversal armed; entry {entry} stop {stop}"
                    emit(s, SetupState.SETUP_ARMED, c, detail)
                elif i - s.reaction_index > cfg.flip_window_bars:
                    emit(s, SetupState.EXPIRED, c, "no confirmation within the flip window")
                    s.reason = "no confirmation within flip window"
            else:  # ARMED: await the entry-zone retest; chase guard if the target is hit first
                if _target_reached(s, c):
                    emit(s, SetupState.ENTRY_MISSED, c, "target reached before entry (do not chase)")
                    s.reason = "target reached before entry"
                elif _entry_touched(s, c):
                    emit(s, SetupState.ENTRY_ZONE_TOUCHED, c, "price retested the entry zone")
                    emit(s, SetupState.BLOCKED, c, "reversal plan ready; pending risk/news gates")
                elif s.armed_index is not None and i - s.armed_index > cfg.entry_window_bars:
                    emit(s, SetupState.EXPIRED, c, "entry zone not retested within the window")
                    s.reason = "no entry retest within window"

    return ReversalResult(setups=[_to_model(s) for s in setups], events=events)


def _entry_touched(s: _Setup, c: Candle) -> bool:
    if s.entry_price is None:
        return False
    return c.high >= s.entry_price if s.origin.direction is Direction.BEARISH else c.low <= s.entry_price


def _target_reached(s: _Setup, c: Candle) -> bool:
    if s.target_price is None:
        return False
    return c.low <= s.target_price if s.origin.direction is Direction.BEARISH else c.high >= s.target_price


def _build_plan(
    s: _Setup,
    zones_by_id: dict[str, PdArrayZone],
    pools: Sequence[LiquidityPool],
    atr: float,
    cfg: ReversalConfig,
) -> None:
    """At ARM: limit entry at the confirming structure's midpoint, ATR-buffered stop beyond the origin
    far edge, target the nearest opposite-side draw. Never an authorization — only a plan."""
    zone = _entry_zone(s.confirming_zone_ids, zones_by_id)
    if zone is None:
        return
    s.entry_price = zone.midpoint
    s.entry_zone_id = zone.id
    buffer = cfg.stop_buffer_atr * atr
    s.stop_price = (
        s.origin.far_edge + buffer if s.origin.direction is Direction.BEARISH else s.origin.far_edge - buffer
    )
    target = _nearest_target(s.origin.direction, s.entry_price, pools)
    if target is not None:
        s.target_price = target.price
        s.target_pool_id = target.id
        s.target_label = target.label
        risk = abs(s.entry_price - s.stop_price)
        if risk > 0:
            s.rr = round(abs(s.target_price - s.entry_price) / risk, 2)


def _to_model(s: _Setup) -> ReversalSetup:
    return ReversalSetup(
        id=f"REV:{s.origin.id}",
        setup_type=SetupType.REVERSAL_NO_WICK_IFVG,
        direction=s.origin.direction,
        state=s.state,
        terminal=s.terminal,
        trading_day=trading_day_start(s.origin.known_at).date(),
        origin=s.origin,
        discovered_at=s.discovered_at,  # type: ignore[arg-type]
        state_changed_at=s.state_changed_at,  # type: ignore[arg-type]
        rebalanced_at=s.rebalanced_at,  # type: ignore[arg-type]
        reaction_at=s.reaction_at,  # type: ignore[arg-type]
        armed_at=s.armed_at,  # type: ignore[arg-type]
        confirmations=list(s.confirmations),
        confirming_zone_ids=list(s.confirming_zone_ids),
        protective_level=s.protective_level,
        entry_price=s.entry_price,
        entry_zone_id=s.entry_zone_id,
        stop_price=s.stop_price,
        target_price=s.target_price,
        target_pool_id=s.target_pool_id,
        target_label=s.target_label,
        rr=s.rr,
        reason=s.reason,
    )
