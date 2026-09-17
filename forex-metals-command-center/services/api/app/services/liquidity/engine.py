"""Sequential liquidity engine (deterministic, no lookahead).

One pass over CLOSED candles. At candle i:
 1. Pools that became known before candle i opened are activated:
    - swing pools: from the structure swings (internal + external merged by pivot), active from the candle
      after their confirmation; scope becomes EXTERNAL once the external pivot is confirmed;
    - EQH/EQL clusters: formed when a newly active swing pool is within `equal.toleranceAtr` of an
      untaken same-side swing pool at least `equal.minBarsApart` pivots away;
    - key levels (PDH/PDL/PWH/PWL, and Phase 6 session highs/lows): active from the first candle opening
      at/after `known_at`.
 2. Every active pool is evaluated against candle i (state machine below).

State machine for BSL at price P (SSL mirrors it: lows instead of highs, reversed comparisons):
    FRESH/TOUCHED  high > P and close <= P                    -> SWEEP  (SWEPT)
                   high > P and close >  P                    -> BREAK  (BROKEN)
                   P - tol <= high <= P  (new touch episode)  -> TOUCH  (TOUCHED)
    SWEPT          close > sweep high within failure window   -> SWEEP_FAILED (RUN)   "false sweep"
    BROKEN         close <= P within acceptance window        -> RECLAIM (RECLAIMED)
                   later candle, close > P, best close since break >= P + ext*ATR(break)
                                    within acceptance window  -> RUN (RUN)
    RUN, RECLAIMED terminal; SWEPT/BROKEN become terminal when their window expires.
Untaken = FRESH/TOUCHED. APPROACHING is derived for the as-of view only (it is not an event).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import (
    LiquidityEventType,
    LiquidityPoolType,
    LiquidityScope,
    LiquiditySide,
    LiquidityState,
    SwingKind,
    TrendDirection,
)
from app.services.liquidity.models import (
    DolSelection,
    KeyLevel,
    LiquidityConfig,
    LiquidityEvent,
    LiquidityPool,
)
from app.services.liquidity.scoring import magnet_scores, select_dol
from app.services.structure.models import LevelStructure, Swing
from app.services.structure.swings import atr_at, true_ranges

UNTAKEN = frozenset({LiquidityState.FRESH, LiquidityState.TOUCHED})
HIGH_KEY_TYPES = frozenset(
    {
        LiquidityPoolType.PDH,
        LiquidityPoolType.PWH,
        LiquidityPoolType.ASIA_HIGH,
        LiquidityPoolType.LONDON_HIGH,
        LiquidityPoolType.NY_AM_HIGH,
        LiquidityPoolType.NY_PM_HIGH,
    }
)


@dataclass
class _Pool:
    id: str
    type: LiquidityPoolType
    side: LiquiditySide
    scope: LiquidityScope
    label: str
    price: float
    formed_index: int | None  # pivot index for swing-based pools
    formed_at: datetime
    known_at: datetime
    members: list[_Pool] = field(default_factory=list)  # EQ clusters: member swing pools
    source_times: list[datetime] = field(default_factory=list)
    state: LiquidityState = LiquidityState.FRESH
    touches: int = 0
    in_touch: bool = False
    state_index: int | None = None
    sweep_index: int = -1
    sweep_extreme: float = 0.0
    break_index: int = -1
    break_atr: float = 0.0
    best_close: float = 0.0  # best (signed) close since break

    @property
    def is_high(self) -> bool:
        return self.side is LiquiditySide.BSL

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def untaken(self) -> bool:
        return self.state in UNTAKEN


@dataclass(frozen=True)
class LiquidityResult:
    pools: list[LiquidityPool]
    events: list[LiquidityEvent]
    dol: DolSelection | None


def _merge_swings(
    internal: LevelStructure | None, external: LevelStructure | None
) -> list[tuple[Swing, Swing | None]]:
    """(earliest-confirmed swing, external swing if any) per pivot, merged across structure levels."""
    by_key: dict[tuple[SwingKind, datetime], dict[str, Swing]] = defaultdict(dict)
    for level, name in ((internal, "internal"), (external, "external")):
        if level is None:
            continue
        for s in level.swings:
            by_key[(s.kind, s.time)][name] = s
    merged: list[tuple[Swing, Swing | None]] = []
    for entry in by_key.values():
        ext = entry.get("external")
        first = min(entry.values(), key=lambda s: s.confirmed_at)
        merged.append((first, ext))
    return merged


def analyze_liquidity(
    candles: Sequence[Candle],
    internal: LevelStructure | None,
    external: LevelStructure | None,
    key_levels: Sequence[KeyLevel],
    trend: TrendDirection,
    cfg: LiquidityConfig,
) -> LiquidityResult:
    n = len(candles)
    if n == 0:
        return LiquidityResult(pools=[], events=[], dol=None)
    open_idx = {c.open_time: i for i, c in enumerate(candles)}
    close_idx = {c.close_time: i for i, c in enumerate(candles)}
    trs = true_ranges(candles)

    def atr(i: int) -> float:
        return atr_at(trs, max(i, 0), cfg.atr_period)

    activations: dict[int, list[_Pool]] = defaultdict(list)
    scope_upgrades: dict[int, list[_Pool]] = defaultdict(list)

    for swing, ext in _merge_swings(internal, external):
        confirm = close_idx.get(swing.confirmed_at)
        pivot = open_idx.get(swing.time)
        if confirm is None or pivot is None:
            continue
        high = swing.kind is SwingKind.HIGH
        pool = _Pool(
            id=f"SWING:{'BSL' if high else 'SSL'}:{swing.time.isoformat()}",
            type=LiquidityPoolType.SWING_HIGH if high else LiquidityPoolType.SWING_LOW,
            side=LiquiditySide.BSL if high else LiquiditySide.SSL,
            scope=LiquidityScope.INTERNAL,
            label=f"{'Swing high' if high else 'Swing low'} {swing.price}",
            price=swing.price,
            formed_index=pivot,
            formed_at=swing.time,
            known_at=swing.confirmed_at,
            source_times=[swing.time],
        )
        # Index n means "known as of the last close, not yet evaluated against any candle".
        activations[confirm + 1].append(pool)
        if ext is not None:
            ext_confirm = close_idx.get(ext.confirmed_at)
            if ext_confirm is not None:
                scope_upgrades[ext_confirm + 1].append(pool)

    for kl in key_levels:
        start = next((i for i, c in enumerate(candles) if c.open_time >= kl.known_at), None)
        if start is None:
            if kl.known_at > candles[-1].close_time:
                continue  # not known yet as of the last close
            start = n
        high = kl.type in HIGH_KEY_TYPES
        activations[start].append(
            _Pool(
                id=f"{kl.type.value}:{kl.period_start.isoformat()}",
                type=kl.type,
                side=LiquiditySide.BSL if high else LiquiditySide.SSL,
                scope=LiquidityScope.EXTERNAL,
                label=kl.label,
                price=kl.price,
                formed_index=None,
                formed_at=kl.period_start,
                known_at=kl.known_at,
            )
        )

    active: list[_Pool] = []
    events: list[LiquidityEvent] = []

    def emit(p: _Pool, kind: LiquidityEventType, i: int, state: LiquidityState) -> None:
        c = candles[i]
        p.state, p.state_index = state, i
        events.append(
            LiquidityEvent(
                id=f"{p.id}:{kind.value}:{c.open_time.isoformat()}",
                pool_id=p.id,
                pool_type=p.type,
                side=p.side,
                type=kind,
                price=p.price,
                time=c.open_time,
                extreme=c.high if p.is_high else c.low,
                close=c.close,
            )
        )

    def activate(i: int) -> None:
        at = candles[i].open_time if i < n else candles[-1].close_time
        for p in scope_upgrades.get(i, []):
            p.scope = LiquidityScope.EXTERNAL
            for eq in active:
                if p in eq.members:
                    eq.scope = LiquidityScope.EXTERNAL
        for p in activations.get(i, []):
            active.append(p)
            if p.formed_index is not None:
                _cluster(p, active, atr(i - 1), cfg, at)

    for i, c in enumerate(candles):
        activate(i)
        a = atr(i)
        for p in active:
            _evaluate(p, c, i, a, cfg, emit)
    activate(n)  # pools/scopes that became known with the close of the last candle

    as_of_atr = atr(n - 1)
    last_close = candles[-1].close
    pools = _snapshot(active, candles, last_close, as_of_atr, trend, cfg)
    return LiquidityResult(pools=pools, events=events, dol=select_dol(pools, as_of_atr, cfg))


def _cluster(
    new: _Pool, active: list[_Pool], atr: float, cfg: LiquidityConfig, activated_at: datetime
) -> None:
    tol = cfg.equal_tolerance_atr * atr
    assert new.formed_index is not None
    matches = [
        p
        for p in active
        if p is not new
        and p.formed_index is not None
        and p.type is new.type
        and p.untaken
        and abs(p.price - new.price) <= tol
        and abs(new.formed_index - p.formed_index) >= cfg.equal_min_bars_apart
    ]
    if not matches:
        return
    eq_type = LiquidityPoolType.EQH if new.is_high else LiquidityPoolType.EQL
    existing = next(
        (e for e in active if e.type is eq_type and e.untaken and any(m in e.members for m in matches)),
        None,
    )
    if existing is not None:
        existing.members.append(new)
        existing.source_times.append(new.formed_at)
        existing.price = max(existing.price, new.price) if new.is_high else min(existing.price, new.price)
        if new.scope is LiquidityScope.EXTERNAL:
            existing.scope = LiquidityScope.EXTERNAL
        existing.label = f"{eq_type.value} x{len(existing.members)}"
        return
    members = sorted([*matches, new], key=lambda p: p.formed_at)
    price = max(m.price for m in members) if new.is_high else min(m.price for m in members)
    active.append(
        _Pool(
            id=f"{eq_type.value}:{members[0].formed_at.isoformat()}:{activated_at.isoformat()}",
            type=eq_type,
            side=new.side,
            scope=LiquidityScope.EXTERNAL
            if any(m.scope is LiquidityScope.EXTERNAL for m in members)
            else LiquidityScope.INTERNAL,
            label=f"{eq_type.value} x{len(members)}",
            price=price,
            formed_index=None,
            formed_at=members[0].formed_at,
            known_at=activated_at,
            members=members,
            source_times=[m.formed_at for m in members],
        )
    )


Emit = Callable[["_Pool", LiquidityEventType, int, LiquidityState], None]


def _evaluate(p: _Pool, c: Candle, i: int, atr: float, cfg: LiquidityConfig, emit: Emit) -> None:
    sign = 1.0 if p.is_high else -1.0
    level = sign * p.price
    extreme = sign * (c.high if p.is_high else c.low)
    close = sign * c.close

    if p.state in UNTAKEN:
        if extreme > level:
            p.in_touch = False
            if close <= level:
                p.sweep_index, p.sweep_extreme = i, extreme
                emit(p, LiquidityEventType.SWEEP, i, LiquidityState.SWEPT)
            else:
                p.break_index, p.break_atr, p.best_close = i, atr, close
                emit(p, LiquidityEventType.BREAK, i, LiquidityState.BROKEN)
        elif extreme >= level - cfg.touch_tolerance_atr * atr:
            if not p.in_touch:
                p.touches += 1
                p.in_touch = True
                emit(p, LiquidityEventType.TOUCH, i, LiquidityState.TOUCHED)
        else:
            p.in_touch = False
    elif p.state is LiquidityState.SWEPT:
        if i - p.sweep_index <= cfg.sweep_failure_window_bars and close > p.sweep_extreme:
            emit(p, LiquidityEventType.SWEEP_FAILED, i, LiquidityState.RUN)
    elif (
        p.state is LiquidityState.BROKEN
        and i > p.break_index
        and i - p.break_index <= cfg.break_acceptance_window_bars
    ):
        p.best_close = max(p.best_close, close)
        if close <= level:
            emit(p, LiquidityEventType.RECLAIM, i, LiquidityState.RECLAIMED)
        elif p.best_close >= level + cfg.run_extension_atr * p.break_atr:
            emit(p, LiquidityEventType.RUN, i, LiquidityState.RUN)


def _snapshot(
    active: list[_Pool],
    candles: Sequence[Candle],
    last_close: float,
    atr: float,
    trend: TrendDirection,
    cfg: LiquidityConfig,
) -> list[LiquidityPool]:
    scores = magnet_scores(
        [p for p in active if p.untaken],
        last_close=last_close,
        atr=atr,
        trend=trend,
        cfg=cfg,
    )
    out: list[LiquidityPool] = []
    for p in active:
        distance = abs(p.price - last_close) / atr if atr > 0 else None
        state = p.state
        if p.untaken and distance is not None and distance <= cfg.approach_distance_atr:
            state = LiquidityState.APPROACHING
        out.append(
            LiquidityPool(
                id=p.id,
                type=p.type,
                side=p.side,
                scope=p.scope,
                label=p.label,
                price=p.price,
                formed_at=p.formed_at,
                known_at=p.known_at,
                source_times=list(p.source_times),
                state=state,
                touches=p.touches,
                state_changed_at=candles[p.state_index].open_time if p.state_index is not None else None,
                taken=not p.untaken,
                distance_atr=round(distance, 3) if distance is not None else None,
                magnet_score=scores.get(p.id),
            )
        )
    return out
