"""Hand-built entry confirmation scenarios (Phase 8): models, modes, plan, chase protection."""

import pytest

from app.domain.enums import (
    AUTHORITY_SETUP_STATES,
    Direction,
    EntryMode,
    EntryModel,
    NoWickStrength,
    PdArrayType,
    SetupState,
    SetupStep,
    SetupStepStatus,
)
from app.services.entry.models import EntryConfig
from app.services.entry.plan import build_plan, chase_reason
from tests.session_helpers import utc
from tests.setup_helpers import happy

S = SetupState
BULL_CONFIRM = (2031.8, 2032.3, 2031.3, 2032.0)  # closes above the FVG top 2031.5


def states(result, setup=0):
    sid = result.setups[setup].id
    return [(e.state, e.time) for e in result.events if e.setup_id == sid]


def index_of(candles, t):
    return next(i for i, c in enumerate(candles) if c.open_time == t)


@pytest.mark.parametrize("flip", [False, True])
def test_m15_close_confirms_into_blocked_with_a_plan(flip):
    candles, result = happy(rows={29: BULL_CONFIRM}).run(flip)
    seq = states(result)
    assert [s for s, _ in seq][-3:] == [S.ENTRY_ZONE_TOUCHED, S.WAITING_FOR_CONFIRMATION, S.BLOCKED] or [
        s for s, _ in seq
    ][-2:] == [S.ENTRY_ZONE_TOUCHED, S.BLOCKED]
    assert index_of(candles, seq[-1][1]) == 29
    [setup] = result.setups
    plan = setup.entry_plan
    assert setup.state is S.BLOCKED and not setup.terminal and plan is not None
    assert plan.model is EntryModel.M15_CLOSE and plan.mode is EntryMode.STANDARD and not plan.research_only
    px = (lambda p: 4060 - p) if flip else (lambda p: p)
    assert plan.entry == pytest.approx(px(2032.0)) and plan.tp1 == pytest.approx(px(2040.0))
    sign = -1 if flip else 1
    assert 0 < sign * (px(2028.9) - plan.stop) < 0.2  # protective minus a 0.1 ATR buffer
    assert plan.rr1 == pytest.approx(round(abs(plan.tp1 - plan.entry) / abs(plan.entry - plan.stop), 2))
    assert plan.rr1 >= plan.min_rr == 2.0
    assert setup.next_required_event.startswith("clean risk and news gates, then verdict authority")
    status = {s.step: s.status for s in setup.steps}
    assert status[SetupStep.LTF_CONFIRMATION] is SetupStepStatus.DONE
    assert status[SetupStep.RISK] is SetupStepStatus.NOT_EVALUATED
    assert all(e.state not in AUTHORITY_SETUP_STATES for e in result.events)


def test_the_touch_candle_itself_can_confirm():
    candles, result = happy(rows={28: (2031.9, 2032.3, 2031.2, 2032.2)}).run()
    seq = states(result)
    assert [s for s, _ in seq][-2:] == [S.ENTRY_ZONE_TOUCHED, S.BLOCKED]
    assert index_of(candles, seq[-2][1]) == index_of(candles, seq[-1][1]) == 28


def test_no_wick_rebalance_takes_priority_over_the_15m_close():
    _, result = happy(rows={29: BULL_CONFIRM}, no_wick={29: NoWickStrength.STRONG}).run()
    assert result.setups[0].entry_plan.model is EntryModel.NO_WICK_REBALANCE
    _, insignificant = happy(rows={29: BULL_CONFIRM}, no_wick={29: NoWickStrength.INSIGNIFICANT}).run()
    assert insignificant.setups[0].entry_plan.model is EntryModel.M15_CLOSE


def test_ltf_refinement_confirms_from_an_m5_break_after_the_touch():
    candles, result = happy(ltf=[(28, 5, 2031.9)]).run()
    plan = result.setups[0].entry_plan
    assert plan is not None and plan.model is EntryModel.LTF_REFINEMENT and plan.entry == 2031.9
    assert plan.confirmed_at == candles[28].open_time
    _, early = happy(ltf=[(27, 5, 2032.4)]).run()  # before the touch candle: ignored
    assert early.setups[0].entry_plan is None


def test_confirmed_ifvg_zone_is_labelled_ifvg():
    _, result = happy(rows={29: BULL_CONFIRM}, zone_type=PdArrayType.IFVG).run()
    assert result.setups[0].entry_plan.model is EntryModel.IFVG
    _, excluded = happy(rows={29: BULL_CONFIRM}, zone_type=PdArrayType.IFVG).run_with_entry(
        include_confirmed_ifvg=False
    )
    assert excluded.setups[0].entry_plan is None and excluded.setups[0].state is S.EXPIRED


def test_conservative_retest_needs_a_second_candle():
    conservative = EntryConfig.from_spec(EntryMode.CONSERVATIVE)
    rows = {29: BULL_CONFIRM, 30: (2032.0, 2032.6, 2031.9, 2032.4)}
    candles, result = happy(rows=rows, count=32, target_price=2046.0, entry_cfg=conservative).run()
    plan = result.setups[0].entry_plan
    assert plan is not None and plan.model is EntryModel.CONSERVATIVE_RETEST and plan.entry == 2032.4
    assert plan.min_rr == 2.5 and index_of(candles, states(result)[-1][1]) == 30
    failed = {29: BULL_CONFIRM, 30: (2032.0, 2032.1, 2031.6, 2031.9)}
    _, no_retest = happy(rows=failed, count=31, target_price=2046.0, entry_cfg=conservative).run()
    assert no_retest.setups[0].entry_plan is None and no_retest.setups[0].state is S.WAITING_FOR_CONFIRMATION


