"""Replay service: in-memory sessions with a cursor; every response is computed as of the cursor.

Future bars are read only (a) to find where the cursor lands when stepping forward and (b) to grade a quiz
answer after the cursor has moved past the question. Nothing beyond the cursor is ever returned.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.contracts import strategy_version
from app.domain.candle import Candle
from app.domain.enums import DataQuality, QuizQuestionType, ReplayMode, Timeframe
from app.domain.instrument import get_instrument
from app.services.analytics.models import AnalyticsConfig
from app.services.candles.service import MAX_LIMIT, CandleService, ChartCandle, UnknownSymbolError
from app.services.replay.engine import grade, make_question, mask_candle, score
from app.services.replay.models import (
    CreateReplayRequest,
    QuizAnswerRequest,
    QuizQuestion,
    QuizResult,
    ReplayAnalysis,
    ReplayConfig,
    ReplayEvent,
    ReplayGuidance,
    ReplayReveal,
    ReplaySessionRow,
    ReplaySetupView,
    ReplayState,
    ReplayStatus,
)
from app.services.sessions.service import SessionService
from app.services.setup_state.models import SetupAnalysis
from app.services.setup_state.service import SetupService

_WITHHELD = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})
FORWARD_ONLY = frozenset({ReplayMode.BLIND, ReplayMode.QUIZ})
AUTHORITY = "EDUCATION_ONLY"


class ReplayNotFoundError(LookupError):
    pass


class ReplayRequestError(ValueError):
    pass


@dataclass
class _Session:
    id: str
    symbol: str
    mode: ReplayMode
    timeframe: Timeframe
    start: datetime
    cursor: datetime
    previous_cursor: datetime | None
    created_at: datetime
    last_used: datetime
    price_scale: float = 1.0
    week_shift: int = 0
    ended: bool = False
    at_end: bool = False
    question: QuizQuestion | None = None
    question_params: dict[str, object] = field(default_factory=dict)
    question_index: int = 0
    history: list[QuizResult] = field(default_factory=list)

    @property
    def masked(self) -> bool:
        return self.mode is ReplayMode.BLIND and not self.ended

    @property
    def shift(self) -> timedelta:
        return timedelta(weeks=self.week_shift) if self.masked else timedelta(0)

    @property
    def scale(self) -> float:
        return self.price_scale if self.masked else 1.0


class ReplayService:
    def __init__(
        self,
        candles: CandleService,
        setups: SetupService,
        sessions: SessionService,
        clock: Callable[[], datetime] | None = None,
        cfg: ReplayConfig | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._candles = candles
        self._setups = setups
        self._session_svc = sessions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or ReplayConfig.from_spec()
        self._acfg = AnalyticsConfig.from_spec()
        self._rng = rng or random.SystemRandom()
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    # --- helpers ------------------------------------------------------------

    def _expire(self) -> None:
        cutoff = self._clock() - timedelta(hours=self._cfg.idle_ttl_hours)
        for sid in [s.id for s in self._sessions.values() if s.last_used < cutoff]:
            del self._sessions[sid]

    def _get(self, session_id: str) -> _Session:
        self._expire()
        s = self._sessions.get(session_id)
        if s is None:
            raise ReplayNotFoundError(session_id)
        s.last_used = self._clock()
        return s

    async def _closed(self, symbol: str, tf: Timeframe, limit: int, until: datetime) -> list[Candle]:
        """Closed candles with close time <= until (the hide-future boundary)."""
        series = await self._candles.load_series(symbol, tf, min(limit, MAX_LIMIT), until)
        if series.quality in _WITHHELD:
            raise ReplayRequestError(f"{tf.value} history {series.quality.value}: replay is unavailable here")
        return [c for c in series.candles if c.is_closed and c.close_time <= until]

    async def _forward_cursor(self, s: _Session, bars: int) -> tuple[datetime, bool]:
        """Close time of the `bars`-th closed bar after the cursor (capped by the data and the clock)."""
        now = self._clock()
        span = s.timeframe.duration * bars
        for extra in (timedelta(0), timedelta(days=3), timedelta(days=10)):
            until = min(now, s.cursor + span + extra)
            future = [
                c
                for c in await self._closed(s.symbol, s.timeframe, bars + 200, until)
                if c.close_time > s.cursor
            ]
            if len(future) >= bars:
                return future[bars - 1].close_time, False
            if until >= now:
                break
        future = [
            c for c in await self._closed(s.symbol, s.timeframe, bars + 200, now) if c.close_time > s.cursor
        ]
        return (future[-1].close_time if future else s.cursor), True

    async def _analysis(self, s: _Session) -> tuple[SetupAnalysis, ReplayAnalysis]:
        setup = await self._setups.analyze(s.symbol, s.cursor)
        clock = (await self._session_svc.analyze(s.symbol, s.cursor)).clock
        cur = setup.current
        view = None
        if cur is not None:
            plan = cur.entry_plan
            view = ReplaySetupView(
                id=cur.id,
                setup_type=cur.setup_type.value,
                direction=cur.direction.value,
                state=cur.state.value,
                next_required_event=cur.next_required_event,
                plan_entry=plan.entry if plan else None,
                plan_stop=plan.stop if plan else None,
                plan_tp1=plan.tp1 if plan else None,
            )
        return setup, ReplayAnalysis(
            eligible_for_decision=setup.eligible_for_decision,
            ineligibility=[i.value for i in setup.ineligibility],
            setup_state=setup.current_state.value if setup.current_state else None,
            current_setup=view,
            new_york_time=clock.new_york_time,
            active_sessions=[x.value for x in clock.active_sessions],
            time_quality=clock.time_quality.value,
            authority=AUTHORITY,
        )

    async def _new_question(self, s: _Session) -> None:
        s.question, s.question_params = None, {}
        if s.at_end:
            return
        known = await self._closed(s.symbol, s.timeframe, self._cfg.window_bars, s.cursor)
        setup = await self._setups.analyze(s.symbol, s.cursor)
        open_id = setup.current.id if setup.current is not None and setup.current.entry_plan is None else None
        built = make_question(
            f"Q{s.question_index + 1}",
            s.question_index,
            known,
            open_id,
            self._cfg.quiz_horizon_bars,
            self._cfg.level_atr_period,
        )
        if built is not None:
            s.question, s.question_params = built

    # --- lifecycle ------------------------------------------------------------

    async def create(self, req: CreateReplayRequest) -> ReplayState:
        instrument = get_instrument(req.symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(req.symbol)
        async with self._lock:
            self._expire()
            if len(self._sessions) >= self._cfg.max_sessions:
                raise ReplayRequestError(f"at most {self._cfg.max_sessions} replay sessions; end one first")
            now = self._clock()
            if req.start >= now:
                raise ReplayRequestError("start must be in the past (only closed history can be replayed)")
            known = await self._closed(instrument.symbol, req.timeframe, 2, req.start)
            if not known:
                raise ReplayRequestError("no closed history at the start time")
            cursor = known[-1].close_time
            s = _Session(
                id=str(uuid.uuid4()),
                symbol=instrument.symbol,
                mode=req.mode,
                timeframe=req.timeframe,
                start=cursor,
                cursor=cursor,
                previous_cursor=None,
                created_at=now,
                last_used=now,
            )
            if req.mode is ReplayMode.BLIND:
                s.price_scale = round(
                    self._rng.uniform(self._cfg.blind_scale_min, self._cfg.blind_scale_max), 4
                )
                s.week_shift = self._rng.randint(
                    self._cfg.blind_min_week_shift, self._cfg.blind_max_week_shift
                )
            if req.mode is ReplayMode.QUIZ:
                await self._new_question(s)
            self._sessions[s.id] = s
        return await self.state(s.id)

    async def step(self, session_id: str, bars: int) -> ReplayState:
        async with self._lock:
            s = self._get(session_id)
            if s.ended:
                raise ReplayRequestError("the session has ended")
            if s.mode is ReplayMode.QUIZ:
                raise ReplayRequestError("quiz sessions move forward by answering (or skipping) the question")
            if bars < 0 and s.mode in FORWARD_ONLY:
                raise ReplayRequestError(
                    "stepping back is not allowed in this mode (the future was already shown)"
                )
            if bars > 0:
                cursor, at_end = await self._forward_cursor(s, bars)
            else:
                past = await self._closed(s.symbol, s.timeframe, -bars + 1, s.cursor)
                cursor, at_end = (past[0].close_time if len(past) > -bars else s.cursor), False
            s.previous_cursor, s.cursor, s.at_end = s.cursor, cursor, at_end
        return await self.state(session_id)

    async def answer(self, session_id: str, req: QuizAnswerRequest) -> ReplayState:
        async with self._lock:
            s = self._get(session_id)
            if s.mode is not ReplayMode.QUIZ or s.question is None or s.ended:
                raise ReplayRequestError("there is no open quiz question")
            if req.question_id != s.question.id:
                raise ReplayRequestError("that question is no longer open")
            if req.answer not in s.question.options:
                raise ReplayRequestError(f"answer must be one of {', '.join(s.question.options)}")
            question, params, asked_at = s.question, s.question_params, s.cursor
            cursor, at_end = await self._forward_cursor(s, question.horizon_bars)
            revealed = [
                c
                for c in await self._closed(s.symbol, s.timeframe, question.horizon_bars + 50, cursor)
                if c.close_time > asked_at
            ]
            setup_after: dict[str, object] | None = None
            if question.type is QuizQuestionType.SETUP_PROGRESS:
                after = await self._setups.analyze(s.symbol, cursor)
                match = next((x for x in after.setups if x.id == params.get("setupId")), None)
                if match is not None:
                    setup_after = {"state": match.state.value, "hasPlan": match.entry_plan is not None}
            result = grade(question, params, req.answer, revealed, setup_after, asked_at, cursor)
            s.history.append(result)
            s.previous_cursor, s.cursor, s.at_end = asked_at, cursor, at_end
            s.question_index += 1
            await self._new_question(s)
        return await self.state(session_id)

    async def end(self, session_id: str) -> ReplayReveal:
        async with self._lock:
            s = self._get(session_id)
            s.ended = True
            s.question = None
        return ReplayReveal(
            id=s.id,
            symbol=s.symbol,
            start=s.start,
            cursor=s.cursor,
            price_scale=s.price_scale,
            week_shift=s.week_shift,
        )

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._get(session_id)
            del self._sessions[session_id]

    # --- read ------------------------------------------------------------

    async def state(self, session_id: str) -> ReplayState:
        s = self._get(session_id)
        bars = await self._closed(s.symbol, s.timeframe, self._cfg.window_bars, s.cursor)
        candles: list[ChartCandle] = [mask_candle(b, s.scale, s.shift) for b in bars]
        analysis = guidance = None
        show_engine = (
            s.mode in (ReplayMode.MANUAL, ReplayMode.GUIDED)
            or (s.mode is ReplayMode.QUIZ and bool(s.history))
            or (s.mode is ReplayMode.BLIND and s.ended)
        )
        setup: SetupAnalysis | None = None
        if show_engine:
            setup, analysis = await self._analysis(s)
        if s.mode is ReplayMode.GUIDED and setup is not None:
            since = s.previous_cursor
            events = (
                [
                    ReplayEvent(
                        time=e.time, state=e.state.value, direction=e.direction.value, detail=e.detail
                    )
                    for e in setup.events
                    if e.time + s.timeframe.duration > (since or s.cursor)
                    and e.time + s.timeframe.duration <= s.cursor
                ]
                if since is not None
                else []
            )
            guidance = ReplayGuidance(events=events, narrative=_narrative(analysis, events))
        return ReplayState(
            id=s.id,
            mode=s.mode,
            timeframe=s.timeframe,
            label="Hidden instrument" if s.masked else s.symbol,
            cursor=s.cursor + s.shift,
            start=s.start + s.shift,
            can_step_back=s.mode not in FORWARD_ONLY and not s.ended,
            at_end=s.at_end,
            ended=s.ended,
            candles=candles,
            analysis=analysis,
            guidance=guidance,
            quiz=s.question,
            last_result=s.history[-1] if s.history else None,
            history=list(s.history),
            score=score(s.history, self._acfg) if s.mode is ReplayMode.QUIZ else None,
            masked=s.masked,
            authority=AUTHORITY,
            strategy_version=strategy_version(),
        )

    async def status(self) -> ReplayStatus:
        self._expire()
        return ReplayStatus(
            sessions=[
                ReplaySessionRow(
                    id=s.id,
                    mode=s.mode,
                    timeframe=s.timeframe,
                    label="Hidden instrument" if s.masked else s.symbol,
                    cursor=s.cursor + s.shift,
                    ended=s.ended,
                    score=score(s.history, self._acfg) if s.mode is ReplayMode.QUIZ else None,
                )
                for s in sorted(self._sessions.values(), key=lambda x: x.created_at, reverse=True)
            ],
            max_sessions=self._cfg.max_sessions,
            idle_ttl_hours=self._cfg.idle_ttl_hours,
            storage="IN_MEMORY",
            generated_at=self._clock(),
        )


def _narrative(analysis: ReplayAnalysis | None, events: list[ReplayEvent]) -> list[str]:
    if analysis is None:
        return []
    lines = []
    if not analysis.eligible_for_decision:
        lines.append(
            "The data at this point is not eligible for a decision ("
            + (", ".join(analysis.ineligibility) or "unknown")
            + "), so the engine would wait."
        )
    for e in events:
        lines.append(f"{e.state} ({e.direction}): {e.detail}")
    cur = analysis.current_setup
    if cur is None:
        lines.append("No open setup: the engine is waiting for liquidity to be taken and structure to shift.")
    else:
        lines.append(f"Open {cur.direction} {cur.setup_type} is {cur.state}.")
        if cur.plan_entry is not None:
            lines.append(
                f"A confirmed plan exists (entry {cur.plan_entry}, stop {cur.plan_stop}, TP1 {cur.plan_tp1});"
                " "
                "in live use it still needs risk, news and authority."
            )
        elif cur.next_required_event:
            lines.append(f"Next the engine needs: {cur.next_required_event}.")
    sessions = ", ".join(analysis.active_sessions) or "none"
    lines.append(
        f"New York {analysis.new_york_time} · sessions {sessions} · time quality {analysis.time_quality}."
    )
    return lines
