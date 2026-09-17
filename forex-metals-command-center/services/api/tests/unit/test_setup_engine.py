"""Hand-built setup state machine scenarios: every transition, invalidation and expiry, both directions."""

from datetime import timedelta

import pytest

from app.domain.enums import (
    Direction,
    Po3Phase,
    QualifierStatus,
    SetupState,
    SetupStep,
    SetupStepStatus,
    Timeframe,
)
from app.services.setup_state.engine import bias_at
from app.services.setup_state.models import BiasPoint, SetupConfig
from app.services.setup_state.po3 import po3_state
from tests.session_helpers import candle, utc
from tests.setup_helpers import Scenario, happy

S = SetupState
HAPPY = [
    S.DISCOVERED,
    S.WATCH,
    S.SETUP_FORMING,
    S.LIQUIDITY_EVENT,
    S.WAITING_FOR_MSS,
    S.SETUP_ARMED,
    S.WAITING_FOR_RETRACEMENT,
    S.ENTRY_ZONE_APPROACHING,
    S.ENTRY_ZONE_TOUCHED,
    S.WAITING_FOR_CONFIRMATION,
]


def states(result, setup=0):
    sid = result.setups[setup].id
    return [(e.state, e.time) for e in result.events if e.setup_id == sid]


@pytest.mark.parametrize("flip", [False, True])
def test_happy_path_reaches_waiting_for_confirmation_and_never_ready(flip):
    candles, result = happy().run(flip)
    assert [s for s, _ in states(result)] == HAPPY
    at = {s: t for s, t in states(result)}
    idx = {c.open_time: i for i, c in enumerate(candles)}
    assert [idx[at[s]] for s in HAPPY] == [0, 0, 0, 20, 21, 24, 25, 27, 28, 29]
    [setup] = result.setups
    assert setup.direction is (Direction.BEARISH if flip else Direction.BULLISH)
    assert setup.state is S.WAITING_FOR_CONFIRMATION and not setup.terminal
    assert setup.target is not None and setup.target.pool_id == "TARGET"
    assert setup.liquidity_event is not None and setup.liquidity_event.pool_id == "OPPOSITE"
    assert setup.mss is not None and setup.mss.displacement_qualifier is QualifierStatus.PRESENT
    assert setup.protective_level == pytest.approx(4060 - 2028.9 if flip else 2028.9)
    assert setup.zone_ids == ["FVG:25"] and setup.touched_zone_id == "FVG:25"
    assert setup.next_required_event == "LTF_REFINEMENT/NO_WICK_REBALANCE/M15_CLOSE confirmation (STANDARD)"
    status = {s.step: s.status for s in setup.steps}
    assert all(status[k] is SetupStepStatus.DONE for k in list(SetupStep)[:7])
    assert status[SetupStep.LTF_CONFIRMATION] is SetupStepStatus.PENDING
    assert status[SetupStep.RISK] is SetupStepStatus.NOT_EVALUATED and setup.entry_plan is None


def test_liquidity_run_invalidates_before_the_break():
    _, result = happy(rows={21: (2029.6, 2029.7, 2028.3, 2028.5)}).run()
    assert [s for s, _ in states(result)][-2:] == [S.LIQUIDITY_EVENT, S.INVALIDATED]
    assert result.setups[0].reason == "closed beyond the swept extreme (liquidity ran)"


def test_no_break_expires_after_the_window():
    candles, result = happy(breaks={}, count=40).run()
    expired = [t for s, t in states(result) if s is S.EXPIRED]
    assert [candles.index(next(c for c in candles if c.open_time == expired[0]))] == [32]  # 20 + 12
    assert result.setups[0].reason == "no confirmation break within 12 bars"


def test_break_without_displacement_does_not_arm():
    _, result = happy(breaks={24: QualifierStatus.ABSENT}, count=40).run()
    assert S.SETUP_ARMED not in [s for s, _ in states(result)]
    _, relaxed = happy(breaks={24: QualifierStatus.ABSENT}).with_cfg(mss_require_displacement=False).run()
    assert S.SETUP_ARMED in [s for s, _ in states(relaxed)]


