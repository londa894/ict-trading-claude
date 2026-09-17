"""Judas swing detection against the same trading day's COMPLETE Asian range (candle-sequential).

For each configured session instance (default LONDON, NY_AM) whose trading day has a COMPLETE Asian session
known before the window opens, scan closed candles inside the window in order:
  1. The first candle that trades beyond an Asian extreme decides:
       high > Asian high, close <= Asian high, low >= Asian low -> CANDIDATE, BEARISH (fake move up)
       low < Asian low, close >= Asian low, high <= Asian high  -> CANDIDATE, BULLISH (fake move down)
       anything else beyond (a close beyond, or both sides)     -> no Judas swing for this instance
  2. CANDIDATE, on a LATER candle in the window:
       close beyond the sweep extreme                         -> FAILED ("closed beyond the sweep extreme")
       close beyond the Asian midpoint in the Judas direction -> CONFIRMED
  3. Still CANDIDATE when the window has ended (as of the data) -> FAILED ("window ended").
A Judas swing describes session behaviour only. It never creates a trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import Direction, JudasStatus, SessionInstanceState, SessionName
from app.services.sessions.levels import closed_until
from app.services.sessions.models import JudasSwing, SessionConfig, SessionInstance


def detect_judas(
    candles: Sequence[Candle], instances: Sequence[SessionInstance], as_of: datetime, cfg: SessionConfig
) -> list[JudasSwing]:
    closed = closed_until(candles, as_of)
    asia = {
        i.trading_day: i
        for i in instances
        if i.session is SessionName.ASIA and i.state is SessionInstanceState.COMPLETE
    }
    out: list[JudasSwing] = []
    for inst in instances:
        if inst.session not in cfg.judas_sessions or inst.state is SessionInstanceState.NOT_STARTED:
            continue
        a = asia.get(inst.trading_day)
        if a is None or a.known_at is None or a.known_at > inst.start:
            continue
        assert a.high is not None and a.low is not None and a.midpoint is not None
        swing = _scan(inst, a, [c for c in closed if inst.start <= c.open_time and c.close_time <= inst.end])
        if swing is None:
            continue
        if swing.status is JudasStatus.CANDIDATE and as_of >= inst.end:
            swing = swing.model_copy(
                update={"status": JudasStatus.FAILED, "resolved_at": inst.end, "detail": "window ended"}
            )
        out.append(swing)
    return out


def _scan(inst: SessionInstance, a: SessionInstance, window: list[Candle]) -> JudasSwing | None:
    assert a.high is not None and a.low is not None and a.midpoint is not None
    swing: JudasSwing | None = None
    for c in window:
        if swing is None:
            above, below = c.high > a.high, c.low < a.low
            if not above and not below:
                continue
            if above and not below and c.close <= a.high:
                direction, extreme = Direction.BEARISH, c.high
            elif below and not above and c.close >= a.low:
                direction, extreme = Direction.BULLISH, c.low
            else:
                return None
            swing = JudasSwing(
                id=f"JUDAS:{inst.id}",
                trading_day=inst.trading_day,
                session=inst.session,
                direction=direction,
                status=JudasStatus.CANDIDATE,
                asian_high=a.high,
                asian_low=a.low,
                asian_midpoint=a.midpoint,
                sweep_time=c.open_time,
                sweep_extreme=extreme,
                resolved_at=None,
                detail="swept the Asian " + ("high" if direction is Direction.BEARISH else "low"),
            )
            continue
        bearish = swing.direction is Direction.BEARISH
        if (c.close > swing.sweep_extreme) if bearish else (c.close < swing.sweep_extreme):
            return swing.model_copy(
                update={
                    "status": JudasStatus.FAILED,
                    "resolved_at": c.close_time,
                    "detail": "closed beyond the sweep extreme",
                }
            )
        if (c.close < a.midpoint) if bearish else (c.close > a.midpoint):
            return swing.model_copy(
                update={
                    "status": JudasStatus.CONFIRMED,
                    "resolved_at": c.close_time,
                    "detail": "closed beyond the Asian midpoint",
                }
            )
    return swing
