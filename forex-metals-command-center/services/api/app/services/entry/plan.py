"""Entry plan construction and chase protection (pure functions).

For a BULLISH setup (mirror for BEARISH):
  stop   = protective extreme - stopBufferAtr x ATR
  risk   = entry - stop (must be > 0)
  TP1    = the setup's target liquidity (DOL); TP2 / TP3 = the next untaken target-side pools beyond TP1
           that are more than targetDistinctAtr x ATR apart from the previous target (nearest first)
  R:R_n  = (TP_n - entry) / risk
Chase protection (ENTRY_MISSED, do not chase):
  at confirmation  TP1 closer than minTp1Atr x ATR, or R:R1 < minRr for the mode
  after it         TP1 traded before an authorized entry, or R:R from the latest close < minRr
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.enums import Direction, EntryMode, EntryModel
from app.services.entry.models import EntryConfig, EntryPlan


def build_plan(
    *,
    direction: Direction,
    model: EntryModel,
    mode: EntryMode,
    confirmed_at: datetime,
    zone_id: str,
    entry: float,
    protective: float,
    tp1: float,
    further_targets: Sequence[float],
    atr: float,
    cfg: EntryConfig,
    research_only: bool = False,
) -> tuple[EntryPlan | None, str | None]:
    """Returns (plan, None) when valid, or (None, reason) when the entry must be treated as missed."""
    bullish = direction is Direction.BULLISH
    sign = 1.0 if bullish else -1.0
    stop = protective - sign * cfg.stop_buffer_atr * atr
    risk = sign * (entry - stop)
    if risk <= 0:
        return None, "entry is beyond the stop"
    reward1 = sign * (tp1 - entry)
    if reward1 < cfg.min_tp1_atr * atr:
        return None, f"TP1 closer than {cfg.min_tp1_atr} ATR (do not chase)"
    rr1 = reward1 / risk
    if rr1 < cfg.min_rr:
        return None, f"R:R {rr1:.2f} below {cfg.min_rr} (do not chase)"
    beyond = sorted((p for p in further_targets if sign * (p - tp1) > 0), key=lambda p: sign * (p - tp1))
    extra: list[float] = []
    last = tp1
    for p in beyond:
        if sign * (p - last) > cfg.target_distinct_atr * atr:
            extra.append(p)
            last = p
        if len(extra) == 2:
            break
    tp2 = extra[0] if extra else None
    tp3 = extra[1] if len(extra) > 1 else None

    def rr(tp: float | None) -> float | None:
        return round(sign * (tp - entry) / risk, 2) if tp is not None else None

    return (
        EntryPlan(
            model=model,
            mode=mode,
            direction=direction,
            confirmed_at=confirmed_at,
            zone_id=zone_id,
            entry=round(entry, 6),
            stop=round(stop, 6),
            risk=round(risk, 6),
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            rr1=round(rr1, 2),
            rr2=rr(tp2),
            rr3=rr(tp3),
            min_rr=cfg.min_rr,
            research_only=research_only,
            detail=f"{model.value} ({mode.value}); not authorized (risk check and news gate)",
        ),
        None,
    )


def chase_reason(plan: EntryPlan, close: float) -> str | None:
    """After confirmation: R:R measured from the latest close collapsed below the mode minimum."""
    sign = 1.0 if plan.direction is Direction.BULLISH else -1.0
    if sign * (close - plan.entry) <= 0:
        return None
    risk = sign * (close - plan.stop)
    if risk <= 0:
        return None
    rr_now = sign * (plan.tp1 - close) / risk
    if rr_now < plan.min_rr:
        return f"price moved away; R:R now {rr_now:.2f} below {plan.min_rr} (do not chase)"
    return None