def test_aggressive_limit_research_enters_at_the_zone_edge_on_touch():
    aggressive = EntryConfig.from_spec(EntryMode.AGGRESSIVE)
    _, result = happy(entry_cfg=aggressive).run()
    plan = result.setups[0].entry_plan
    assert plan is not None and plan.model is EntryModel.LIMIT_RESEARCH and plan.research_only
    assert plan.entry == 2031.5 and plan.min_rr == 1.5


@pytest.mark.parametrize(
    ("target", "reason"),
    [(2036.0, "R:R 1."), (2032.8, "TP1 closer than 1.0 ATR")],
)
def test_chase_protection_at_confirmation(target, reason):
    _, result = happy(rows={29: BULL_CONFIRM}, target_price=target).run()
    setup = result.setups[0]
    assert setup.state is S.ENTRY_MISSED and setup.terminal and setup.entry_plan is None
    assert reason in setup.reason and setup.reason.startswith("M15_CLOSE confirmed but")


def test_no_confirmation_within_the_window_expires():
    rows = {i: (2032.3, 2032.4, 2031.6, 2032.1) for i in range(29, 34)}
    candles, result = happy(rows=rows, count=34).run()
    expired = states(result)[-1]
    assert expired[0] is S.EXPIRED and index_of(candles, expired[1]) == 32  # touch 28 + 4
    assert result.setups[0].reason == "no entry confirmation within 4 bars of the touch"


@pytest.mark.parametrize(
    ("after", "extra", "state", "reason"),
    [
        ({30: (2032.0, 2032.1, 2028.5, 2029.0)}, {}, S.INVALIDATED, "plan stop traded"),
        ({30: (2032.0, 2036.5, 2031.9, 2036.0)}, {}, S.ENTRY_MISSED, "price moved away; R:R now"),
        ({}, {"target_taken_at": 30}, S.ENTRY_MISSED, "TP1 reached before an authorized entry"),
    ],
)
def test_after_confirmation(after, extra, state, reason):
    _, result = happy(rows={29: BULL_CONFIRM, **after}, count=31, **extra).run()
    setup = result.setups[0]
    assert [s for s, _ in states(result)][-2:] == [S.BLOCKED, state]
    assert setup.reason.startswith(reason) and setup.entry_plan is not None


def test_plan_targets_rr_and_bearish_mirror():
    cfg = EntryConfig.from_spec()
    t = utc(2024, 1, 9, 8, 0)
    plan, reason = build_plan(
        direction=Direction.BULLISH,
        model=EntryModel.M15_CLOSE,
        mode=EntryMode.STANDARD,
        confirmed_at=t,
        zone_id="z",
        entry=100.0,
        protective=98.1,
        tp1=106.0,
        further_targets=[106.1, 108.0, 110.0, 99.0],
        atr=1.0,
        cfg=cfg,
    )
    assert reason is None and plan is not None
    assert (plan.stop, plan.risk, plan.rr1) == (98.0, 2.0, 3.0)
    assert (plan.tp2, plan.tp3, plan.rr2, plan.rr3) == (
        108.0,
        110.0,
        4.0,
        5.0,
    )  # 106.1 is not distinct from TP1
    bear, _ = build_plan(
        direction=Direction.BEARISH,
        model=EntryModel.M15_CLOSE,
        mode=EntryMode.STANDARD,
        confirmed_at=t,
        zone_id="z",
        entry=100.0,
        protective=101.9,
        tp1=94.0,
        further_targets=[92.0],
        atr=1.0,
        cfg=cfg,
    )
    assert bear is not None and (bear.stop, bear.rr1, bear.tp2, bear.tp3) == (102.0, 3.0, 92.0, None)
    none, why = build_plan(
        direction=Direction.BULLISH,
        model=EntryModel.M15_CLOSE,
        mode=EntryMode.STANDARD,
        confirmed_at=t,
        zone_id="z",
        entry=97.0,
        protective=98.1,
        tp1=106.0,
        further_targets=[],
        atr=1.0,
        cfg=cfg,
    )
    assert none is None and why == "entry is beyond the stop"
    assert chase_reason(plan, 99.5) is None  # below entry: not a chase
    assert chase_reason(plan, 100.5) is None  # R:R from 100.5 = 5.5 / 2.5 = 2.2
    assert chase_reason(plan, 102.0) is not None


def test_entry_config_refuses_unavailable_models(monkeypatch):
    from app.services.entry import models

    real = models.load_spec

    def patched(models_list):
        def inner(name):
            spec = real(name)
            if name == "entry":
                spec = {**spec, "modes": {**spec["modes"], "STANDARD": {"models": models_list, "minRr": 2.0}}}
            return spec

        return inner

    for bad in (["BREAKER"], ["IFVG"]):
        monkeypatch.setattr(models, "load_spec", patched(bad))
        with pytest.raises(ValueError, match="BREAKER is not available"):
            EntryConfig.from_spec()
