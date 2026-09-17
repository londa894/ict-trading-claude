"""Alert rules, store and ALERT_ME_WHEN_READY checklist (Phase 11), on hand-built snapshots."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.domain.enums import (
    AlertCategory,
    AlertPriority,
    AlertType,
    Blocker,
    ConditionStatus,
    DataQuality,
    Direction,
    DisplacementGrade,
    EntryWarning,
    EventImportance,
    LiquidityEventType,
    LiquidityPoolType,
    LiquidityScope,
    LiquiditySide,
    LiquidityState,
    MarketStatus,
    NewsState,
    NoWickClassification,
    NoWickStrength,
    NoWickZoneEventType,
    PdArrayEventType,
    PdArrayType,
    ReadyWatchState,
    RiskLock,
    RiskStatus,
    SessionName,
    SetupState,
    SetupStep,
    SetupStepStatus,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
    Timeframe,
    Verdict,
)
from app.domain.instrument import get_instrument
from app.services.alerts.models import AlertCandidate, AlertConfig
from app.services.alerts.ready import SEQUENCE, checklist, next_required
from app.services.alerts.rules import Snapshot, derive
from app.services.alerts.store import AlertStore
from app.services.market_state.gate import GateInput, evaluate

CFG = AlertConfig.from_spec()
T0 = datetime(2024, 1, 9, 14, 0, tzinfo=UTC)
M15 = timedelta(minutes=15)


def decision(verdict: str = "WAIT", quality: DataQuality = DataQuality.CURRENT):
    d = evaluate(
        GateInput(
            symbol="XAUUSD",
            now=T0,
            instrument=get_instrument("XAUUSD"),
            provider_available=True,
            is_synthetic=False,
            data_quality=quality,
            market_status=MarketStatus.OPEN,
        )
    )
    return d.model_copy(update={"verdict": Verdict(verdict)})


def pipeline(**over):
    base = dict(
        structure=NS(events=[]),
        liquidity=NS(events=[], pools=[]),
        pd_arrays=NS(displacements=[], events=[], zones=[]),
        no_wick=NS(events=[], zone_events=[]),
    )
    base.update(over)
    return NS(**base)


def run(as_of=T0, events=(), current=None, sessions=(), pipe=None, eligible=True):
    return NS(
        analysis=NS(
            as_of=as_of, events=list(events), current=current, timeframe=Timeframe.M15, symbol="XAUUSD"
        ),
        pipeline=pipe if pipe is not None else pipeline(),
        session=NS(clock=NS(active_sessions=list(sessions))),
    )


def evaluation(eligible=True, locks=(), warnings=(), news=None, macro=None):
    risk = NS(locks=[NS(lock=lk, detail=f"{lk.value} detail") for lk in locks], status=RiskStatus.CLEAR)
    return NS(eligible_for_decision=eligible, risk=risk, warnings=list(warnings), news=news, macro=macro)


def snap(now=T0, d=None, ev=None, r=None):
    return Snapshot(
        symbol="XAUUSD", now=now, decision=d or decision(), evaluation=ev or evaluation(), run=r or run()
    )


def cycle(first: Snapshot, second: Snapshot):
    alerts, memory = derive(first, None, CFG)
    assert alerts == []  # baseline is silent
    return derive(second, memory, CFG)[0]


def types(alerts) -> list[AlertType]:
    return [a.type for a in alerts]


def test_first_snapshot_is_a_silent_baseline_even_with_history():
    busy = run(
        events=[
            NS(
                id="s1",
                state=SetupState.ENTRY_MISSED,
                time=T0,
                price=1.0,
                detail="x",
                direction=Direction.BULLISH,
            )
        ]
    )
    assert derive(snap(r=busy, d=decision("UNAVAILABLE")), None, CFG)[0] == []


def test_data_unavailable_and_restored_and_no_market_alerts_from_untrusted_data():
    sweep = NS(
        id="L1",
        pool_id="p",
        pool_type=LiquidityPoolType.SWING_HIGH,
        side=LiquiditySide.BSL,
        type=LiquidityEventType.SWEEP,
        price=2040.0,
        time=T0 + M15,
        close=2039.0,
    )
    later = run(as_of=T0 + M15, pipe=pipeline(liquidity=NS(events=[sweep], pools=[])))
    down = cycle(
        snap(), snap(d=decision("UNAVAILABLE", DataQuality.STALE), r=later, ev=evaluation(eligible=False))
    )
    assert types(down) == [AlertType.DATA_UNAVAILABLE] and "DATA_STALE" in down[0].message
    _, memory = derive(snap(d=decision("UNAVAILABLE")), None, CFG)
    restored = derive(snap(), memory, CFG)[0]
    assert types(restored) == [AlertType.DATA_RESTORED]


def test_setup_transitions_map_to_entry_and_setup_alerts():
    def ev(i, state):
        return NS(
            id=f"e{i}",
            state=state,
            time=T0 + M15,
            price=2032.0,
            detail=f"{state.value} detail",
            direction=Direction.BULLISH,
        )

    states = [
        SetupState.WATCH,
        SetupState.SETUP_ARMED,
        SetupState.ENTRY_ZONE_APPROACHING,
        SetupState.ENTRY_ZONE_TOUCHED,
        SetupState.BLOCKED,
        SetupState.ENTRY_MISSED,
        SetupState.INVALIDATED,
        SetupState.EXPIRED,
    ]
    alerts = cycle(snap(), snap(r=run(as_of=T0 + M15, events=[ev(i, s) for i, s in enumerate(states)])))
    assert types(alerts) == [
        AlertType.SETUP_ARMED,
        AlertType.ENTRY_ZONE_APPROACHING,
        AlertType.ENTRY_ZONE_TOUCHED,
        AlertType.ENTRY_CONFIRMED_PENDING_GATES,
        AlertType.ENTRY_MISSED,
        AlertType.SETUP_INVALIDATED,
        AlertType.SETUP_EXPIRED,
    ]
    by = {a.type: a for a in alerts}
    assert by[AlertType.ENTRY_MISSED].message.endswith("Do not chase.")
    assert "Not authorized" in by[AlertType.ENTRY_CONFIRMED_PENDING_GATES].message
    assert all(a.direction is Direction.BULLISH and "LONG" not in a.title + a.message for a in alerts)


def test_novelty_needs_an_unseen_id_and_a_recent_time():
    old = NS(
        id="old",
        state=SetupState.SETUP_ARMED,
        time=T0 - 3 * M15,
        price=1.0,
        detail="",
        direction=Direction.BEARISH,
    )
    grace = NS(
        id="grace",
        state=SetupState.SETUP_ARMED,
        time=T0 - M15,
        price=1.0,
        detail="",
        direction=Direction.BEARISH,
    )
    seen = NS(
        id="seen", state=SetupState.SETUP_ARMED, time=T0, price=1.0, detail="", direction=Direction.BEARISH
    )
    alerts = cycle(snap(r=run(events=[seen])), snap(r=run(as_of=T0 + M15, events=[seen, old, grace])))
    assert [a.reference for a in alerts] == ["grace"]  # windowed history re-surfacing is ignored


def test_market_event_rules():
    t = T0 + M15

    def lq(i, pool_type, etype):
        return NS(
            id=i,
            pool_id="p",
            pool_type=pool_type,
            side=LiquiditySide.SSL,
            type=etype,
            price=2000.0,
            time=t,
            close=2001.0,
        )

    pools = [
        NS(
            id="eq",
            type=LiquidityPoolType.EQL,
            side=LiquiditySide.SSL,
            label="EQL x2",
            price=1990.0,
            known_at=t,
            state=LiquidityState.FRESH,
            scope=LiquidityScope.INTERNAL,
            taken=False,
            distance_atr=3.0,
        ),
        NS(
            id="ext",
            type=LiquidityPoolType.PDH,
            side=LiquiditySide.BSL,
            label="PDH",
            price=2050.0,
            known_at=T0 - timedelta(days=1),
            state=LiquidityState.APPROACHING,
            scope=LiquidityScope.EXTERNAL,
            taken=False,
            distance_atr=0.4,
        ),
    ]
    pipe = pipeline(
        structure=NS(
            events=[
                NS(
                    id="mss",
                    status=StructureEventStatus.CONFIRMED,
                    time=t,
                    level=StructureLevel.INTERNAL,
                    type=StructureEventType.MSS,
                    direction=Direction.BULLISH,
                    price=2010.0,
                    confirmation=NS(value="CANDLE_CLOSE"),
                ),
                NS(
                    id="pot",
                    status=StructureEventStatus.POTENTIAL,
                    time=t,
                    level=StructureLevel.INTERNAL,
                    type=StructureEventType.BOS,
                    direction=Direction.BULLISH,
                    price=2011.0,
                    confirmation=NS(value="WICK_ONLY"),
                ),
            ]
        ),
        liquidity=NS(
            events=[
                lq("sweep", LiquidityPoolType.SWING_LOW, LiquidityEventType.SWEEP),
                lq("run", LiquidityPoolType.SWING_LOW, LiquidityEventType.BREAK),
                lq("touch", LiquidityPoolType.SWING_LOW, LiquidityEventType.TOUCH),
                lq("pdl", LiquidityPoolType.PDL, LiquidityEventType.TOUCH),
                lq("asia", LiquidityPoolType.ASIA_LOW, LiquidityEventType.SWEEP),
                lq("asia-touch", LiquidityPoolType.ASIA_LOW, LiquidityEventType.TOUCH),
            ],
            pools=pools,
        ),
        pd_arrays=NS(
            displacements=[
                NS(
                    id="d1",
                    grade=DisplacementGrade.STRONG,
                    direction=Direction.BULLISH,
                    magnitude_atr=2.1,
                    candle_count=2,
                    time=t,
                ),
                NS(
                    id="d2",
                    grade=DisplacementGrade.MODERATE,
                    direction=Direction.BULLISH,
                    magnitude_atr=1.1,
                    candle_count=1,
                    time=t,
                ),
            ],
            events=[
                *(
                    NS(
                        id=f"pd{k.value}",
                        zone_id="strong",
                        type=k,
                        zone_type=PdArrayType.FVG,
                        direction=Direction.BULLISH,
                        time=t,
                        price=2005.0,
                        detail="",
                    )
                    for k in (
                        PdArrayEventType.CREATED,
                        PdArrayEventType.TOUCHED,
                        PdArrayEventType.INVALIDATED,
                        PdArrayEventType.HALF_FILL,
                    )
                ),
                NS(
                    id="weak-created",
                    zone_id="weak",
                    type=PdArrayEventType.CREATED,
                    zone_type=PdArrayType.FVG,
                    direction=Direction.BULLISH,
                    time=t,
                    price=2005.0,
                    detail="",
                ),
            ],
            zones=[
                NS(id="strong", displacement_grade=DisplacementGrade.STRONG),
                NS(id="weak", displacement_grade=DisplacementGrade.MODERATE),
            ],
        ),
        no_wick=NS(
            events=[
                NS(
                    id="nw1",
                    strength=NoWickStrength.STRONG,
                    classification=NoWickClassification.TRUE_BULLISH_MARUBOZU,
                    relevance_score=70.0,
                    time=t,
                    close=2006.0,
                    direction=Direction.BULLISH,
                ),
                NS(
                    id="nw2",
                    strength=NoWickStrength.MEANINGFUL,
                    classification=NoWickClassification.BULLISH_NO_LOWER_WICK,
                    relevance_score=40.0,
                    time=t,
                    close=2006.0,
                    direction=Direction.BULLISH,
                ),
            ],
            zone_events=[
                NS(
                    id="z1",
                    type=NoWickZoneEventType.FULLY_REBALANCED,
                    time=t,
                    price=2003.0,
                    detail="50%",
                    direction=Direction.BULLISH,
                ),
                NS(
                    id="z2",
                    type=NoWickZoneEventType.TOUCHED,
                    time=t,
                    price=2003.0,
                    detail="touch",
                    direction=Direction.BULLISH,
                ),
            ],
        ),
    )
    alerts = cycle(snap(), snap(r=run(as_of=t, pipe=pipe)))
    got = sorted((a.type.value, a.reference) for a in alerts)
    assert got == sorted(
        [
            ("STRUCTURE_BREAK", "mss"),
            ("LIQUIDITY_SWEEP", "sweep"),
            ("LIQUIDITY_RUN", "run"),
            ("KEY_LEVEL_INTERACTION", "pdl"),
            ("SESSION_LIQUIDITY_EVENT", "asia"),
            ("EQUAL_LEVEL_CREATED", "eq"),
            ("LIQUIDITY_APPROACHING", "ext"),
            ("DISPLACEMENT", "d1:STRONG"),
            ("FVG_CREATED", "pdCREATED"),
            ("FVG_TOUCHED", "pdTOUCHED"),
            ("FVG_INVALIDATED", "pdINVALIDATED"),
            ("NO_WICK_EVENT", "nw1"),
            ("NO_WICK_REBALANCE", "z1"),
        ]
    )


def test_risk_locks_sessions_and_warnings_fire_on_changes_only():
    first = snap(
        ev=evaluation(locks=[RiskLock.DAILY_LOSS_LIMIT], warnings=[EntryWarning.WEAK_FVG]),
        r=run(
            current=NS(id="S1", direction=Direction.BEARISH, state=SetupState.SETUP_ARMED, zone_ids=[]),
            sessions=[SessionName.LONDON],
        ),
    )
    second = snap(
        ev=evaluation(
            locks=[RiskLock.CONSECUTIVE_LOSSES], warnings=[EntryWarning.WEAK_FVG, EntryWarning.AGAINST_HTF]
        ),
        r=run(
            as_of=T0 + M15,
            current=NS(id="S1", direction=Direction.BEARISH, state=SetupState.SETUP_ARMED, zone_ids=[]),
            sessions=[SessionName.NY_AM],
        ),
    )
    alerts = cycle(first, second)
    assert sorted((a.type.value, a.reference) for a in alerts) == sorted(
        [
            ("RISK_LOCK", "CONSECUTIVE_LOSSES"),
            ("RISK_LOCK_CLEARED", "DAILY_LOSS_LIMIT"),
            ("SESSION_CHANGE", "NY_AM started; LONDON ended"),
            ("EARLY_ENTRY_WARNING", "S1:AGAINST_HTF"),
        ]
    )
    same = derive(second, derive(second, None, CFG)[1], CFG)[0]
    assert same == []  # nothing changed -> nothing fires


def test_store_dedupes_within_the_category_cooldown():
    store = AlertStore(CFG)
    c = AlertCandidate(AlertType.DATA_UNAVAILABLE, "XAUUSD", "decision", "t", "m", T0)
    first = store.add(c, T0)
    assert first is not None and first.seq == 1 and first.category is AlertCategory.CRITICAL
    assert first.priority is AlertPriority.CRITICAL and first.dedupe_key == "XAUUSD:DATA_UNAVAILABLE:decision"
    assert store.add(c, T0 + timedelta(seconds=599)) is None and store.suppressed["DATA_UNAVAILABLE"] == 1
    assert store.add(replace(c, reference="other"), T0 + timedelta(seconds=1)) is not None
    assert store.add(c, T0 + timedelta(seconds=600)) is not None
    assert [a.seq for a in store.list()] == [3, 2, 1]


def test_store_buffer_is_bounded():
    small = replace(CFG, buffer_size=3)
    store = AlertStore(small)
    for i in range(5):
        store.add(AlertCandidate(AlertType.FVG_CREATED, "XAUUSD", f"z{i}", "t", "m", T0), T0)
    assert [a.seq for a in store.list()] == [5, 4, 3]


def test_config_covers_every_type_and_category():
    assert set(CFG.types) == set(AlertType) and set(CFG.category_cooldown) == set(AlertCategory)
    assert CFG.types[AlertType.READY] == (AlertCategory.ENTRY, AlertPriority.CRITICAL)
    assert CFG.monitored_symbols == ("XAUUSD",)


def ready_run(direction=Direction.BULLISH, done=True):
    status = SetupStepStatus.DONE if done else SetupStepStatus.PENDING
    steps = [NS(step=s, status=status, detail=s.value) for s in SEQUENCE] + [
        NS(step=SetupStep.RISK, status=SetupStepStatus.NOT_EVALUATED, detail="")
    ]
    return run(current=NS(id="S", direction=direction, state=SetupState.BLOCKED, steps=steps, zone_ids=[]))


def clear_news(state=NewsState.CLEAR, blockers=()):
    return NS(state=state, blockers=list(blockers))


def within_limits(news=None):
    return NS(risk=NS(status=RiskStatus.WITHIN_LIMITS), news=news if news is not None else clear_news())


def test_ready_watch_cannot_fire_under_fail_safe_authority():
    state, conditions, fire = checklist(
        decision("WAIT"), within_limits(), ready_run(), Direction.BULLISH, "FAIL_SAFE_ONLY"
    )
    assert state is ReadyWatchState.GATES_PENDING and not fire
    missing = [c.name for c in conditions if c.status is not ConditionStatus.MET]
    assert missing == ["VERDICT_AUTHORITY", "VERDICT"]
    assert next_required(conditions) == "VERDICT_AUTHORITY: FAIL_SAFE_ONLY"
    # even a (forbidden) directional verdict cannot fire without FULL authority
    assert (
        checklist(decision("LONG"), within_limits(), ready_run(), Direction.BULLISH, "FAIL_SAFE_ONLY")[2]
        is False
    )


def test_ready_watch_fires_only_on_a_matching_verdict_with_full_authority():
    assert checklist(decision("LONG"), within_limits(), ready_run(), Direction.BULLISH, "FULL")[:1] == (
        ReadyWatchState.FIRED,
    )
    assert checklist(decision("SHORT"), within_limits(), ready_run(), Direction.BULLISH, "FULL")[2] is False
    assert (
        checklist(decision("SHORT"), within_limits(), ready_run(Direction.BEARISH), None, "FULL")[2] is True
    )


@pytest.mark.parametrize(
    ("d", "r", "direction", "expected"),
    [
        (decision("UNAVAILABLE"), ready_run(), None, ReadyWatchState.UNAVAILABLE),
        (decision("WAIT"), ready_run(done=False), None, ReadyWatchState.WAITING),
        (decision("WAIT"), ready_run(Direction.BEARISH), Direction.BULLISH, ReadyWatchState.WAITING),
        (decision("WAIT"), run(), None, ReadyWatchState.WAITING),
    ],
)
def test_ready_watch_states(d, r, direction, expected):
    assert checklist(d, within_limits(), r, direction, "FAIL_SAFE_ONLY")[0] is expected


def test_ready_watch_needs_risk_within_limits():
    state, conditions, _ = checklist(
        decision("WAIT"),
        NS(risk=NS(status=RiskStatus.LOCKED), news=clear_news()),
        ready_run(),
        None,
        "FAIL_SAFE_ONLY",
    )
    assert (
        state is ReadyWatchState.WAITING
        and next(c for c in conditions if c.name == "RISK").detail == "LOCKED"
    )


def test_weak_fvgs_alert_only_when_they_belong_to_the_open_setup():
    t = T0 + M15
    event = NS(
        id="e",
        zone_id="z",
        type=PdArrayEventType.TOUCHED,
        zone_type=PdArrayType.FVG,
        direction=Direction.BEARISH,
        time=t,
        price=1.0,
        detail="",
    )
    pipe = pipeline(
        pd_arrays=NS(displacements=[], events=[event], zones=[NS(id="z", displacement_grade=None)])
    )
    setup = NS(id="S", direction=Direction.BEARISH, state=SetupState.WAITING_FOR_RETRACEMENT, zone_ids=["z"])
    assert cycle(snap(), snap(r=run(as_of=t, pipe=pipe))) == []
    alerts = cycle(snap(r=run(current=setup)), snap(r=run(as_of=t, pipe=pipe, current=setup)))
    assert types(alerts) == [AlertType.FVG_TOUCHED]


def test_unusable_data_never_counts_setup_steps_as_met():
    state, conditions, fire = checklist(decision("UNAVAILABLE"), within_limits(), ready_run(), None, "FULL")
    assert state is ReadyWatchState.UNAVAILABLE and not fire
    setup_part = [c for c in conditions if c.name in {"OPEN_SETUP", *(s.value for s in SEQUENCE)}]
    assert len(setup_part) == 9 and {c.status for c in setup_part} == {ConditionStatus.NOT_EVALUATED}


def test_ready_watch_needs_a_clear_news_gate():
    for news, detail in (
        (clear_news(NewsState.BLACKOUT, [Blocker.NEWS_BLACKOUT]), "BLACKOUT (NEWS_BLACKOUT)"),
        (clear_news(NewsState.CLEAR, [Blocker.NEWS_DATA_SYNTHETIC]), "CLEAR (NEWS_DATA_SYNTHETIC)"),
        (
            clear_news(NewsState.UNAVAILABLE, [Blocker.NEWS_DATA_UNAVAILABLE]),
            "UNAVAILABLE (NEWS_DATA_UNAVAILABLE)",
        ),
    ):
        state, conditions, _ = checklist(
            decision("WAIT"), within_limits(news), ready_run(), None, "FAIL_SAFE_ONLY"
        )
        gate = next(c for c in conditions if c.name == "NEWS_GATE")
        assert (
            state is ReadyWatchState.WAITING
            and gate.status is ConditionStatus.MISSING
            and gate.detail == detail
        )
    caution = checklist(
        decision("WAIT"), within_limits(clear_news(NewsState.CAUTION)), ready_run(), None, "FAIL_SAFE_ONLY"
    )
    assert caution[0] is ReadyWatchState.GATES_PENDING


def news_state(state, minutes=90.0, event_id="EV1", importance=EventImportance.HIGH):
    event = NS(
        id=event_id,
        importance=importance,
        currency="USD",
        name="US CPI",
        scheduled_time=T0 + timedelta(minutes=minutes),
        minutes_to_event=minutes,
    )
    return NS(
        state=state,
        active_event=event if state is not NewsState.CLEAR else None,
        next_event=event,
        blockers=[],
    )


def test_news_alerts_blackout_cleared_and_countdown_even_on_untrusted_data():
    ineligible = evaluation(eligible=False)
    first = snap(ev=evaluation(news=news_state(NewsState.CLEAR, minutes=90)))
    near = snap(
        ev=evaluation(eligible=False, news=news_state(NewsState.CAUTION, minutes=50)),
        d=decision("UNAVAILABLE"),
    )
    _, memory = derive(first, None, CFG)
    alerts, memory = derive(near, memory, CFG)
    assert [(a.type, a.reference) for a in alerts] == [
        (AlertType.DATA_UNAVAILABLE, "decision"),
        (AlertType.NEWS_COUNTDOWN, "EV1:60"),
    ]
    closer = snap(ev=evaluation(news=news_state(NewsState.BLACKOUT, minutes=10)))
    alerts, memory = derive(closer, memory, CFG)
    assert [(a.type, a.reference) for a in alerts] == [
        (AlertType.DATA_RESTORED, "decision"),
        (AlertType.NEWS_BLACKOUT, "EV1"),
        (AlertType.NEWS_COUNTDOWN, "EV1:15"),
    ]
    again = derive(closer, memory, CFG)[0]
    assert [a.type for a in again] == []  # no repeats
    after = snap(ev=evaluation(news=news_state(NewsState.NORMALIZED, minutes=-60, event_id="EV1")))
    alerts, _ = derive(after, memory, CFG)
    assert [a.type for a in alerts] == [AlertType.NEWS_CLEARED]
    assert ineligible.news is None


def test_countdown_announces_only_the_nearest_threshold_when_first_seen_late():
    _, memory = derive(
        snap(ev=evaluation(news=news_state(NewsState.CLEAR, minutes=500, event_id="EV0"))), None, CFG
    )
    late = snap(ev=evaluation(news=news_state(NewsState.BLACKOUT, minutes=5, event_id="EV2")))
    alerts, memory = derive(late, memory, CFG)
    countdowns = [a.reference for a in alerts if a.type is AlertType.NEWS_COUNTDOWN]
    assert countdowns == ["EV2:15"] and {"EV2:60", "EV2:15"} <= memory.countdowns
    medium = snap(
        ev=evaluation(
            news=news_state(NewsState.CAUTION, minutes=5, event_id="EV3", importance=EventImportance.MEDIUM)
        )
    )
    assert [a.type for a in derive(medium, memory, CFG)[0] if a.type is AlertType.NEWS_COUNTDOWN] == []


def _macro(bias, available=True, synthetic=False):
    return NS(bias=NS(value=bias), available=available, is_synthetic=synthetic, score=0.4)


def test_macro_shift_alerts_on_a_bias_change_only_and_survives_outages():
    _, memory = derive(snap(ev=evaluation(macro=_macro("NEUTRAL"))), None, CFG)
    assert [a.type for a in derive(snap(ev=evaluation(macro=_macro("NEUTRAL"))), memory, CFG)[0]] == []
    alerts, memory = derive(snap(ev=evaluation(macro=_macro("BULLISH"))), memory, CFG)
    assert [(a.type, a.reference) for a in alerts] == [(AlertType.MACRO_SHIFT, "NEUTRAL>BULLISH")]
    assert "never creates a trade" in alerts[0].message
    outage = snap(ev=evaluation(eligible=False, macro=_macro("UNAVAILABLE", available=False)))
    alerts, memory = derive(outage, memory, CFG)
    assert AlertType.MACRO_SHIFT not in [a.type for a in alerts] and memory.macro_bias == "BULLISH"
    back = derive(snap(ev=evaluation(macro=_macro("BULLISH"))), memory, CFG)[0]
    assert AlertType.MACRO_SHIFT not in [a.type for a in back]  # an outage does not re-announce the bias
    flipped = derive(snap(ev=evaluation(macro=_macro("BEARISH", synthetic=True))), memory, CFG)[0]
    shift = [a for a in flipped if a.type is AlertType.MACRO_SHIFT]
    assert shift and "synthetic" in shift[0].message
