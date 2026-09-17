"""Replay quiz and masking (Phase 19)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.candle import Candle
from app.domain.enums import DataQuality, QuizGrade, QuizQuestionType, ReplayMode, SampleSizeLabel, Timeframe
from app.services.analytics.models import AnalyticsConfig
from app.services.replay import models as replay_models
from app.services.replay.engine import grade, make_question, mask_candle, mean_true_range, score
from app.services.replay.models import CreateReplayRequest, ReplayConfig, StepRequest

T0 = datetime(2024, 4, 15, 12, 0, tzinfo=UTC)
M15 = timedelta(minutes=15)
ACFG = AnalyticsConfig.from_spec()


def bar(i: int, o: float, h: float, lo: float, c: float) -> Candle:
    t = T0 + M15 * i
    return Candle(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=t,
        close_time=t + M15,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=None,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )


def flat(n: int, start: int = 0, price: float = 2000.0) -> list[Candle]:
    return [bar(start + i, price, price + 1, price - 1, price) for i in range(n)]


def test_config_and_request_validation(monkeypatch):
    cfg = ReplayConfig.from_spec()
    assert cfg.quiz_horizon_bars == 6 and Timeframe.M15 in cfg.timeframes
    with pytest.raises(ValidationError):
        CreateReplayRequest(symbol="XAUUSD", timeframe=Timeframe.H4, start=T0)
    with pytest.raises(ValidationError):
        CreateReplayRequest(symbol="XAUUSD", start=datetime(2024, 4, 15, 12, 0))  # noqa: DTZ001 - naive on purpose
    for bad in (0, cfg.max_step_bars + 1, -cfg.max_step_bars - 1):
        with pytest.raises(ValidationError):
            StepRequest(bars=bad)
    assert CreateReplayRequest(symbol="XAUUSD", start=T0).mode is ReplayMode.MANUAL
    real = replay_models.load_spec

    def patched(name):
        data = json.loads(json.dumps(real(name)))
        data["blind"]["priceScaleMin"] = 2.0
        return data

    monkeypatch.setattr(replay_models, "load_spec", patched)
    with pytest.raises(ValueError):
        ReplayConfig.from_spec()


def test_mean_true_range():
    assert mean_true_range(flat(10), 14) is None
    assert mean_true_range(flat(20), 14) == pytest.approx(2.0)


def test_question_rotation_uses_only_known_data_and_skips_inapplicable_types():
    known = flat(30)
    q0, p0 = make_question("Q1", 0, known, None, 6, 14)
    assert q0.type is QuizQuestionType.NEXT_BARS_DIRECTION and p0 == {"ref": 2000.0} and "SKIP" in q0.options
    q1, _ = make_question("Q2", 1, known, None, 6, 14)
    assert q1.type is QuizQuestionType.LEVEL_FIRST and (q1.upper_level, q1.lower_level) == (2002.0, 1998.0)
    q2, _ = make_question("Q3", 2, known, None, 6, 14)
    assert q2.type is QuizQuestionType.NEXT_BARS_DIRECTION  # no open setup -> rotation moves on
    q2b, p2b = make_question("Q3", 2, known, "S1", 6, 14)
    assert q2b.type is QuizQuestionType.SETUP_PROGRESS and p2b == {"setupId": "S1"}
    q_short, _ = make_question("Q2", 1, flat(5), None, 6, 14)
    assert q_short.type is not QuizQuestionType.LEVEL_FIRST  # not enough bars for an ATR
    assert make_question("Q1", 0, [], None, 6, 14) is None


def grade_direction(future, answer="UP"):
    q, p = make_question("Q1", 0, flat(30), None, 6, 14)
    return grade(q, p, answer, future, None, T0, T0 + M15 * 6)


def test_direction_grading():
    up = [*flat(5, 30), bar(35, 2000, 2006, 1999, 2005)]
    assert grade_direction(up).grade is QuizGrade.CORRECT
    assert (
        grade_direction(up, "DOWN").grade is QuizGrade.INCORRECT
        and grade_direction(up, "DOWN").correct_answer == "UP"
    )
    assert grade_direction(flat(6, 30)).grade is QuizGrade.VOID  # unchanged
    assert grade_direction(flat(3, 30)).grade is QuizGrade.VOID  # not enough revealed bars
    skipped = grade_direction(up, "SKIP")
    assert (
        skipped.grade is QuizGrade.VOID
        and skipped.detail.startswith("skipped")
        and skipped.correct_answer == "UP"
    )


def test_level_first_grading_including_ambiguity():
    q, p = make_question("Q2", 1, flat(30), None, 6, 14)  # levels 2002 / 1998
    lower_first = [bar(30, 2000, 2001, 1997, 1998), bar(31, 1998, 2003, 1997, 2002), *flat(4, 32)]
    assert grade(q, p, "LOWER_LEVEL", lower_first, None, T0, T0).grade is QuizGrade.CORRECT
    both = [bar(30, 2000, 2003, 1997, 2000), *flat(5, 31)]
    assert grade(q, p, "UPPER_LEVEL", both, None, T0, T0).grade is QuizGrade.VOID
    assert grade(q, p, "NEITHER", flat(6, 30), None, T0, T0).grade is QuizGrade.CORRECT
    late = [*flat(6, 30), bar(36, 2000, 2010, 1999, 2009)]  # beyond the horizon: ignored
    assert grade(q, p, "UPPER_LEVEL", late, None, T0, T0).correct_answer == "NEITHER"


def test_setup_progress_grading():
    q, p = make_question("Q3", 2, flat(30), "S1", 6, 14)
    bars = flat(6, 30)
    assert (
        grade(q, p, "CONFIRMED_PLAN", bars, {"state": "BLOCKED", "hasPlan": True}, T0, T0).grade
        is QuizGrade.CORRECT
    )
    assert (
        grade(q, p, "STILL_OPEN", bars, {"state": "INVALIDATED", "hasPlan": False}, T0, T0).correct_answer
        == "INVALIDATED_OR_EXPIRED"
    )
    assert (
        grade(q, p, "STILL_OPEN", bars, {"state": "WAITING_FOR_MSS", "hasPlan": False}, T0, T0).grade
        is QuizGrade.CORRECT
    )
    assert grade(q, p, "STILL_OPEN", bars, None, T0, T0).grade is QuizGrade.VOID


def test_score_and_masking():
    q, p = make_question("Q1", 0, flat(30), None, 6, 14)
    up = [*flat(5, 30), bar(35, 2000, 2006, 1999, 2005)]
    history = [grade(q, p, a, up, None, T0, T0) for a in ("UP", "UP", "DOWN", "SKIP")]
    s = score(history, ACFG)
    assert (s.asked, s.correct, s.incorrect, s.void, s.accuracy) == (
        4,
        2,
        1,
        1,
        pytest.approx(0.6667, abs=1e-4),
    )
    assert s.label is SampleSizeLabel.INSUFFICIENT and score([], ACFG).accuracy is None
    m = mask_candle(bar(0, 2000, 2010, 1990, 2005), 0.5, timedelta(weeks=600))
    assert (m.open, m.high, m.low, m.close) == (1000.0, 1005.0, 995.0, 1002.5)
    assert m.time == T0 + timedelta(weeks=600) and m.time.weekday() == T0.weekday()
