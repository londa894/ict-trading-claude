"""Alert rules (pure): compare one symbol's snapshot with the previous cycle's memory.

- The first snapshot of a symbol is a silent baseline (history never floods the feed).
- Market-event alerts come only from data eligible for a decision; unusable data raises DATA_UNAVAILABLE only.
- An analysis event is new when its id was not seen before AND it happened no earlier than one setup bar
  before the previous as-of (windowed pools can re-surface old events; those are ignored).
- Decision, risk, warning and session alerts fire on changes of state, not on every candle.
- FVG/IFVG alerts only for the open setup's zones or zones created by STRONG+ displacement.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.decision import MasterDecision
from app.domain.enums import (
    AlertType,
    Direction,
    EventImportance,
    LiquidityEventType,
    LiquidityPoolType,
    LiquidityScope,
    LiquidityState,
    NewsState,
    NoWickZoneEventType,
    PdArrayEventType,
    SetupState,
    StructureEventStatus,
    Verdict,
)
from app.services.alerts.models import AlertCandidate, AlertConfig, SymbolMemory
from app.services.news.models import NewsAssessment, NewsConfig
from app.services.scoring.models import DecisionEvaluation
from app.services.setup_state.service import SetupRun

A = AlertType
NEWS_CFG = NewsConfig.from_spec()
KEY_LEVELS = frozenset(
    {LiquidityPoolType.PDH, LiquidityPoolType.PDL, LiquidityPoolType.PWH, LiquidityPoolType.PWL}
)
SESSION_POOLS = frozenset(
    {
        LiquidityPoolType.ASIA_HIGH,
        LiquidityPoolType.ASIA_LOW,
        LiquidityPoolType.LONDON_HIGH,
        LiquidityPoolType.LONDON_LOW,
        LiquidityPoolType.NY_AM_HIGH,
        LiquidityPoolType.NY_AM_LOW,
        LiquidityPoolType.NY_PM_HIGH,
        LiquidityPoolType.NY_PM_LOW,
    }
)
SETUP_ALERTS = {
    SetupState.SETUP_ARMED: A.SETUP_ARMED,
    SetupState.ENTRY_ZONE_APPROACHING: A.ENTRY_ZONE_APPROACHING,
    SetupState.ENTRY_ZONE_TOUCHED: A.ENTRY_ZONE_TOUCHED,
    SetupState.BLOCKED: A.ENTRY_CONFIRMED_PENDING_GATES,
    SetupState.ENTRY_MISSED: A.ENTRY_MISSED,
    SetupState.INVALIDATED: A.SETUP_INVALIDATED,
    SetupState.EXPIRED: A.SETUP_EXPIRED,
}
PD_ALERTS = {
    PdArrayEventType.CREATED: A.FVG_CREATED,
    PdArrayEventType.IFVG_CONFIRMED: A.FVG_CREATED,
    PdArrayEventType.TOUCHED: A.FVG_TOUCHED,
    PdArrayEventType.INVALIDATED: A.FVG_INVALIDATED,
}
NO_WICK_ZONE_ALERTS = frozenset(
    {
        NoWickZoneEventType.FULLY_REBALANCED,
        NoWickZoneEventType.REACTED,
        NoWickZoneEventType.FAILED,
    }
)


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    now: datetime
    decision: MasterDecision
    evaluation: DecisionEvaluation | None
    run: SetupRun | None


def _ids(run: SetupRun | None) -> set[str]:
    if run is None or run.pipeline is None:
        return {f"setup:{e.id}" for e in run.analysis.events} if run else set()
    p = run.pipeline
    return {
        *(f"setup:{e.id}" for e in run.analysis.events),
        *(f"structure:{e.id}" for e in p.structure.events),
        *(f"liquidity:{e.id}" for e in p.liquidity.events),
        *(f"pool:{x.id}" for x in p.liquidity.pools),
        *(f"displacement:{d.id}:{d.grade.value}" for d in p.pd_arrays.displacements),
        *(f"pd:{e.id}" for e in p.pd_arrays.events),
        *(f"nowick:{e.id}" for e in p.no_wick.events),
        *(f"nowickzone:{e.id}" for e in p.no_wick.zone_events),
    }


def memory_of(s: Snapshot) -> SymbolMemory:
    ev, run = s.evaluation, s.run
    risk = ev.risk if ev is not None else None
    current = run.analysis.current if run is not None else None
    return SymbolMemory(
        as_of=run.analysis.as_of if run is not None else None,
        seen_ids=frozenset(_ids(run)),
        approaching_pools=frozenset(
            x.id
            for x in (run.pipeline.liquidity.pools if run is not None and run.pipeline is not None else [])
            if x.state is LiquidityState.APPROACHING
        ),
        verdict=s.decision.verdict.value,
        data_quality=s.decision.data_quality.value,
        risk_locks=frozenset(x.lock for x in risk.locks) if risk is not None else frozenset(),
        warnings=frozenset(w.value for w in ev.warnings) if ev is not None else frozenset(),
        setup_id=current.id if current is not None else None,
        active_sessions=frozenset(run.session.clock.active_sessions) if run is not None else frozenset(),
        eligible=bool(
            ev is not None and ev.eligible_for_decision and s.decision.verdict is not Verdict.UNAVAILABLE
        ),
        news_state=ev.news.state.value if ev is not None and ev.news is not None else None,
        macro_bias=(
            ev.macro.bias.value if ev is not None and ev.macro is not None and ev.macro.available else None
        ),
        countdowns=frozenset(_crossed(ev.news) if ev is not None and ev.news is not None else ()),
    )


def _crossed(news: NewsAssessment) -> list[str]:
    """Countdown keys for the next HIGH/EXTREME event whose threshold has been reached."""
    nxt = news.next_event
    if nxt is None or nxt.importance not in (EventImportance.HIGH, EventImportance.EXTREME):
        return []
    return [f"{nxt.id}:{m}" for m in NEWS_CFG.countdown_minutes if nxt.minutes_to_event <= m]


def derive(
    s: Snapshot, previous: SymbolMemory | None, cfg: AlertConfig
) -> tuple[list[AlertCandidate], SymbolMemory]:
    memory = memory_of(s)
    if previous is None:
        return [], memory  # silent baseline
    out: list[AlertCandidate] = []
    sym, d = s.symbol, s.decision

    def add(
        t: AlertType,
        ref: str,
        title: str,
        message: str,
        at: datetime,
        price: float | None = None,
        direction: Direction | None = None,
    ) -> None:
        out.append(AlertCandidate(t, sym, ref, title, message, at, price, direction))

    # --- decision state -----------------------------------------------------------------------------------
    unavailable = d.verdict is Verdict.UNAVAILABLE
    if unavailable and previous.verdict != Verdict.UNAVAILABLE.value:
        blockers = ", ".join(b.value for b in d.blockers)
        add(
            A.DATA_UNAVAILABLE,
            "decision",
            f"{sym} decision UNAVAILABLE",
            f"Data cannot be trusted: {blockers}",
            s.now,
        )
    elif not unavailable and previous.verdict == Verdict.UNAVAILABLE.value:
        add(
            A.DATA_RESTORED,
            "decision",
            f"{sym} data usable again",
            f"Decision {d.verdict.value}, data {d.data_quality.value}",
            s.now,
        )
    # --- news gate (calendar-driven: independent of market-data eligibility) -------------------------
    news = s.evaluation.news if s.evaluation is not None else None
    if news is not None:
        active = news.active_event
        if news.state is NewsState.BLACKOUT and previous.news_state != NewsState.BLACKOUT.value and active:
            add(
                A.NEWS_BLACKOUT,
                active.id,
                f"{sym} news BLACKOUT",
                f"{active.importance.value} {active.currency} {active.name} "
                f"at {active.scheduled_time.isoformat()}",
                s.now,
            )
        was_blocked = previous.news_state in (NewsState.BLACKOUT.value, NewsState.POST_NEWS_WAIT.value)
        if was_blocked and news.state not in (NewsState.BLACKOUT, NewsState.POST_NEWS_WAIT):
            add(A.NEWS_CLEARED, "news", f"{sym} news window over", f"News state {news.state.value}", s.now)
        new_countdowns = sorted(k for k in memory.countdowns if k not in previous.countdowns)
        nxt = news.next_event
        if new_countdowns and nxt is not None:
            threshold = min(int(k.rsplit(":", 1)[1]) for k in new_countdowns)
            add(
                A.NEWS_COUNTDOWN,
                f"{nxt.id}:{threshold}",
                f"{sym} {nxt.importance.value} news in {threshold} min",
                f"{nxt.currency} {nxt.name} at {nxt.scheduled_time.isoformat()} ({nxt.minutes_to_event} min)",
                s.now,
            )
        memory = replace(memory, countdowns=memory.countdowns | previous.countdowns)

    # --- macro context (data-driven: a bias change is context, never a trade signal) ----------------------
    macro = s.evaluation.macro if s.evaluation is not None else None
    if (
        macro is not None
        and memory.macro_bias
        and previous.macro_bias
        and memory.macro_bias != previous.macro_bias
    ):
        synthetic = " (synthetic data)" if macro.is_synthetic else ""
        add(
            A.MACRO_SHIFT,
            f"{previous.macro_bias}>{memory.macro_bias}",
            f"{sym} macro bias {previous.macro_bias} -> {memory.macro_bias}",
            f"Macro score {macro.score}{synthetic}; context only, it never creates a trade",
            s.now,
        )
    elif memory.macro_bias is None and previous.macro_bias is not None:
        memory = replace(memory, macro_bias=previous.macro_bias)  # an outage does not reset the last bias

    if not memory.eligible:
        return out, memory  # never raise market-event alerts from untrusted data

    ev, run = s.evaluation, s.run
    assert ev is not None and run is not None
    tf = run.analysis.timeframe
    grace = previous.as_of - tf.duration * cfg.novelty_grace_bars if previous.as_of is not None else None

    def new(key: str, at: datetime) -> bool:
        return key not in previous.seen_ids and (grace is None or at >= grace)

    # --- risk ----------------------------------------------------------------------------------------------
    if ev.risk is not None:
        for item in ev.risk.locks:
            if item.lock not in previous.risk_locks:
                add(A.RISK_LOCK, item.lock.value, f"{sym} risk lock {item.lock.value}", item.detail, s.now)
        for lock in previous.risk_locks - memory.risk_locks:
            add(
                A.RISK_LOCK_CLEARED,
                lock.value,
                f"{sym} risk lock {lock.value} cleared",
                "The lock no longer applies",
                s.now,
            )

    # --- sessions ------------------------------------------------------------------------------------------
    if memory.active_sessions != previous.active_sessions:
        started = sorted(x.value for x in memory.active_sessions - previous.active_sessions)
        ended = sorted(x.value for x in previous.active_sessions - memory.active_sessions)
        text = "; ".join([*(f"{x} started" for x in started), *(f"{x} ended" for x in ended)])
        add(A.SESSION_CHANGE, text, f"{sym} session change", text, s.now)

    # --- setups and early-entry warnings ------------------------------------------------------------------
    for e in run.analysis.events:
        t = SETUP_ALERTS.get(e.state)
        if t is None or not new(f"setup:{e.id}", e.time):
            continue
        msg = e.detail
        if t is A.ENTRY_MISSED:
            msg = f"{e.detail}. Do not chase."
        elif t is A.ENTRY_CONFIRMED_PENDING_GATES:
            msg = f"{e.detail}. Not authorized: pending the risk check and news gate."
        add(t, e.id, f"{sym} {e.direction.value} setup {e.state.value}", msg, e.time, e.price, e.direction)
    current = run.analysis.current
    if current is not None:
        fresh = memory.warnings - (previous.warnings if previous.setup_id == current.id else frozenset())
        for w in sorted(fresh):
            add(
                A.EARLY_ENTRY_WARNING,
                f"{current.id}:{w}",
                f"{sym} early-entry warning {w}",
                f"Open {current.direction.value} setup in {current.state.value}",
                s.now,
                direction=current.direction,
            )

    p = run.pipeline
    if p is None:
        return out, memory

    # --- structure -----------------------------------------------------------------------------------------
    for se in p.structure.events:
        if se.status is StructureEventStatus.CONFIRMED and new(f"structure:{se.id}", se.time):
            add(
                A.STRUCTURE_BREAK,
                se.id,
                f"{sym} {tf.value} {se.level.value} {se.type.value} {se.direction.value}",
                f"Broke {se.price} ({se.confirmation.value})",
                se.time,
                se.price,
                se.direction,
            )

    # --- liquidity -----------------------------------------------------------------------------------------
    for le in p.liquidity.events:
        if not new(f"liquidity:{le.id}", le.time):
            continue
        if le.pool_type in KEY_LEVELS:
            t = A.KEY_LEVEL_INTERACTION
        elif le.pool_type in SESSION_POOLS:
            t = A.SESSION_LIQUIDITY_EVENT
            if le.type not in (LiquidityEventType.SWEEP, LiquidityEventType.RUN, LiquidityEventType.BREAK):
                continue
        elif le.type is LiquidityEventType.SWEEP:
            t = A.LIQUIDITY_SWEEP
        elif le.type in (LiquidityEventType.RUN, LiquidityEventType.BREAK):
            t = A.LIQUIDITY_RUN
        else:
            continue
        add(
            t,
            le.id,
            f"{sym} {le.side.value} {le.pool_type.value} {le.type.value}",
            f"{le.pool_type.value} @ {le.price}, close {le.close}",
            le.time,
            le.price,
        )
    for pool in p.liquidity.pools:
        if pool.type in (LiquidityPoolType.EQH, LiquidityPoolType.EQL) and new(
            f"pool:{pool.id}", pool.known_at
        ):
            add(
                A.EQUAL_LEVEL_CREATED,
                pool.id,
                f"{sym} {pool.label} formed",
                f"{pool.side.value} pool @ {pool.price}",
                pool.known_at,
                pool.price,
            )
        approaching = pool.state is LiquidityState.APPROACHING and pool.id not in previous.approaching_pools
        if approaching and pool.scope is LiquidityScope.EXTERNAL and not pool.taken:
            add(
                A.LIQUIDITY_APPROACHING,
                pool.id,
                f"{sym} approaching {pool.label}",
                f"{pool.side.value} @ {pool.price} ({pool.distance_atr} ATR)",
                s.now,
                pool.price,
            )

    # --- displacement, FVG/IFVG, no wick ---------------------------------------------------------------
    for disp in p.pd_arrays.displacements:
        strong = disp.grade.rank >= cfg.min_displacement_grade.rank
        if strong and new(f"displacement:{disp.id}:{disp.grade.value}", disp.time):
            add(
                A.DISPLACEMENT,
                f"{disp.id}:{disp.grade.value}",
                f"{sym} {disp.grade.value} {disp.direction.value} displacement",
                f"{disp.magnitude_atr} ATR over {disp.candle_count} candle(s)",
                disp.time,
                direction=disp.direction,
            )
    # FVG/IFVG alerts only for the open setup's zones or zones born from STRONG+ displacement.
    zones = {z.id: z for z in p.pd_arrays.zones}
    setup_zones = set(current.zone_ids) if current is not None else set()

    def zone_matters(zone_id: str) -> bool:
        z = zones.get(zone_id)
        strong = (
            z is not None
            and z.displacement_grade is not None
            and z.displacement_grade.rank >= cfg.min_displacement_grade.rank
        )
        return zone_id in setup_zones or strong

    for pe in p.pd_arrays.events:
        t = PD_ALERTS.get(pe.type)
        if t is not None and zone_matters(pe.zone_id) and new(f"pd:{pe.id}", pe.time):
            add(
                t,
                pe.id,
                f"{sym} {pe.direction.value} {pe.zone_type.value} {pe.type.value}",
                pe.detail,
                pe.time,
                pe.price,
                pe.direction,
            )
    for nw in p.no_wick.events:
        if nw.strength.rank >= cfg.min_no_wick_strength.rank and new(f"nowick:{nw.id}", nw.time):
            add(
                A.NO_WICK_EVENT,
                nw.id,
                f"{sym} {nw.strength.value} {nw.classification.value}",
                f"Relevance {nw.relevance_score} (context only)",
                nw.time,
                nw.close,
                nw.direction,
            )
    for ze in p.no_wick.zone_events:
        if ze.type in NO_WICK_ZONE_ALERTS and new(f"nowickzone:{ze.id}", ze.time):
            add(
                A.NO_WICK_REBALANCE,
                ze.id,
                f"{sym} no-wick zone {ze.type.value}",
                ze.detail,
                ze.time,
                ze.price,
                ze.direction,
            )
    return out, memory