def test_no_leg_fvg_expires_after_the_grace_window():
    _, result = happy(zones={}).run()
    assert [s for s, _ in states(result)][-2:] == [S.WAITING_FOR_RETRACEMENT, S.EXPIRED]
    assert result.setups[0].reason == "no FVG formed in the displacement leg"


def test_zone_created_after_the_grace_window_is_not_a_leg_fvg():
    _, result = happy(zones={27: (2030.5, 2031.5)}).run()
    assert result.setups[0].state is S.EXPIRED and result.setups[0].zone_ids == []


def test_close_beyond_the_protective_extreme_invalidates():
    _, result = happy(rows={26: (2033.8, 2034.0, 2028.0, 2028.5)}).run()
    assert result.setups[0].state is S.INVALIDATED
    assert result.setups[0].reason == "closed beyond the protective extreme"


def test_target_taken_before_entry_invalidates():
    _, result = happy(target_taken_at=26).run()
    assert result.setups[0].reason == "target liquidity reached before a confirmed entry (do not chase)"


def test_every_leg_fvg_invalidated_invalidates():
    _, result = happy(zone_invalidated_at=26).run()
    assert result.setups[0].reason == "every retracement zone was invalidated"


def test_no_touch_within_the_retracement_window_expires():
    rows = {i: (2034.0, 2034.5, 2033.5, 2034.0) for i in range(26, 30)}
    _, result = happy(rows=rows).with_cfg(retracement_window_bars=4).run()
    assert result.setups[0].state is S.EXPIRED and result.setups[0].reason == "no retracement within 4 bars"


def test_htf_bias_change_invalidates_and_no_setup_restarts_while_bias_is_mixed():
    scenario = happy(count=30)
    scenario.bias.append((Timeframe.H1, Direction.BEARISH, 22))
    _, result = scenario.run()
    assert [s for s, _ in states(result)][-1] is S.INVALIDATED
    assert result.setups[0].reason == "HTF bias changed to NONE"
    assert len(result.setups) == 1


def test_a_new_setup_starts_on_a_later_candle_after_a_terminal_one():
    _, result = happy(rows={21: (2029.6, 2029.7, 2028.3, 2028.5)}, count=24, zones={}, breaks={}).run()
    assert len(result.setups) == 2 and result.setups[0].terminal and not result.setups[1].terminal
    assert result.setups[1].discovered_at > result.setups[0].state_changed_at


def test_trading_day_end_expires_a_setup_after_its_liquidity_event():
    # 21:00 UTC = 16:00 EST; the next open slot after the 17:00 roll is 23:00 UTC (new trading day).
    scenario = Scenario(start=utc(2024, 1, 9, 21, 0), count=8, sweeps={1: 2028.9}, breaks={}, zones={})
    scenario.rows = {1: (2030.0, 2030.5, 2028.9, 2029.6)}
    _, result = scenario.run()
    [expired] = [t for s, t in states(result) if s is S.EXPIRED]
    assert expired == utc(2024, 1, 9, 23, 0) and result.setups[0].reason == "trading day ended"
    _, kept = (
        Scenario(start=utc(2024, 1, 9, 21, 0), count=8, sweeps={1: 2028.9}, breaks={}, zones={})
        .with_cfg(expire_at_trading_day_end=False)
        .run()
    )
    assert S.EXPIRED not in [s for s, _ in states(kept)]


def test_watch_and_forming_follow_price_and_target():
    rows = {5: (2031.5, 2032.0, 2031.0, 2031.5), 6: (2030.0, 2030.5, 2029.5, 2030.0)}
    _, result = Scenario(rows=rows, count=10, sweeps={}, breaks={}, zones={}, target_taken_at=8).run()
    assert [s for s, _ in states(result)] == [
        S.DISCOVERED,
        S.WATCH,
        S.SETUP_FORMING,
        S.WATCH,  # 5: moved away from the Asia low
        S.SETUP_FORMING,  # 6: back near it
        S.DISCOVERED,  # 8: the only target was taken
    ]


