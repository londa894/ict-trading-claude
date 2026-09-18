"""Core setup state machine (Phase 7): one candle-sequential pass, facts known at each candle's close only.

Setup model LIQUIDITY_SWEEP_MSS for bias direction D (everything mirrors for BEARISH):
  bias  the common direction of the latest confirmed EXTERNAL BOS/MSS on every bias timeframe, known by
        the candle's close. At most one open setup; a new one starts only on a later candle than a
        terminal one.
  DISCOVERED       bias D exists (setup opened)
  WATCH            an untaken D-side pool beyond price exists (target = nearest; locked at the sweep)
  SETUP_FORMING    close within `formingApproachAtr` x ATR of an untaken opposite-side pool beyond price
                   (back to WATCH when it moves away; back to DISCOVERED when no target is left)
  LIQUIDITY_EVENT  an opposite-side SWEEP/RECLAIM on this candle
  WAITING_FOR_MSS  a later candle without the confirmation break
  SETUP_ARMED      a confirmed D-direction MSS/CHOCH (any level; displacement PRESENT when required)
                   within `mss.windowBars` of the liquidity event. Protective extreme = the most
                   adverse extreme from the liquidity event candle through the break candle.
  WAITING_FOR_RETRACEMENT  a later candle; leg FVGs = D-direction FVGs created from the liquidity event
                           candle up to `zoneGraceBars` after the break
  ENTRY_ZONE_APPROACHING   close within `retracement.approachAtr` x ATR of the nearest live FVG's edge
  ENTRY_ZONE_TOUCHED       a wick reaches the entry edge of a live FVG created before the candle
  WAITING_FOR_CONFIRMATION a later candle without an entry confirmation
  BLOCKED          (Phase 8) an entry model confirmed within `confirmationWindowBars` of the touch and
                   the plan passed chase protection; blocked until clean risk and news
                   gates exist
Terminal:
  INVALIDATED  HTF bias no longer D; before the break a close beyond the swept extreme; after arming a
               close beyond the protective extreme; the target pool taken before a confirmed entry;
               every leg FVG invalidated
  EXPIRED      no break within the window; no FVG by the end of the grace window; no touch within
               `retracement.windowBars` of the break; the trading day ended after the liquidity event
  ENTRY_MISSED (Phase 8) plan failed chase protection at confirmation; or after BLOCKED, TP1 traded or the R:R
               from the latest close collapsed (do not chase)
After BLOCKED a wick through the plan stop or a close beyond the protective extreme invalidates.
LONG_READY / SHORT_READY / ACTIVE / CLOSED are never emitted while verdict authority is FAIL_SAFE_ONLY.
BLOCKED is also the as-of view for ineligible data.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    EntryModel,
    LiquidityEventType,
    LiquiditySide,
    PdArrayEventType,
    PdArrayType,
    QualifierStatus,
    SetupState,
    SetupStep,
    SetupStepStatus,
    SetupType,
    StructureEventStatus,
    Timeframe,
)
from app.services.entry.models import EntryConfig, EntryPlan
from app.services.entry.plan import build_plan, chase_reason
from app.services.liquidity.models import LiquidityEvent, LiquidityPool
from app.services.no_wick.models import NoWickEvent
from app.services.pd_arrays.displacement import atr_before
from app.services.pd_arrays.models import PdArrayEvent, PdArrayZone
from app.services.setup_state.chase import chase_guard_applies
from app.services.setup_state.models import (
    BiasPoint,
    BreakRef,
    LiquidityRef,
    Setup,
    SetupConfig,
    SetupEvent,
    SetupStepState,
    TargetRef,
)
from app.services.setup_state.significance import sweep_is_significant
from app.services.setup_state.targets import eligible_targets
from app.services.structure.models import StructureEvent
from app.services.structure.swings import true_ranges

S = SetupState
TERMINAL = frozenset({S.INVALIDATED, S.EXPIRED, S.ENTRY_MISSED})
PRE_LIQUIDITY = frozenset({S.DISCOVERED, S.WATCH, S.SETUP_FORMING})
WAITING_BREAK = frozenset({S.LIQUIDITY_EVENT, S.WAITING_FOR_MSS})
TAKE_EVENTS = frozenset({LiquidityEventType.SWEEP, LiquidityEventType.BREAK})

# Every transition the engine may emit (enforced by the property tests).
ALLOWED: dict[SetupState, frozenset[SetupState]] = {
    S.DISCOVERED: frozenset({S.WATCH, S.INVALIDATED}),
    S.WATCH: frozenset({S.SETUP_FORMING, S.LIQUIDITY_EVENT, S.DISCOVERED, S.INVALIDATED}),
    S.SETUP_FORMING: frozenset({S.WATCH, S.LIQUIDITY_EVENT, S.DISCOVERED, S.INVALIDATED}),
    S.LIQUIDITY_EVENT: frozenset({S.WAITING_FOR_MSS, S.SETUP_ARMED, S.INVALIDATED, S.EXPIRED}),
    S.WAITING_FOR_MSS: frozenset({S.SETUP_ARMED, S.INVALIDATED, S.EXPIRED}),
    S.SETUP_ARMED: frozenset(
        {S.WAITING_FOR_RETRACEMENT, S.ENTRY_ZONE_APPROACHING, S.ENTRY_ZONE_TOUCHED, S.INVALIDATED, S.EXPIRED}
    ),
    S.WAITING_FOR_RETRACEMENT: frozenset(
        {S.ENTRY_ZONE_APPROACHING, S.ENTRY_ZONE_TOUCHED, S.INVALIDATED, S.EXPIRED}
    ),
    S.ENTRY_ZONE_APPROACHING: frozenset(
        {S.ENTRY_ZONE_TOUCHED, S.WAITING_FOR_RETRACEMENT, S.INVALIDATED, S.EXPIRED}
    ),
    S.ENTRY_ZONE_TOUCHED: frozenset(
        {S.WAITING_FOR_CONFIRMATION, S.BLOCKED, S.ENTRY_MISSED, S.INVALIDATED, S.EXPIRED}
    ),
    S.WAITING_FOR_CONFIRMATION: frozenset({S.BLOCKED, S.ENTRY_MISSED, S.INVALIDATED, S.EXPIRED}),
    S.BLOCKED: frozenset({S.ENTRY_MISSED, S.INVALIDATED, S.EXPIRED}),
}


@dataclass(frozen=True)
class SetupInputs:
    structure_events: Sequence[StructureEvent]
    pools: Sequence[LiquidityPool]
    liquidity_events: Sequence[LiquidityEvent]
    pd_zones: Sequence[PdArrayZone]
    pd_events: Sequence[PdArrayEvent]
    bias_points: Sequence[BiasPoint]
    bias_timeframes: Sequence[Timeframe]
    # Phase 8 entry inputs (None -> that entry model cannot confirm)
    no_wick_events: Sequence[NoWickEvent] | None = None
    ltf_candles: Sequence[Candle] | None = None
    ltf_breaks: Sequence[StructureEvent] | None = None


@dataclass(frozen=True)
class SetupResult:
    setups: list[Setup]
    events: list[SetupEvent]


@dataclass
class _Setup:
    id: str
    direction: Direction
    state: SetupState
    trading_day: date
    discovered_index: int
    state_index: int
    target: TargetRef | None = None
    liquidity: LiquidityRef | None = None
    liquidity_index: int = -1
    adverse: float | None = None
    mss: BreakRef | None = None
    mss_index: int = -1
    protective: float | None = None
    zone_ids: list[str] = field(default_factory=list)
    touched_zone_id: str | None = None
    touched_index: int = -1
    reason: str | None = None
    plan: EntryPlan | None = None
    retest_index: int = -1
    retest_close: float = 0.0
    breaks_without_displacement: int = 0  # diagnostic: same-direction breaks the qualifier rejected

    @property
    def bullish(self) -> bool:
        return self.direction is Direction.BULLISH


def bias_at(points: Sequence[BiasPoint], timeframes: Sequence[Timeframe], at: datetime) -> Direction | None:
    """Common direction of the latest point known at `at` on every timeframe (None if missing/differing)."""
    directions: set[Direction] = set()
    for tf in timeframes:
        known = [p for p in points if p.timeframe is tf and p.known_at <= at]
        if not known:
            return None
        directions.add(max(known, key=lambda p: p.known_at).direction)
    return next(iter(directions)) if len(directions) == 1 else None


class _Machine:
    def __init__(
        self,
        candles: Sequence[Candle],
        inputs: SetupInputs,
        cfg: SetupConfig,
        trading_day_of: Callable[[datetime], date],
        entry_cfg: EntryConfig | None = None,
    ) -> None:
        self.candles = candles
        self.cfg = cfg
        self.ecfg = entry_cfg or EntryConfig.from_spec()
        self.inputs = inputs
        self.trading_day_of = trading_day_of
        self.n = len(candles)
        self.trs = true_ranges(candles)
        self.index = {c.open_time: i for i, c in enumerate(candles)}
        self.events: list[SetupEvent] = []

        self.breaks_at: dict[int, list[StructureEvent]] = defaultdict(list)
        for e in inputs.structure_events:
            if (
                e.status is StructureEventStatus.CONFIRMED
                and e.type in cfg.mss_break_types
                and e.time in self.index
            ):
                self.breaks_at[self.index[e.time]].append(e)
        self.liq_at: dict[int, list[LiquidityEvent]] = defaultdict(list)
        self.taken_index: dict[str, int] = {}
        for le in inputs.liquidity_events:
            if le.time not in self.index:
                continue
            i = self.index[le.time]
            self.liq_at[i].append(le)
            if le.type in TAKE_EVENTS:
                self.taken_index[le.pool_id] = min(self.taken_index.get(le.pool_id, i), i)
        self.pools = list(inputs.pools)
        self.pool_by_id = {p.id: p for p in self.pools}
        kinds = {PdArrayType.FVG} | ({PdArrayType.IFVG} if self.ecfg.include_confirmed_ifvg else set())
        self.zones = {z.id: z for z in inputs.pd_zones if z.type in kinds}
        self.fvg_created: dict[str, int] = {}  # FVG: CREATED; IFVG: IFVG_CONFIRMED
        self.fvg_invalidated: dict[str, int] = {}
        for pe in inputs.pd_events:
            if pe.zone_id not in self.zones or pe.time not in self.index:
                continue
            usable_event = (
                PdArrayEventType.CREATED
                if self.zones[pe.zone_id].type is PdArrayType.FVG
                else PdArrayEventType.IFVG_CONFIRMED
            )
            if pe.type is usable_event:
                self.fvg_created[pe.zone_id] = self.index[pe.time]
            elif pe.type is PdArrayEventType.INVALIDATED:
                self.fvg_invalidated[pe.zone_id] = self.index[pe.time]
        self.no_wick_at: dict[int, list[NoWickEvent]] = defaultdict(list)
        for nw in inputs.no_wick_events or []:
            if nw.time in self.index:
                self.no_wick_at[self.index[nw.time]].append(nw)
        self.ltf_close = {c.open_time: c.close for c in inputs.ltf_candles or []}
        self.ltf_breaks = sorted(
            (
                b
                for b in inputs.ltf_breaks or []
                if b.status is StructureEventStatus.CONFIRMED and b.type in self.ecfg.ltf_break_types
            ),
            key=lambda b: b.time,
        )
        self.bias_points = sorted(inputs.bias_points, key=lambda p: p.known_at)
        self.bias_times = [p.known_at for p in self.bias_points]

    # -- facts at candle i -----------------------------------------------------------------------------------
    def bias(self, i: int) -> Direction | None:
        at = self.candles[i].close_time
        known = self.bias_points[: bisect_right(self.bias_times, at)]
        return bias_at(known, self.inputs.bias_timeframes, at)

    def atr(self, i: int) -> float:
        return atr_before(self.trs, i + 1, self.cfg.atr_period) or 0.0

    def untaken(self, pool: LiquidityPool, i: int) -> bool:
        return pool.known_at <= self.candles[i].close_time and self.taken_index.get(pool.id, self.n) > i

    def target(self, s: _Setup, i: int) -> LiquidityPool | None:
        close = self.candles[i].close
        side = LiquiditySide.BSL if s.bullish else LiquiditySide.SSL
        beyond = [
            p
            for p in self.pools
            if p.side is side
            and self.untaken(p, i)
            and ((p.price > close) if s.bullish else (p.price < close))
        ]
        beyond = eligible_targets(beyond, price=close, atr=self.atr(i), min_atr=self.cfg.min_target_atr)
        return min(beyond, key=lambda p: (abs(p.price - close), p.id)) if beyond else None

    def near_opposite(self, s: _Setup, i: int) -> bool:
        close, reach = self.candles[i].close, self.cfg.forming_approach_atr * self.atr(i)
        side = LiquiditySide.SSL if s.bullish else LiquiditySide.BSL
        return any(
            p.side is side
            and self.untaken(p, i)
            and ((close >= p.price >= close - reach) if s.bullish else (close <= p.price <= close + reach))
            for p in self.pools
        )

    def sweep(self, s: _Setup, i: int) -> LiquidityEvent | None:
        side = LiquiditySide.SSL if s.bullish else LiquiditySide.BSL
        hits = [e for e in self.liq_at.get(i, []) if e.side is side and e.type in self.cfg.sweep_event_types]
        if self.cfg.significant_sweeps_only:
            # Price sweeps minor swing levels constantly; only key levels may start a setup.
            hits = [e for e in hits if sweep_is_significant(e, True)]
        if not hits:
            return None
        return min(hits, key=lambda e: e.extreme) if s.bullish else max(hits, key=lambda e: e.extreme)

    # -- transitions -----------------------------------------------------------------------------------------
    def emit(self, s: _Setup, state: SetupState, i: int, detail: str, price: float | None = None) -> None:
        if state is not s.state and state not in ALLOWED.get(s.state, frozenset()):
            raise AssertionError(f"illegal setup transition {s.state} -> {state}")
        s.state, s.state_index = state, i
        c = self.candles[i]
        self.events.append(
            SetupEvent(
                id=f"{s.id}:{state.value}:{c.open_time.isoformat()}",
                setup_id=s.id,
                direction=s.direction,
                state=state,
                time=c.open_time,
                price=c.close if price is None else price,
                detail=detail,
            )
        )

    def end(self, s: _Setup, state: SetupState, i: int, reason: str) -> None:
        s.reason = reason
        self.emit(s, state, i, reason)

    def advance(self, s: _Setup, i: int, bias: Direction | None, new: bool = False) -> None:
        c = self.candles[i]
        if not new and bias is not s.direction:
            self.end(s, S.INVALIDATED, i, f"HTF bias changed to {bias.value if bias else 'NONE'}")
            return
        if (
            self.cfg.expire_at_trading_day_end
            and s.state not in PRE_LIQUIDITY
            and self.trading_day_of(c.open_time) != s.trading_day
        ):
            self.end(s, S.EXPIRED, i, "trading day ended")
            return
        if s.state in PRE_LIQUIDITY:
            self.pre_liquidity(s, i)
        elif s.state in WAITING_BREAK:
            self.waiting_break(s, i)
        else:
            self.armed(s, i)

    def pre_liquidity(self, s: _Setup, i: int) -> None:
        c = self.candles[i]
        target = self.target(s, i)
        if target is None:
            if s.state is not S.DISCOVERED:
                self.emit(s, S.DISCOVERED, i, "no untaken target liquidity beyond price")
            return
        s.target = TargetRef(pool_id=target.id, pool_type=target.type, label=target.label, price=target.price)
        s.trading_day = self.trading_day_of(c.open_time)
        if s.state is S.DISCOVERED:
            self.emit(s, S.WATCH, i, f"target {target.label} @ {target.price}")
        sweep = self.sweep(s, i)
        if sweep is not None:
            s.liquidity = LiquidityRef(
                pool_id=sweep.pool_id,
                pool_type=sweep.pool_type,
                event_type=sweep.type,
                time=sweep.time,
                extreme=sweep.extreme,
            )
            s.liquidity_index, s.adverse = i, sweep.extreme
            self.emit(
                s,
                S.LIQUIDITY_EVENT,
                i,
                f"{sweep.type.value} {sweep.side.value} {sweep.pool_type.value}",
                sweep.extreme,
            )
            self.waiting_break(s, i, same_candle=True)
            return
        near = self.near_opposite(s, i)
        if near and s.state is S.WATCH:
            self.emit(s, S.SETUP_FORMING, i, "price trading into opposite-side liquidity")
        elif not near and s.state is S.SETUP_FORMING:
            self.emit(s, S.WATCH, i, "moved away from opposite-side liquidity")

    def waiting_break(self, s: _Setup, i: int, same_candle: bool = False) -> None:
        c = self.candles[i]
        assert s.liquidity is not None
        extreme = c.low if s.bullish else c.high
        s.adverse = (
            extreme
            if s.adverse is None
            else (min(s.adverse, extreme) if s.bullish else max(s.adverse, extreme))
        )
        if not same_candle:
            if (c.close < s.liquidity.extreme) if s.bullish else (c.close > s.liquidity.extreme):
                self.end(s, S.INVALIDATED, i, "closed beyond the swept extreme (liquidity ran)")
                return
            deeper = self.sweep(s, i)
            if deeper is not None and (
                (deeper.extreme < s.liquidity.extreme)
                if s.bullish
                else (deeper.extreme > s.liquidity.extreme)
            ):
                s.liquidity = LiquidityRef(
                    pool_id=deeper.pool_id,
                    pool_type=deeper.pool_type,
                    event_type=deeper.type,
                    time=deeper.time,
                    extreme=deeper.extreme,
                )
        brk = self.confirmation_break(s, i)
        if brk is not None:
            s.mss = BreakRef(
                event_id=brk.id,
                type=brk.type,
                level=brk.level,
                time=brk.time,
                price=brk.price,
                displacement_qualifier=brk.displacement_qualifier,
            )
            s.mss_index, s.protective = i, s.adverse
            self.emit(
                s, S.SETUP_ARMED, i, f"{brk.level.value} {brk.type.value} @ {brk.price} with displacement"
            )
            self.collect_zones(s, i)
            return
        if i - s.liquidity_index >= self.cfg.mss_window_bars:
            detail = f"no confirmation break within {self.cfg.mss_window_bars} bars"
            if s.breaks_without_displacement:
                # Names the binding gate: structure broke, the displacement qualifier refused it.
                detail += f" ({s.breaks_without_displacement} break(s) lacked displacement)"
            self.end(s, S.EXPIRED, i, detail)
        elif not same_candle and s.state is S.LIQUIDITY_EVENT:
            self.emit(s, S.WAITING_FOR_MSS, i, "waiting for a displacement MSS/CHoCH")

    def confirmation_break(self, s: _Setup, i: int) -> StructureEvent | None:
        for e in self.breaks_at.get(i, []):
            if e.direction is not s.direction:
                continue
            if self.cfg.mss_require_displacement and e.displacement_qualifier is not QualifierStatus.PRESENT:
                # Diagnostic only: remember that structure DID break but failed the
                # displacement qualifier, so the expiry reason can name the binding gate.
                s.breaks_without_displacement += 1
                continue
            return e
        return None

    def collect_zones(self, s: _Setup, i: int) -> None:
        last = min(i, s.mss_index + self.cfg.zone_grace_bars)
        for zid, created in sorted(self.fvg_created.items(), key=lambda kv: (kv[1], kv[0])):
            zone = self.zones[zid]
            if (
                zid not in s.zone_ids
                and zone.direction is s.direction
                and s.liquidity_index <= created <= last
            ):
                s.zone_ids.append(zid)

    def live_zones(self, s: _Setup, i: int) -> list[PdArrayZone]:
        return [self.zones[z] for z in s.zone_ids if self.fvg_invalidated.get(z, self.n) > i]

    def armed(self, s: _Setup, i: int) -> None:
        c = self.candles[i]
        assert s.protective is not None and s.target is not None
        if s.state is S.BLOCKED:
            self.blocked(s, i)
            return
        self.collect_zones(s, i)
        if (c.close < s.protective) if s.bullish else (c.close > s.protective):
            self.end(s, S.INVALIDATED, i, "closed beyond the protective extreme")
            return
        if chase_guard_applies(
            target_taken=self.taken_index.get(s.target.pool_id, self.n) <= i,
            zone_touched=s.touched_index >= 0,
            after_touch_only=self.cfg.chase_guard_after_touch_only,
        ):
            self.end(s, S.INVALIDATED, i, "target liquidity reached before a confirmed entry (do not chase)")
            return
        live = self.live_zones(s, i)
        if s.zone_ids and not live:
            self.end(s, S.INVALIDATED, i, "every retracement zone was invalidated")
            return
        if not s.zone_ids and i >= s.mss_index + self.cfg.zone_grace_bars:
            self.end(s, S.EXPIRED, i, "no FVG formed in the displacement leg")
            return
        if s.state in (S.ENTRY_ZONE_TOUCHED, S.WAITING_FOR_CONFIRMATION):
            if i > s.touched_index and self.confirm(s, i):
                return
            if i - s.touched_index >= self.ecfg.confirmation_window_bars:
                window = self.ecfg.confirmation_window_bars
                self.end(s, S.EXPIRED, i, f"no entry confirmation within {window} bars of the touch")
                return
            if s.state is S.ENTRY_ZONE_TOUCHED and i > s.touched_index:
                self.emit(s, S.WAITING_FOR_CONFIRMATION, i, f"waiting for {self.models_text()}")
            return
        if i - s.mss_index >= self.cfg.retracement_window_bars:
            self.end(s, S.EXPIRED, i, f"no retracement within {self.cfg.retracement_window_bars} bars")
            return
        if i <= s.mss_index:
            return
        usable = [z for z in live if self.fvg_created.get(z.id, self.n) < i]
        if not usable:
            if s.state is S.SETUP_ARMED:
                self.emit(s, S.WAITING_FOR_RETRACEMENT, i, "waiting for the leg's FVG")
            return
        zone = max(usable, key=lambda z: z.top) if s.bullish else min(usable, key=lambda z: z.bottom)
        edge = zone.top if s.bullish else zone.bottom
        if (c.low <= edge) if s.bullish else (c.high >= edge):
            s.touched_zone_id, s.touched_index, s.retest_index = zone.id, i, -1
            self.emit(s, S.ENTRY_ZONE_TOUCHED, i, f"touched {zone.type.value} {zone.bottom}-{zone.top}", edge)
            self.confirm(s, i)
            return
        distance = (c.close - edge) if s.bullish else (edge - c.close)
        near = distance <= self.cfg.retracement_approach_atr * self.atr(i)
        if near and s.state is not S.ENTRY_ZONE_APPROACHING:
            self.emit(s, S.ENTRY_ZONE_APPROACHING, i, f"approaching {zone.type.value} edge {edge}", edge)
        elif not near and s.state is not S.WAITING_FOR_RETRACEMENT:
            self.emit(s, S.WAITING_FOR_RETRACEMENT, i, "waiting for retracement into the zone")

    def models_text(self) -> str:
        return "/".join(m.value for m in self.ecfg.models) + f" confirmation ({self.ecfg.mode.value})"

    def ltf_entry(self, s: _Setup, i: int) -> float | None:
        """Earliest LTF reversal break in [touch candle open, candle i close), known by candle i's close."""
        start = self.candles[s.touched_index].open_time
        end = self.candles[i].close_time
        step = self.ecfg.execution_timeframe.duration
        for b in self.ltf_breaks:
            if b.direction is s.direction and start <= b.time and b.time + step <= end:
                return self.ltf_close.get(b.time)
        return None

    def confirm(self, s: _Setup, i: int) -> bool:
        assert s.touched_zone_id is not None
        c = self.candles[i]
        zone = self.zones[s.touched_zone_id]
        edge = zone.top if s.bullish else zone.bottom
        closes_beyond = (c.close > edge) if s.bullish else (c.close < edge)
        with_direction = (c.close > c.open) if s.bullish else (c.close < c.open)
        for model in self.ecfg.models:
            entry: float | None = None
            label = model
            if model is EntryModel.LIMIT_RESEARCH:
                entry = edge if i == s.touched_index else None
            elif model is EntryModel.LTF_REFINEMENT:
                entry = self.ltf_entry(s, i)
            elif model is EntryModel.NO_WICK_REBALANCE:
                meaningful = any(
                    e.direction is s.direction and e.strength.rank >= 1 for e in self.no_wick_at.get(i, [])
                )
                entry = c.close if closes_beyond and meaningful else None
            elif model is EntryModel.M15_CLOSE:
                if closes_beyond and with_direction:
                    entry = c.close
                    label = EntryModel.IFVG if zone.type is PdArrayType.IFVG else model
            elif model is EntryModel.CONSERVATIVE_RETEST:
                beyond_first = (c.close > s.retest_close) if s.bullish else (c.close < s.retest_close)
                if s.retest_index >= 0 and i == s.retest_index + 1 and beyond_first:
                    entry = c.close
                elif closes_beyond and with_direction and i > s.retest_index:
                    s.retest_index, s.retest_close = i, c.close
            if entry is not None:
                self.finish_confirmation(s, i, label, entry, zone)
                return True
        return False

    def finish_confirmation(
        self, s: _Setup, i: int, model: EntryModel, entry: float, zone: PdArrayZone
    ) -> None:
        assert s.target is not None and s.protective is not None
        side = LiquiditySide.BSL if s.bullish else LiquiditySide.SSL
        further = [
            p.price for p in self.pools if p.side is side and p.id != s.target.pool_id and self.untaken(p, i)
        ]
        plan, reason = build_plan(
            direction=s.direction,
            model=model,
            mode=self.ecfg.mode,
            confirmed_at=self.candles[i].open_time,
            zone_id=zone.id,
            entry=entry,
            protective=s.protective,
            tp1=s.target.price,
            further_targets=further,
            atr=self.atr(i),
            cfg=self.ecfg,
            research_only=model is EntryModel.LIMIT_RESEARCH,
        )
        if plan is None:
            self.end(s, S.ENTRY_MISSED, i, f"{model.value} confirmed but {reason}")
            return
        s.plan = plan
        self.emit(
            s,
            S.BLOCKED,
            i,
            f"{model.value} confirmed (R:R {plan.rr1}); blocked pending a clean risk check and the news gate",
            entry,
        )

    def blocked(self, s: _Setup, i: int) -> None:
        c = self.candles[i]
        plan = s.plan
        assert plan is not None and s.protective is not None and s.target is not None
        if (c.low <= plan.stop) if s.bullish else (c.high >= plan.stop):
            self.end(s, S.INVALIDATED, i, "plan stop traded")
        elif (c.close < s.protective) if s.bullish else (c.close > s.protective):
            self.end(s, S.INVALIDATED, i, "closed beyond the protective extreme")
        elif self.taken_index.get(s.target.pool_id, self.n) <= i:
            self.end(s, S.ENTRY_MISSED, i, "TP1 reached before an authorized entry (do not chase)")
        else:
            reason = chase_reason(plan, c.close)
            if reason is not None:
                self.end(s, S.ENTRY_MISSED, i, reason)

    # -- run -------------------------------------------------------------------------------------------------
    def run(self) -> SetupResult:
        done: list[_Setup] = []
        current: _Setup | None = None
        for i, c in enumerate(self.candles):
            bias = self.bias(i)
            ended_now = False
            if current is not None:
                self.advance(current, i, bias)
                if current.state in TERMINAL:
                    done.append(current)
                    current, ended_now = None, True
            if current is None and bias is not None and not ended_now:
                current = _Setup(
                    id=f"SETUP:{bias.value}:{c.open_time.isoformat()}",
                    direction=bias,
                    state=S.DISCOVERED,
                    trading_day=self.trading_day_of(c.open_time),
                    discovered_index=i,
                    state_index=i,
                )
                tfs = "+".join(t.value for t in self.inputs.bias_timeframes)
                self.emit(current, S.DISCOVERED, i, f"HTF bias {bias.value} ({tfs})")
                self.advance(current, i, bias, new=True)
                if current.state in TERMINAL:  # defensive: a brand-new setup cannot end on its first candle
                    done.append(current)
                    current = None
        setups = [*done, *([current] if current is not None else [])]
        return SetupResult(setups=[self.snapshot(s) for s in setups], events=self.events)

    def snapshot(self, s: _Setup) -> Setup:
        return Setup(
            id=s.id,
            setup_type=SetupType.LIQUIDITY_SWEEP_MSS,
            direction=s.direction,
            state=s.state,
            terminal=s.state in TERMINAL,
            trading_day=s.trading_day,
            discovered_at=self.candles[s.discovered_index].open_time,
            state_changed_at=self.candles[s.state_index].open_time,
            target=s.target,
            liquidity_event=s.liquidity,
            mss=s.mss,
            protective_level=s.protective,
            zone_ids=list(s.zone_ids),
            touched_zone_id=s.touched_zone_id,
            reason=s.reason,
            next_required_event=(
                None if s.state in TERMINAL else next_required_event(s, self.cfg, self.models_text())
            ),
            steps=steps(s, self.models_text()),
            entry_plan=s.plan,
        )


