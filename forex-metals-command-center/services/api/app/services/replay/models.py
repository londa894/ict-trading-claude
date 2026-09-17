"""Replay models (Phase 19). Education only: a replay never authorizes anything and never shows the future."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.contracts import load_spec
from app.domain.base import ApiModel, is_utc
from app.domain.enums import QuizGrade, QuizQuestionType, ReplayMode, SampleSizeLabel, Timeframe
from app.services.candles.service import ChartCandle


@dataclass(frozen=True)
class ReplayConfig:
    timeframes: tuple[Timeframe, ...]
    window_bars: int
    max_step_bars: int
    quiz_horizon_bars: int
    level_atr_period: int
    max_sessions: int
    idle_ttl_hours: float
    blind_scale_min: float
    blind_scale_max: float
    blind_min_week_shift: int
    blind_max_week_shift: int

    @classmethod
    def from_spec(cls) -> ReplayConfig:
        s = load_spec("replay")
        b = s["blind"]
        cfg = cls(
            timeframes=tuple(Timeframe(x) for x in s["timeframes"]),
            window_bars=int(s["windowBars"]),
            max_step_bars=int(s["maxStepBars"]),
            quiz_horizon_bars=int(s["quizHorizonBars"]),
            level_atr_period=int(s["levelAtrPeriod"]),
            max_sessions=int(s["maxSessions"]),
            idle_ttl_hours=float(s["idleTtlHours"]),
            blind_scale_min=float(b["priceScaleMin"]),
            blind_scale_max=float(b["priceScaleMax"]),
            blind_min_week_shift=int(b["minWeekShift"]),
            blind_max_week_shift=int(b["maxWeekShift"]),
        )
        if not 0 < cfg.blind_scale_min < cfg.blind_scale_max or cfg.blind_min_week_shift < 1:
            raise ValueError("blind masking limits invalid")
        if cfg.quiz_horizon_bars < 1 or cfg.max_step_bars < cfg.quiz_horizon_bars or cfg.window_bars < 50:
            raise ValueError("replay limits invalid")
        return cfg


LIMITS = ReplayConfig.from_spec()


class CreateReplayRequest(ApiModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    mode: ReplayMode = ReplayMode.MANUAL
    timeframe: Timeframe = Timeframe.M15
    start: datetime

    @field_validator("start")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if not is_utc(value):
            raise ValueError("start must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _timeframe(self) -> Self:
        if self.timeframe not in LIMITS.timeframes:
            raise ValueError(f"timeframe must be one of {', '.join(t.value for t in LIMITS.timeframes)}")
        return self


class StepRequest(ApiModel):
    bars: int = Field(ge=-LIMITS.max_step_bars, le=LIMITS.max_step_bars)

    @field_validator("bars")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("bars must not be 0")
        return value


class QuizAnswerRequest(ApiModel):
    question_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=40)


class ReplaySetupView(ApiModel):
    id: str
    setup_type: str
    direction: str
    state: str
    next_required_event: str | None
    plan_entry: float | None
    plan_stop: float | None
    plan_tp1: float | None


class ReplayAnalysis(ApiModel):
    """The live engine's view as of the cursor (never later)."""

    eligible_for_decision: bool
    ineligibility: list[str]
    setup_state: str | None
    current_setup: ReplaySetupView | None
    new_york_time: str
    active_sessions: list[str]
    time_quality: str
    authority: str  # always EDUCATION_ONLY


class ReplayEvent(ApiModel):
    time: datetime
    state: str
    direction: str
    detail: str


class ReplayGuidance(ApiModel):
    events: list[ReplayEvent]  # engine events after the previous cursor, up to this cursor
    narrative: list[str]


class QuizQuestion(ApiModel):
    id: str
    type: QuizQuestionType
    prompt: str
    options: list[str]
    horizon_bars: int
    reference_price: float  # masked in the same way as the candles (quiz sessions are not blind)
    upper_level: float | None
    lower_level: float | None


class QuizResult(ApiModel):
    question_id: str
    type: QuizQuestionType
    answer: str
    grade: QuizGrade
    correct_answer: str | None
    detail: str
    answered_at_cursor: datetime
    revealed_to_cursor: datetime


class QuizScore(ApiModel):
    asked: int
    correct: int
    incorrect: int
    void: int
    accuracy: float | None
    label: SampleSizeLabel


class ReplayState(ApiModel):
    id: str
    mode: ReplayMode
    timeframe: Timeframe
    label: str  # the symbol, or "Hidden instrument" in BLIND sessions
    cursor: datetime  # shifted by a hidden whole number of weeks in BLIND sessions
    start: datetime
    can_step_back: bool
    at_end: bool
    ended: bool
    candles: list[ChartCandle]  # closed candles with close time <= cursor only
    analysis: ReplayAnalysis | None
    guidance: ReplayGuidance | None
    quiz: QuizQuestion | None
    last_result: QuizResult | None
    history: list[QuizResult]
    score: QuizScore | None
    masked: bool
    authority: str  # always EDUCATION_ONLY
    strategy_version: str


class ReplayReveal(ApiModel):
    id: str
    symbol: str
    start: datetime
    cursor: datetime
    price_scale: float
    week_shift: int


class ReplaySessionRow(ApiModel):
    id: str
    mode: ReplayMode
    timeframe: Timeframe
    label: str
    cursor: datetime
    ended: bool
    score: QuizScore | None


class ReplayStatus(ApiModel):
    sessions: list[ReplaySessionRow]
    max_sessions: int
    idle_ttl_hours: float
    storage: str  # always IN_MEMORY
    generated_at: datetime