def test_no_bias_no_setup():
    _, result = Scenario(
        bias=[(Timeframe.H4, Direction.BULLISH, None)], count=10, sweeps={}, breaks={}, zones={}
    ).run()
    assert result.setups == [] and result.events == []


def test_bias_at_requires_every_timeframe_to_agree():
    t0 = utc(2024, 1, 9, 0, 0)
    pts = [
        BiasPoint(timeframe=Timeframe.H4, direction=Direction.BULLISH, known_at=t0, event_id="a"),
        BiasPoint(timeframe=Timeframe.H1, direction=Direction.BEARISH, known_at=t0, event_id="b"),
        BiasPoint(
            timeframe=Timeframe.H1,
            direction=Direction.BULLISH,
            known_at=t0 + timedelta(hours=1),
            event_id="c",
        ),
    ]
    tfs = [Timeframe.H4, Timeframe.H1]
    assert bias_at(pts, tfs, t0) is None
    assert bias_at(pts, tfs, t0 + timedelta(hours=1)) is Direction.BULLISH
    assert bias_at(pts, tfs, t0 - timedelta(minutes=1)) is None


def test_config_refuses_bos_as_a_confirmation_break(monkeypatch):
    from app.services.setup_state import models

    real = models.load_spec

    def patched(name):
        spec = real(name)
        if name == "setup":
            spec = {**spec, "mss": {**spec["mss"], "breakTypes": ["BOS"]}}
        return spec

    monkeypatch.setattr(models, "load_spec", patched)
    with pytest.raises(ValueError, match="reversal"):
        SetupConfig.from_spec()


# --- PO3 ---------------------------------------------------------------------------------------------

CFG = SetupConfig.from_spec()
T = utc(2024, 1, 9, 5, 0)


def day(rows):
    return [candle(T + timedelta(minutes=15 * k), r) for k, r in enumerate(rows)]


@pytest.mark.parametrize(
    ("rows", "bias", "phase"),
    [
        ([(2030, 2031, 2029, 2030.5)], Direction.BULLISH, Po3Phase.ACCUMULATION),
        ([(2030, 2030.5, 2026, 2027)], Direction.BULLISH, Po3Phase.MANIPULATION),  # low 4 below open (>= 3)
        ([(2030, 2030.5, 2026, 2027), (2027, 2034, 2027, 2033.5)], Direction.BULLISH, Po3Phase.DISTRIBUTION),
        (
            [(2030, 2034, 2029.5, 2033.5)],
            Direction.BULLISH,
            Po3Phase.UNCLEAR,
        ),  # expansion without manipulation
        (
            [(2030, 2034, 2029.5, 2033.5), (2033.5, 2033.6, 2026.5, 2026.8)],
            Direction.BEARISH,
            Po3Phase.DISTRIBUTION,
        ),
        ([(2030, 2031, 2029, 2030.5)], None, Po3Phase.UNCLEAR),
    ],
)
def test_po3_phases(rows, bias, phase):
    # ADR 20 -> manipulation and distribution distances are 3.0.
    assert po3_state(day(rows), T.date(), 2030.0, 20.0, bias, CFG).phase is phase


def test_po3_needs_daily_open_and_adr():
    assert (
        po3_state(day([(2030, 2031, 2029, 2030)]), T.date(), None, 20.0, Direction.BULLISH, CFG).phase
        is Po3Phase.UNCLEAR
    )
    assert (
        po3_state(day([(2030, 2031, 2029, 2030)]), T.date(), 2030.0, None, Direction.BULLISH, CFG).phase
        is Po3Phase.UNCLEAR
    )
