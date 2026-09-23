"""Multi-session sweep confluence (spec section 5), candle-sequential, same trading day.

The pattern: London fails to take out Asia's extreme (London's low stays above Asia's low, or its high
below Asia's high), then a single New York candle sweeps BOTH levels in one move (its range spans from
at/above the nearer London level through the farther Asia level). That one-move raid of stacked
session liquidity is flagged as an elevated-confidence sweep. It describes session behaviour only and
never creates a trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import LiquiditySide, SessionInstanceState, SessionName
from app.services.sessions.levels import closed_until
from app.services.sessions.models import SessionConfig, SessionInstance, SessionSweepConfluence

_NY_SESSIONS = (SessionName.NY_AM, SessionName.NY_PM)


def detect_sweep_confluence(
    candles: Sequence[Candle], instances: Sequence[SessionInstance], as_of: datetime, cfg: SessionConfig
) -> list[SessionSweepConfluence]:
    closed = closed_until(candles, as_of)
    complete = {
        (i.trading_day, i.session): i
        for i in instances
        if i.state is SessionInstanceState.COMPLETE and i.known_at is not None
    }
    out: list[SessionSweepConfluence] = []
    seen: set[tuple[object, LiquiditySide]] = set()  # first NY session to sweep wins per (day, side)
    ny = sorted(
        (
            i
            for i in instances
            if i.session in _NY_SESSIONS and i.state is not SessionInstanceState.NOT_STARTED
        ),
        key=lambda i: i.start,
    )
    for inst in ny:
        asia = complete.get((inst.trading_day, SessionName.ASIA))
        london = complete.get((inst.trading_day, SessionName.LONDON))
        if asia is None or london is None:
            continue
        # Both prior sessions must be complete before New York opens.
        if london.known_at is None or london.known_at > inst.start:
            continue
        if None in (asia.high, asia.low, london.high, london.low):
            continue
        window = [c for c in closed if inst.start <= c.open_time and c.close_time <= inst.end]
        conf = _scan(inst, asia, london, window)
        if conf is not None and (inst.trading_day, conf.side) not in seen:
            seen.add((inst.trading_day, conf.side))
            out.append(conf)
    return out


def _scan(
    inst: SessionInstance, asia: SessionInstance, london: SessionInstance, window: Sequence[Candle]
) -> SessionSweepConfluence | None:
    assert asia.high is not None and asia.low is not None
    assert london.high is not None and london.low is not None
    london_held_low = london.low > asia.low  # London failed to take Asia's low
    london_held_high = london.high < asia.high  # London failed to take Asia's high
    for c in window:
        # SSL: one candle spans from at/above London's low down through Asia's low (both lows swept).
        if london_held_low and c.high >= london.low and c.low <= asia.low:
            return _make(inst, LiquiditySide.SSL, asia.low, london.low, c.open_time, c.low)
        # BSL: one candle spans from at/below London's high up through Asia's high (both highs swept).
        if london_held_high and c.low <= london.high and c.high >= asia.high:
            return _make(inst, LiquiditySide.BSL, asia.high, london.high, c.open_time, c.high)
    return None


def _make(
    inst: SessionInstance,
    side: LiquiditySide,
    asia_level: float,
    london_level: float,
    sweep_time: datetime,
    sweep_extreme: float,
) -> SessionSweepConfluence:
    which = "lows" if side is LiquiditySide.SSL else "highs"
    return SessionSweepConfluence(
        id=f"SWEEPCONF:{inst.id}:{side.value}",
        trading_day=inst.trading_day,
        session=inst.session,
        side=side,
        asia_level=asia_level,
        london_level=london_level,
        sweep_time=sweep_time,
        sweep_extreme=sweep_extreme,
        detail=f"New York swept Asia's and London's {which} in one move after London held",
    )
