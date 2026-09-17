"""Average Daily Range and expansion state (no lookahead).

ADR = mean(high - low) of the last `periodDays` CLOSED New York-close D1 candles that closed at or before the
start of the current trading day. Fewer candles -> no ADR (never a partial average). The current range is the
current trading day's range from closed source candles; pct used = current / ADR x 100.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import ExpansionState, Timeframe
from app.services.sessions.clock import trading_day_bounds
from app.services.sessions.levels import current_day_range
from app.services.sessions.models import AdrConfig, AdrState


def classify_expansion(pct: float, cfg: AdrConfig) -> ExpansionState:
    if pct < cfg.consolidating_below_pct:
        return ExpansionState.CONSOLIDATING
    if pct < cfg.early_below_pct:
        return ExpansionState.EARLY
    if pct < cfg.active_below_pct:
        return ExpansionState.ACTIVE
    if pct < cfg.late_below_pct:
        return ExpansionState.LATE
    return ExpansionState.EXHAUSTED


def adr_state(
    d1: Sequence[Candle], source: Sequence[Candle], as_of: datetime, cfg: AdrConfig
) -> AdrState | None:
    day, current = current_day_range(source, as_of)
    day_start, _ = trading_day_bounds(day)
    prior = sorted(
        (c for c in d1 if c.timeframe is Timeframe.D1 and c.is_closed and c.close_time <= day_start),
        key=lambda c: c.open_time,
    )[-cfg.period_days :]
    if len(prior) < cfg.period_days:
        return None
    adr = sum(c.high - c.low for c in prior) / len(prior)
    if adr <= 0:
        return None
    pct = round(current / adr * 100, 2) if current is not None else None
    return AdrState(
        adr=round(adr, 6),
        period_days=cfg.period_days,
        current_range=current,
        pct_used=pct,
        expansion=classify_expansion(pct, cfg) if pct is not None else None,
    )
