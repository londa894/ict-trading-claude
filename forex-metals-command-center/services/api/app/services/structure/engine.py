"""Sequential market-structure engine (BOS / CHoCH / MSS, protected levels, state).

Candles are processed strictly in order. At candle i the engine may only use swings whose
confirmation candle closed BEFORE i (confirm_index < i). This makes every event reproducible from
the prefix of candles up to the event — no lookahead.

Break targets
- target high = the most recent confirmed swing high, if unbroken (older swing highs are superseded)
- target low  = the most recent confirmed swing low, if unbroken
- protected low  (in a BULLISH trend) / protected high (in a BEARISH trend): set when a BOS/MSS occurs,
  to the latest unbroken opposite swing at that moment (falling back to the previous protected level
  if the latest is already broken).

Classification of a CONFIRMED break (close beyond the level; default rule CANDLE_CLOSE)
- counter-trend close through the protected level          -> MSS  (trend flips)
- counter-trend close through a non-protected target swing -> CHOCH (state TRANSITIONING, trend kept)
- close through a target swing in the trend direction      -> BOS  (clears TRANSITIONING)
- first break with no trend                                -> BOS with trend_before=NONE
- one candle closing through both sides                    -> both events flagged ambiguous; trend NONE

Wick-only crossings (high/low beyond, close not beyond) produce POTENTIAL events once per swing and
never change the trend.

MSS here is purely structural. Its liquidity/displacement qualifiers stay NOT_EVALUATED until the
Liquidity (Phase 3) and Displacement (Phase 4) engines exist.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.candle import Candle
from app.domain.enums import (
    BreakConfirmation,
    Direction,
    QualifierStatus,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
    StructureState,
    SwingKind,
    SwingLabel,
    TrendDirection,
)
from app.services.structure.models import LevelStructure, StructureConfig, StructureEvent, Swing
from app.services.structure.swings import RawSwing, detect_swings

_RANGING_LABEL_PAIRS = {
    (SwingLabel.LH, SwingLabel.HL),
    (SwingLabel.LH, SwingLabel.EL),
    (SwingLabel.EH, SwingLabel.HL),
    (SwingLabel.EH, SwingLabel.EL),
    (SwingLabel.HH, SwingLabel.LL),
}


@dataclass
class _RawEvent:
    index: int
    type: StructureEventType
    direction: Direction
    status: StructureEventStatus
    swing: RawSwing
    trend_before: TrendDirection
    ambiguous: bool = False
    id: str = ""


@dataclass
class _State:
    trend: TrendDirection = TrendDirection.NONE
    transitioning: bool = False
    latest: dict[SwingKind, RawSwing | None] = field(
        default_factory=lambda: {SwingKind.HIGH: None, SwingKind.LOW: None}
    )
    protected: dict[SwingKind, RawSwing | None] = field(
        default_factory=lambda: {SwingKind.HIGH: None, SwingKind.LOW: None}
    )
    last_break_index: int | None = None


def _unbroken(s: RawSwing | None) -> RawSwing | None:
    return s if s is not None and not s.is_broken else None


def _classify(trend: TrendDirection, direction: Direction, through_protected: bool) -> StructureEventType:
    if through_protected:
        return StructureEventType.MSS
    if trend is TrendDirection.NONE or trend.value == direction.value:
        return StructureEventType.BOS
    return StructureEventType.CHOCH


def analyze_level(candles: Sequence[Candle], level: StructureLevel, cfg: StructureConfig) -> LevelStructure:
    """Analyse CLOSED candles (ascending, validated) for one structure level."""
    pivot = cfg.pivot_length[level]
    raw_swings = detect_swings(candles, pivot, cfg.equal_tolerance_atr, cfg.atr_period)
    by_confirm: dict[int, list[RawSwing]] = defaultdict(list)
    for s in raw_swings:
        by_confirm[s.confirm_index].append(s)

    st = _State()
    events: list[_RawEvent] = []
    potential_done: set[tuple[int, SwingKind]] = set()

    def emit(ev: _RawEvent) -> None:
        c = candles[ev.index]
        ev.id = (
            f"{level.value}:{c.open_time.isoformat()}:{ev.type.value}:{ev.direction.value}:"
            f"{ev.status.value}:{ev.swing.kind.value}:{candles[ev.swing.index].open_time.isoformat()}"
        )
        events.append(ev)

    def break_swings(ev: _RawEvent, swings: list[RawSwing | None]) -> None:
        for s in swings:
            if s is not None and not s.is_broken:
                s.broken_index = ev.index
                s.broken_by = ev.id

    def _emit_potentials(
        i: int,
        extreme: float,
        direction: Direction,
        candidates: tuple[tuple[RawSwing | None, bool], ...],
    ) -> None:
        seen: set[int] = set()
        for swing, through_protected in candidates:
            if swing is None or swing.is_broken or id(swing) in seen:
                continue
            seen.add(id(swing))
            crossed = extreme > swing.price if direction is Direction.BULLISH else extreme < swing.price
            if not crossed or (swing.index, swing.kind) in potential_done:
                continue
            potential_done.add((swing.index, swing.kind))
            emit(
                _RawEvent(
                    i,
                    _classify(trend_before, direction, through_protected),
                    direction,
                    StructureEventStatus.POTENTIAL,
                    swing,
                    trend_before,
                )
            )

    trend_before = TrendDirection.NONE
    for i, c in enumerate(candles):
        trend_before = st.trend
        target_high = _unbroken(st.latest[SwingKind.HIGH])
        target_low = _unbroken(st.latest[SwingKind.LOW])
        prot_high = _unbroken(st.protected[SwingKind.HIGH]) if st.trend is TrendDirection.BEARISH else None
        prot_low = _unbroken(st.protected[SwingKind.LOW]) if st.trend is TrendDirection.BULLISH else None

        bull_target = target_high is not None and c.close > target_high.price
        bull_mss = prot_high is not None and c.close > prot_high.price
        bear_target = target_low is not None and c.close < target_low.price
        bear_mss = prot_low is not None and c.close < prot_low.price
        bull = bull_target or bull_mss
        bear = bear_target or bear_mss

        confirmed: list[tuple[_RawEvent, list[RawSwing | None]]] = []
        if bull:
            key = prot_high if bull_mss else target_high
            assert key is not None
            ev = _RawEvent(
                i,
                _classify(st.trend, Direction.BULLISH, bull_mss),
                Direction.BULLISH,
                StructureEventStatus.CONFIRMED,
                key,
                st.trend,
                ambiguous=bear,
            )
            confirmed.append((ev, [target_high if bull_target else None, prot_high if bull_mss else None]))
        if bear:
            key = prot_low if bear_mss else target_low
            assert key is not None
            ev = _RawEvent(
                i,
                _classify(st.trend, Direction.BEARISH, bear_mss),
                Direction.BEARISH,
                StructureEventStatus.CONFIRMED,
                key,
                st.trend,
                ambiguous=bull,
            )
            confirmed.append((ev, [target_low if bear_target else None, prot_low if bear_mss else None]))

        for ev, swings in confirmed:
            emit(ev)
            break_swings(ev, swings)

        if bull and bear:
            st.trend, st.transitioning = TrendDirection.NONE, False
            st.protected = {SwingKind.HIGH: None, SwingKind.LOW: None}
            st.last_break_index = i
        elif confirmed:
            ev = confirmed[0][0]
            st.last_break_index = i
            if ev.type is StructureEventType.CHOCH:
                st.transitioning = True
            else:  # BOS or MSS: trend (re)established in the break direction
                new_trend = TrendDirection(ev.direction.value)
                opposite = SwingKind.LOW if new_trend is TrendDirection.BULLISH else SwingKind.HIGH
                same = SwingKind.HIGH if opposite is SwingKind.LOW else SwingKind.LOW
                previous_protected = _unbroken(st.protected[opposite]) if st.trend is new_trend else None
                st.protected = {
                    opposite: _unbroken(st.latest[opposite]) or previous_protected,
                    same: None,
                }
                st.trend, st.transitioning = new_trend, False

        # Wick-only crossings -> POTENTIAL (once per swing), classified against the pre-candle trend.
        if not bull:
            _emit_potentials(i, c.high, Direction.BULLISH, ((prot_high, True), (target_high, False)))
        if not bear:
            _emit_potentials(i, c.low, Direction.BEARISH, ((prot_low, True), (target_low, False)))

        for s in by_confirm.get(i, []):
            st.latest[s.kind] = s

    return _build_output(candles, level, pivot, raw_swings, events, st, cfg)


def _state(n: int, st: _State, cfg: StructureConfig, level: StructureLevel) -> StructureState:
    if n < cfg.min_candles or st.trend is TrendDirection.NONE:
        return StructureState.UNCLEAR
    if st.transitioning:
        return StructureState.TRANSITIONING
    high, low = st.latest[SwingKind.HIGH], st.latest[SwingKind.LOW]
    # Compression/expansion only counts for swings formed after the last break; older labels describe
    # the structure that the break already resolved.
    after_break = st.last_break_index is not None and all(
        s is not None and s.confirm_index > st.last_break_index for s in (high, low)
    )
    if (
        after_break
        and high is not None
        and low is not None
        and (high.label, low.label) in _RANGING_LABEL_PAIRS
    ):
        return StructureState.RANGING
    if st.last_break_index is None or (n - 1 - st.last_break_index) > cfg.ranging_bars_without_break[level]:
        return StructureState.RANGING
    return StructureState.BULLISH if st.trend is TrendDirection.BULLISH else StructureState.BEARISH


def _build_output(
    candles: Sequence[Candle],
    level: StructureLevel,
    pivot: int,
    raw_swings: list[RawSwing],
    raw_events: list[_RawEvent],
    st: _State,
    cfg: StructureConfig,
) -> LevelStructure:
    def swing_id(s: RawSwing) -> str:
        return f"{level.value}:{s.kind.value}:{candles[s.index].open_time.isoformat()}"

    def to_swing(s: RawSwing) -> Swing:
        return Swing(
            id=swing_id(s),
            level=level,
            kind=s.kind,
            label=s.label,
            price=s.price,
            time=candles[s.index].open_time,
            confirmed_at=candles[s.confirm_index].close_time,
            broken_at=candles[s.broken_index].open_time if s.broken_index is not None else None,
            broken_by=s.broken_by,
        )

    events = [
        StructureEvent(
            id=e.id,
            level=level,
            type=e.type,
            direction=e.direction,
            status=e.status,
            confirmation=BreakConfirmation.CANDLE_CLOSE
            if e.status is StructureEventStatus.CONFIRMED
            else BreakConfirmation.WICK_ONLY,
            price=e.swing.price,
            time=candles[e.index].open_time,
            broken_swing_id=swing_id(e.swing),
            broken_swing_time=candles[e.swing.index].open_time,
            trend_before=e.trend_before,
            ambiguous=e.ambiguous,
            liquidity_qualifier=QualifierStatus.NOT_EVALUATED,
            displacement_qualifier=QualifierStatus.NOT_EVALUATED,
        )
        for e in raw_events
    ]
    n = len(candles)
    prot_high = _unbroken(st.protected[SwingKind.HIGH])
    prot_low = _unbroken(st.protected[SwingKind.LOW])
    return LevelStructure(
        level=level,
        pivot_length=pivot,
        state=_state(n, st, cfg, level),
        trend=st.trend,
        swings=[to_swing(s) for s in raw_swings],
        events=events,
        protected_high=to_swing(prot_high) if prot_high else None,
        protected_low=to_swing(prot_low) if prot_low else None,
        bars_since_last_break=(n - 1 - st.last_break_index) if st.last_break_index is not None else None,
    )
