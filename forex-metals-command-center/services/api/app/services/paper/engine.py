"""Paper simulation (pure, sequential, closed bars only).

Candles are treated as mid prices; hs = spread / 2, s = +1 BULLISH / -1 BEARISH. Entries buy the ask
(mid + hs) for longs and sell the bid for shorts; exits use the other side.
Fills happen only on bars opening at or after creation (no lookahead):
  MARKET: open + s*hs + s*slippage on the first such bar.
  LIMIT: when the entry-side price reaches the limit; filled at the better of the limit and the entry-side
  open. An unfilled LIMIT expires after pendingExpiryBars bars.
Exits (exit-side prices):
  gap at the open through the stop -> filled at that open - s*slippage; gap through the target -> that open;
  otherwise stop touched -> stop - s*slippage, target touched -> target. Both in one bar -> STOP
  (conservative) with ambiguous=True. On a bar where a LIMIT filled intrabar only the stop can trigger.
MFE/MAE track the favourable/adverse exit-side extremes of every bar while open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import Direction, ExitReason, PaperEntryType, PaperEventType, PaperStatus
from app.services.paper.models import AssumedCosts

E = PaperEventType


@dataclass(frozen=True)
class SimParams:
    direction: Direction
    entry_type: PaperEntryType
    limit_price: float | None
    stop: float
    target: float
    created_at: datetime
    costs: AssumedCosts
    pending_expiry_bars: int

    @property
    def s(self) -> int:
        return 1 if self.direction is Direction.BULLISH else -1


@dataclass(frozen=True)
class SimState:
    status: PaperStatus = PaperStatus.PENDING
    fill_price: float | None = None
    filled_at: datetime | None = None
    exit_price: float | None = None
    exited_at: datetime | None = None
    exit_reason: ExitReason | None = None
    pending_bars: int = 0
    best: float | None = None  # most favourable exit-side price while open
    worst: float | None = None  # most adverse exit-side price while open
    processed_through: datetime | None = None


@dataclass(frozen=True)
class SimEvent:
    type: PaperEventType
    at: datetime
    price: float | None
    ambiguous: bool
    detail: str


def _r(p: SimParams, price: float) -> float:
    return round(price, 10)


def step(p: SimParams, state: SimState, bar: Candle) -> tuple[SimState, list[SimEvent]]:
    if state.status not in (PaperStatus.PENDING, PaperStatus.OPEN) or not bar.is_closed:
        return state, []
    if state.processed_through is not None and bar.open_time <= state.processed_through:
        return state, []
    if bar.open_time < p.created_at:
        return state, []
    s, hs, slip = p.s, p.costs.spread / 2, p.costs.slippage
    events: list[SimEvent] = []
    intrabar_fill = False

    if state.status is PaperStatus.PENDING:
        entry_open = bar.open + s * hs
        if p.entry_type is PaperEntryType.MARKET:
            fill = _r(p, entry_open + s * slip)
            state = replace(state, status=PaperStatus.OPEN, fill_price=fill, filled_at=bar.open_time)
            events.append(
                SimEvent(
                    E.FILLED, bar.open_time, fill, False, "market entry at the bar open (spread + slippage)"
                )
            )
        else:
            assert p.limit_price is not None
            favourable_entry = (bar.low + hs) if s > 0 else (bar.high - hs)
            if s * (favourable_entry - p.limit_price) <= 0:
                gapped = s * (entry_open - p.limit_price) <= 0
                fill = _r(p, entry_open if gapped else p.limit_price)
                intrabar_fill = not gapped
                state = replace(state, status=PaperStatus.OPEN, fill_price=fill, filled_at=bar.open_time)
                how = "at the open (gapped through the limit)" if gapped else "at the limit"
                events.append(SimEvent(E.FILLED, bar.open_time, fill, False, f"limit entry {how}"))
            else:
                pending = state.pending_bars + 1
                if pending >= p.pending_expiry_bars:
                    events.append(
                        SimEvent(
                            E.EXPIRED, bar.open_time, None, False, f"limit not reached in {pending} bars"
                        )
                    )
                    return replace(
                        state,
                        status=PaperStatus.EXPIRED,
                        pending_bars=pending,
                        processed_through=bar.open_time,
                    ), events
                return replace(state, pending_bars=pending, processed_through=bar.open_time), events

    # --- open position on this bar --------------------
    exit_open = bar.open - s * hs
    favourable = (bar.high - hs) if s > 0 else (bar.low + hs)
    adverse = (bar.low - hs) if s > 0 else (bar.high + hs)
    best = (
        favourable
        if state.best is None
        else (max(state.best, favourable) if s > 0 else min(state.best, favourable))
    )
    worst = (
        adverse
        if state.worst is None
        else (min(state.worst, adverse) if s > 0 else max(state.worst, adverse))
    )
    state = replace(state, best=best, worst=worst, processed_through=bar.open_time)

    def close(
        kind: PaperEventType, reason: ExitReason, price: float, ambiguous: bool, detail: str
    ) -> SimState:
        events.append(SimEvent(kind, bar.open_time, _r(p, price), ambiguous, detail))
        return replace(
            state,
            status=PaperStatus.CLOSED,
            exit_price=_r(p, price),
            exited_at=bar.close_time,
            exit_reason=reason,
        )

    if not intrabar_fill:
        if s * (exit_open - p.stop) <= 0:
            return close(
                E.STOP_HIT,
                ExitReason.STOP,
                exit_open - s * slip,
                False,
                "gapped through the stop at the open",
            ), events
        if s * (exit_open - p.target) >= 0:
            return close(
                E.TARGET_HIT, ExitReason.TARGET, exit_open, False, "gapped through the target at the open"
            ), events
    stop_hit = s * (adverse - p.stop) <= 0
    target_hit = not intrabar_fill and s * (favourable - p.target) >= 0
    if stop_hit:
        ambiguous = target_hit or intrabar_fill
        detail = "stop and target touched in the same bar: stop assumed" if target_hit else "stop touched"
        if intrabar_fill:
            detail = "filled and stopped in the same bar (order unknown: stop assumed)"
        return close(E.STOP_HIT, ExitReason.STOP, p.stop - s * slip, ambiguous, detail), events
    if target_hit:
        return close(E.TARGET_HIT, ExitReason.TARGET, p.target, False, "target touched"), events
    return state, events


def simulate(p: SimParams, state: SimState, bars: Sequence[Candle]) -> tuple[SimState, list[SimEvent]]:
    events: list[SimEvent] = []
    for bar in sorted(bars, key=lambda b: b.open_time):
        state, new = step(p, state, bar)
        events.extend(new)
        if state.status not in (PaperStatus.PENDING, PaperStatus.OPEN):
            break
    return state, events


def manual_close_price(p: SimParams, last_bar: Candle) -> float:
    """Exit at the last closed bar's close on the exit side, paying slippage."""
    return _r(p, last_bar.close - p.s * (p.costs.spread / 2) - p.s * p.costs.slippage)