def next_required_event(s: _Setup, cfg: SetupConfig, confirmation: str) -> str:
    target_side = "BSL" if s.bullish else "SSL"
    opposite = "SSL" if s.bullish else "BSL"
    break_needed = f"{s.direction.value} MSS/CHoCH with displacement within {cfg.mss_window_bars} bars"
    return {
        S.DISCOVERED: f"untaken {target_side} target beyond price",
        S.WATCH: f"price trading into {opposite} liquidity",
        S.SETUP_FORMING: f"{opposite} sweep (wick through, close back inside)",
        S.LIQUIDITY_EVENT: break_needed,
        S.WAITING_FOR_MSS: break_needed,
        S.SETUP_ARMED: "retracement into the displacement leg's FVG",
        S.WAITING_FOR_RETRACEMENT: "retracement into the displacement leg's FVG",
        S.ENTRY_ZONE_APPROACHING: "touch of the FVG entry edge",
        S.ENTRY_ZONE_TOUCHED: confirmation,
        S.WAITING_FOR_CONFIRMATION: confirmation,
        S.BLOCKED: "clean risk and news gates, then verdict authority; the confirmed plan is not authorized",
    }.get(s.state, "")


def steps(s: _Setup, confirmation: str) -> list[SetupStepState]:
    def step(kind: SetupStep, done: bool, detail: str) -> SetupStepState:
        return SetupStepState(
            step=kind, status=SetupStepStatus.DONE if done else SetupStepStatus.PENDING, detail=detail
        )

    return [
        step(SetupStep.HTF_BIAS, True, s.direction.value),
        step(SetupStep.DOL_TARGET, s.target is not None, s.target.label if s.target else "none"),
        step(
            SetupStep.LIQUIDITY_EVENT,
            s.liquidity is not None,
            f"{s.liquidity.event_type.value} {s.liquidity.pool_type.value}" if s.liquidity else "none",
        ),
        step(SetupStep.DISPLACEMENT, s.mss is not None, "with the break" if s.mss else "none"),
        step(
            SetupStep.MSS, s.mss is not None, f"{s.mss.level.value} {s.mss.type.value}" if s.mss else "none"
        ),
        step(SetupStep.PD_ARRAY, bool(s.zone_ids), f"{len(s.zone_ids)} leg FVG(s)"),
        step(SetupStep.RETRACEMENT, s.touched_zone_id is not None, s.touched_zone_id or "none"),
        step(
            SetupStep.LTF_CONFIRMATION,
            s.plan is not None,
            f"{s.plan.model.value} @ {s.plan.entry} (R:R {s.plan.rr1})" if s.plan else confirmation,
        ),
        SetupStepState(
            step=SetupStep.RISK,
            status=SetupStepStatus.NOT_EVALUATED,
            detail="assessed by the evaluation (RISK)",
        ),
    ]


def analyze_setups(
    candles: Sequence[Candle],
    inputs: SetupInputs,
    cfg: SetupConfig,
    trading_day_of: Callable[[datetime], date],
    entry_cfg: EntryConfig | None = None,
) -> SetupResult:
    return _Machine(candles, inputs, cfg, trading_day_of, entry_cfg).run()
