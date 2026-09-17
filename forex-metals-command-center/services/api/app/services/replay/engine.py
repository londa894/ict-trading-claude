"""Replay quiz and masking (pure).

Questions are built only from data known at the cursor; grading reads the bars revealed when the cursor moves.
NEXT_BARS_DIRECTION: close after N bars vs the reference close (equal close -> VOID).
LEVEL_FIRST: reference close +/- 1 ATR (mean true range of the last `period` closed bars); the first bar
touching a level decides; a bar touching both -> VOID; neither touched within N bars -> NEITHER.
SETUP_PROGRESS: the open setup at the cursor after N bars: CONFIRMED_PLAN (entry plan), INVALIDATED_OR_EXPIRED
(INVALIDATED / EXPIRED / ENTRY_MISSED) or STILL_OPEN; a setup no longer reported -> VOID.
BLIND masking multiplies prices by a hidden scale and shifts times by a hidden whole number of weeks, so
weekday and session timing are preserved while dates and levels are not recognisable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

from app.domain.candle import Candle
from app.domain.enums import QuizGrade, QuizQuestionType, SetupState
from app.services.analytics.engine import label_for
from app.services.analytics.models import AnalyticsConfig
from app.services.candles.service import ChartCandle
from app.services.replay.models import QuizQuestion, QuizResult, QuizScore

SKIP = "SKIP"
DIRECTION_OPTIONS = ["UP", "DOWN"]
LEVEL_OPTIONS = ["UPPER_LEVEL", "LOWER_LEVEL", "NEITHER"]
SETUP_OPTIONS = ["CONFIRMED_PLAN", "INVALIDATED_OR_EXPIRED", "STILL_OPEN"]
ENDED = frozenset({SetupState.INVALIDATED.value, SetupState.EXPIRED.value, SetupState.ENTRY_MISSED.value})
ROTATION = [
    QuizQuestionType.NEXT_BARS_DIRECTION,
    QuizQuestionType.LEVEL_FIRST,
    QuizQuestionType.SETUP_PROGRESS,
]


def mean_true_range(bars: Sequence[Candle], period: int) -> float | None:
    closed = [b for b in bars if b.is_closed]
    if len(closed) < period + 1:
        return None
    window = closed[-(period + 1) :]
    ranges = [max(b.high, prev.close) - min(b.low, prev.close) for prev, b in pairwise(window)]
    return sum(ranges) / len(ranges)


def make_question(
    qid: str,
    index: int,
    known: Sequence[Candle],
    open_setup_id: str | None,
    horizon: int,
    atr_period: int,
) -> tuple[QuizQuestion, dict[str, object]] | None:
    """The question and its private grading parameters, built from `known` (closed bars up to the cursor)."""
    closed = [b for b in known if b.is_closed]
    if not closed:
        return None
    ref = closed[-1].close
    atr = mean_true_range(closed, atr_period)
    order = ROTATION[index % len(ROTATION) :] + ROTATION[: index % len(ROTATION)]
    for qtype in order:
        if qtype is QuizQuestionType.NEXT_BARS_DIRECTION:
            prompt = f"Will the close {horizon} bars from now be above or below the current close?"
            return (
                QuizQuestion(
                    id=qid,
                    type=qtype,
                    prompt=prompt,
                    options=[*DIRECTION_OPTIONS, SKIP],
                    horizon_bars=horizon,
                    reference_price=ref,
                    upper_level=None,
                    lower_level=None,
                ),
                {"ref": ref},
            )
        if qtype is QuizQuestionType.LEVEL_FIRST and atr:
            upper, lower = round(ref + atr, 6), round(ref - atr, 6)
            prompt = (
                f"Within {horizon} bars, which is reached first: one ATR above ({upper}) "
                f"or one ATR below ({lower})?"
            )
            return (
                QuizQuestion(
                    id=qid,
                    type=qtype,
                    prompt=prompt,
                    options=[*LEVEL_OPTIONS, SKIP],
                    horizon_bars=horizon,
                    reference_price=ref,
                    upper_level=upper,
                    lower_level=lower,
                ),
                {"upper": upper, "lower": lower},
            )
        if qtype is QuizQuestionType.SETUP_PROGRESS and open_setup_id:
            prompt = (
                f"The engine has an open setup. After {horizon} bars, will it have a confirmed plan, "
                "be invalidated or expired, or still be open?"
            )
            return (
                QuizQuestion(
                    id=qid,
                    type=qtype,
                    prompt=prompt,
                    options=[*SETUP_OPTIONS, SKIP],
                    horizon_bars=horizon,
                    reference_price=ref,
                    upper_level=None,
                    lower_level=None,
                ),
                {"setupId": open_setup_id},
            )
    return None


def grade(
    question: QuizQuestion,
    params: dict[str, object],
    answer: str,
    revealed: Sequence[Candle],
    setup_after: dict[str, object] | None,
    answered_at: datetime,
    revealed_to: datetime,
) -> QuizResult:
    """Grades against bars revealed between the two cursors (setups: the engine view at the new cursor)."""
    bars = [b for b in revealed if b.is_closed]

    def result(g: QuizGrade, correct: str | None, detail: str) -> QuizResult:
        return QuizResult(
            question_id=question.id,
            type=question.type,
            answer=answer,
            grade=g if answer != SKIP else QuizGrade.VOID,
            correct_answer=correct,
            detail=detail if answer != SKIP else f"skipped; {detail}",
            answered_at_cursor=answered_at,
            revealed_to_cursor=revealed_to,
        )

    if len(bars) < question.horizon_bars:
        return result(
            QuizGrade.VOID, None, f"only {len(bars)} of {question.horizon_bars} bars exist after the question"
        )
    horizon = bars[: question.horizon_bars]
    if question.type is QuizQuestionType.NEXT_BARS_DIRECTION:
        ref, last = float(params["ref"]), horizon[-1].close  # type: ignore[arg-type]
        if last == ref:
            return result(QuizGrade.VOID, None, "the close was unchanged")
        correct = "UP" if last > ref else "DOWN"
        return result(
            QuizGrade.CORRECT if answer == correct else QuizGrade.INCORRECT,
            correct,
            f"close moved from {ref} to {last}",
        )
    if question.type is QuizQuestionType.LEVEL_FIRST:
        upper, lower = float(params["upper"]), float(params["lower"])  # type: ignore[arg-type]
        for b in horizon:
            hit_up, hit_down = b.high >= upper, b.low <= lower
            if hit_up and hit_down:
                return result(
                    QuizGrade.VOID,
                    None,
                    f"the {b.open_time.isoformat()} bar touched both levels (order unknown)",
                )
            if hit_up or hit_down:
                correct = "UPPER_LEVEL" if hit_up else "LOWER_LEVEL"
                return result(
                    QuizGrade.CORRECT if answer == correct else QuizGrade.INCORRECT,
                    correct,
                    f"{correct} touched first at {b.open_time.isoformat()}",
                )
        return result(
            QuizGrade.CORRECT if answer == "NEITHER" else QuizGrade.INCORRECT,
            "NEITHER",
            "neither level was touched",
        )
    if setup_after is None:
        return result(QuizGrade.VOID, None, "the setup is no longer reported by the engine")
    if setup_after.get("hasPlan"):
        correct = "CONFIRMED_PLAN"
    elif setup_after.get("state") in ENDED:
        correct = "INVALIDATED_OR_EXPIRED"
    else:
        correct = "STILL_OPEN"
    return result(
        QuizGrade.CORRECT if answer == correct else QuizGrade.INCORRECT,
        correct,
        f"setup state {setup_after.get('state')}",
    )


def score(history: Sequence[QuizResult], cfg: AnalyticsConfig) -> QuizScore:
    correct = sum(1 for r in history if r.grade is QuizGrade.CORRECT)
    incorrect = sum(1 for r in history if r.grade is QuizGrade.INCORRECT)
    graded = correct + incorrect
    return QuizScore(
        asked=len(history),
        correct=correct,
        incorrect=incorrect,
        void=len(history) - graded,
        accuracy=round(correct / graded, 4) if graded else None,
        label=label_for(graded, cfg),
    )


def mask_candle(c: Candle, scale: float, shift: timedelta) -> ChartCandle:
    return ChartCandle(
        time=c.open_time + shift,
        open=round(c.open * scale, 6),
        high=round(c.high * scale, 6),
        low=round(c.low * scale, 6),
        close=round(c.close * scale, 6),
        volume=c.volume,
        is_closed=c.is_closed,
    )
