"""Displacement grading (deterministic, candle-sequential, no lookahead).

A candle QUALIFIES when range > 0 and body/range >= minBodyPct; its direction is BULLISH if close > open,
BEARISH if close < open. The LEG at candle i is the run of consecutive qualifying same-direction candles
ending at i, capped to the last `maxLegCandles`.

    magnitude = |close[i] - open[leg start]| / ATR_before(leg start)

ATR_before(s) averages the true ranges of the `atrPeriod` candles BEFORE s, so the move never inflates its
own yardstick. No prior candle -> no grade. Grades: WEAK/MODERATE/STRONG/EXCEPTIONAL by `gradesAtr`;
STRONG and above require an average body % >= `strongMinAvgBodyPct`, otherwise the grade is capped at
MODERATE.

An event is emitted at candle i when its leg reaches a grade for the first time in the current run, or a
higher grade than already emitted in that run. Emitted events never change.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.candle import Candle
from app.domain.enums import Direction, DisplacementGrade
from app.services.pd_arrays.models import DisplacementEvent, PdArrayConfig
from app.services.structure.swings import true_ranges


def atr_before(trs: Sequence[float], start: int, period: int) -> float | None:
    window = trs[max(0, start - period) : start]
    return sum(window) / len(window) if window else None


def _direction(c: Candle, min_body_pct: float) -> Direction | None:
    rng = c.high - c.low
    if rng <= 0 or c.close == c.open:
        return None
    if abs(c.close - c.open) / rng < min_body_pct:
        return None
    return Direction.BULLISH if c.close > c.open else Direction.BEARISH


def grade_for(magnitude: float, avg_body_pct: float, cfg: PdArrayConfig) -> DisplacementGrade | None:
    grade: DisplacementGrade | None = None
    for g in DisplacementGrade:  # ascending
        if magnitude >= cfg.grades_atr[g]:
            grade = g
    if (
        grade is not None
        and grade.rank > DisplacementGrade.MODERATE.rank
        and avg_body_pct < cfg.strong_min_avg_body_pct
    ):
        grade = DisplacementGrade.MODERATE
    return grade


def detect_displacements(candles: Sequence[Candle], cfg: PdArrayConfig) -> list[DisplacementEvent]:
    trs = true_ranges(candles)
    events: list[DisplacementEvent] = []
    run_start = 0
    run_dir: Direction | None = None
    emitted_rank = -1
    for i, c in enumerate(candles):
        d = _direction(c, cfg.min_body_pct)
        if d is None or d is not run_dir:
            run_start, run_dir, emitted_rank = i, d, -1
        if d is None:
            continue
        start = max(run_start, i - cfg.max_leg_candles + 1)
        atr = atr_before(trs, start, cfg.atr_period)
        if not atr:
            continue
        leg = candles[start : i + 1]
        magnitude = abs(c.close - candles[start].open) / atr
        avg_body = sum(abs(x.close - x.open) / (x.high - x.low) for x in leg) / len(leg)
        grade = grade_for(magnitude, avg_body, cfg)
        if grade is None or grade.rank <= emitted_rank:
            continue
        emitted_rank = grade.rank
        events.append(
            DisplacementEvent(
                id=f"DISP:{d.value}:{c.open_time.isoformat()}:{grade.value}",
                direction=d,
                grade=grade,
                magnitude_atr=round(magnitude, 3),
                leg_start=candles[start].open_time,
                time=c.open_time,
                candle_count=len(leg),
                avg_body_pct=round(avg_body, 3),
            )
        )
    return events
