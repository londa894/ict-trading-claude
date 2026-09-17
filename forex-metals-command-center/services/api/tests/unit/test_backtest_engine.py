"""Backtest runner (Phase 18) with a scripted plan source: sequencing, overlap, expiry, stats, Monte Carlo."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from app.domain.candle import Candle
from app.domain.enums import (
    BacktestTradeStatus,
    DataQuality,
    Direction,
    EntryMode,
    EntryModel,
    SampleSizeLabel,
    SetupState,
    SetupType,
    Timeframe,
)
from app.services.analytics.models import AnalyticsConfig
from app.services.backtest import models as backtest_models
from app.services.backtest.engine import monte_carlo, run_variant, summarize_variant
from app.services.backtest.models import BacktestConfig, BacktestRequest, BacktestVariant
from app.services.paper.models import AssumedCosts

T0 = datetime(2024, 4, 15, 12, 0, tzinfo=UTC)  # Monday
M5, M15 = timedelta(minutes=5), timedelta(minutes=15)
FREE = AssumedCosts(spread=0, slippage=0, commission=0)
ACFG = AnalyticsConfig.from_spec()
CFG = BacktestConfig.from_spec()


def candle(tf: Timeframe, t: datetime, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe=tf,
        open_time=t,
        close_time=t + tf.duration,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=None,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )


def flat_m5(n: int, price: float = 2000.0) -> list[Candle]:
    return [candle(Timeframe.M5, T0 + M5 * i, price, price + 1, price - 1, price) for i in range(n)]


def m15_steps(n: int) -> list[Candle]:
    return [candle(Timeframe.M15, T0 + M15 * i, 2000, 2001, 1999, 2000) for i in range(n)]


def plan_at(
    confirmed_open: datetime, entry=2000.0, stop=1990.0, tp1=2020.0, direction=Direction.BULLISH, sid="S1"
):
    plan = NS(
        confirmed_at=confirmed_open,
        entry=entry,
        stop=stop,
        tp1=tp1,
        direction=direction,
        model=EntryModel.M15_CLOSE,
    )
    return NS(id=sid, setup_type=SetupType.LIQUIDITY_SWEEP_MSS, state=SetupState.BLOCKED, entry_plan=plan)


def scripted(schedule: dict[datetime, list], eligible=lambda t: True, calls=None):
    """analyze(t) returns the setups listed for t (only what 'live' would know at t)."""

    async def analyze(t):
        if calls is not None:
            calls.append(t)
        return NS(eligible_for_decision=eligible(t), setups=schedule.get(t, []))

    return analyze


async def run(schedule, m5, steps, **kw):
    return await run_variant(
        kw.pop("variant", BacktestVariant()),
        scripted(schedule, **kw.pop("script", {})),
        steps,
        m5,
        Timeframe.M15,
        kw.pop("costs", FREE),
        kw.pop("expiry", 12),
        kw.pop("end", T0 + timedelta(days=1)),
        **kw,
    )


# --- request / config ----------------------------------------------------------------------------------


def test_request_validation():
    ok = BacktestRequest(symbol="XAUUSD", start=T0, end=T0 + timedelta(days=2))
    assert [v.name for v in ok.variants] == ["A"] and ok.segments == 1
    for bad in (
        {"end": T0},
        {"end": T0 + timedelta(days=CFG.max_range_days + 1)},
        {"out_of_sample_from": T0 - timedelta(hours=1)},
        {"variants": [BacktestVariant(name="A"), BacktestVariant(name="A")]},
        {"variants": [BacktestVariant(name=n) for n in "ABC"]},
        {"segments": CFG.max_segments + 1},
    ):
        with pytest.raises(ValidationError):
            BacktestRequest(**{"symbol": "XAUUSD", "start": T0, "end": T0 + timedelta(days=1), **bad})
    with pytest.raises(ValidationError):
        BacktestVariant(cost_multiplier=CFG.max_cost_multiplier + 1)


def test_config_guard(monkeypatch):
    real = backtest_models.load_spec

    def patched(name):
        data = json.loads(json.dumps(real(name)))
        data["maxConcurrentPositions"] = 2
        return data

    monkeypatch.setattr(backtest_models, "load_spec", patched)
    with pytest.raises(ValueError):
        BacktestConfig.from_spec()


# --- sequencing ----------------------------------------------------------------------------------------


async def test_plan_is_taken_only_on_its_confirmation_step_and_filled_by_later_bars():
    m5 = flat_m5(40)
    m5[10] = candle(Timeframe.M5, T0 + M5 * 10, 2000, 2021, 1999, 2020)  # target bar after the plan
    plan = plan_at(T0)  # confirmed by the M15 candle opening T0 → visible at T0 + 15m
    calls = []
    funnel, trades = await run(
        {T0 + M15: [plan], T0 + M15 * 2: [plan]}, m5, m15_steps(8), script={"calls": calls}
    )
    assert calls[0] == T0 + M15 and calls == sorted(calls)  # sequential closes only
    assert funnel.plans_confirmed == 1 and funnel.fills == 1 and funnel.closed == 1
    t = trades[0]
    assert (
        t.status is BacktestTradeStatus.CLOSED and t.filled_at == T0 + M15 and t.exit_reason.value == "TARGET"
    )
    assert t.net_r_multiple == 2.0 and t.confirmed_at == T0 + M15


async def test_late_visibility_is_not_traded():
    plan = plan_at(T0)  # would only be new at T0+15m; it first appears at T0+30m (as if seen late)
    funnel, trades = await run({T0 + M15 * 2: [plan]}, flat_m5(40), m15_steps(8))
    assert funnel.plans_confirmed == 0 and trades == []


async def test_ineligible_steps_take_no_plans():
    plan = plan_at(T0)
    funnel, trades = await run(
        {T0 + M15: [plan]}, flat_m5(40), m15_steps(4), script={"eligible": lambda t: False}
    )
    assert funnel.ineligible_steps == 4 and trades == [] and funnel.plans_confirmed == 0


async def test_one_position_at_a_time_expiry_and_open_at_end():
    far = plan_at(T0, entry=1900.0, stop=1890.0, tp1=1950.0, sid="FAR")  # limit never reached
    second = plan_at(T0 + M15, sid="S2")
    funnel, trades = await run(
        {T0 + M15: [far], T0 + M15 * 2: [second]}, flat_m5(60), m15_steps(12), expiry=12
    )
    statuses = [t.status for t in trades]
    assert BacktestTradeStatus.SKIPPED_OVERLAP in statuses and BacktestTradeStatus.EXPIRED in statuses
    assert funnel.plans_skipped_overlap == 1 and funnel.expired == 1
    open_plan = plan_at(T0, entry=2000.0, stop=1980.0, tp1=2100.0)
    funnel, trades = await run(
        {T0 + M15: [open_plan]}, flat_m5(20), m15_steps(4), end=T0 + timedelta(hours=1)
    )
    assert (
        trades[-1].status is BacktestTradeStatus.OPEN_AT_END
        and funnel.open_at_end == 1
        and funnel.closed == 0
    )


async def test_costs_scale_with_the_variant_and_ambiguity_is_flagged():
    m5 = flat_m5(40)
    m5[4] = candle(Timeframe.M5, T0 + M5 * 4, 2000, 2030, 1980, 2000)  # stop and target in one bar
    plan = plan_at(T0, entry=2001.0)
    costs = AssumedCosts(spread=0.4, slippage=0.1, commission=0.5)
    _, trades = await run(
        {T0 + M15: [plan]}, m5, m15_steps(8), costs=costs, variant=BacktestVariant(cost_multiplier=2)
    )
    t = trades[0]
    assert t.ambiguous and t.exit_reason.value == "STOP" and t.exit_price == pytest.approx(1990 - 0.2)
    assert t.net_r_multiple < t.r_multiple  # commission x2 per side


async def test_progress_and_cancellation():
    seen = []

    async def progress(done, total):
        seen.append((done, total))

    await run({}, flat_m5(10), m15_steps(25), progress=progress, progress_every=10)
    assert seen == [(10, 25), (20, 25), (25, 25)]
    with pytest.raises(RuntimeError, match="cancelled"):
        await run({}, flat_m5(10), m15_steps(5), cancelled=lambda: True)


# --- summary -------------------------------------------------------------------------------------------


async def test_summary_segments_split_and_monte_carlo():
    m5 = flat_m5(300)
    schedule = {}
    for k in range(6):
        t_open = T0 + timedelta(hours=2 * k)
        schedule[t_open + M15] = [plan_at(t_open, sid=f"S{k}")]
        hit = 3 + int((2 * k * 60) / 5)
        win = k % 2 == 0
        m5[hit] = candle(
            Timeframe.M5, T0 + M5 * hit, 2000, 2021 if win else 2001, 1999 if win else 1989, 2000
        )
    funnel, trades = await run(schedule, m5, m15_steps(60), end=T0 + timedelta(hours=15))
    assert funnel.closed == 6
    res = summarize_variant(
        BacktestVariant(),
        funnel,
        trades,
        symbol="XAUUSD",
        start=T0,
        end=T0 + timedelta(hours=12),
        segments=2,
        out_of_sample_from=T0 + timedelta(hours=6),
        synthetic=False,
        version="v",
        cfg=CFG,
        acfg=ACFG,
        tol=0.1,
    )
    assert res.stats.count == 6 and res.stats.wins == 3 and res.stats.total_r == pytest.approx(3.0)
    assert [s.stats.count for s in res.segments] == [3, 3]
    assert res.in_sample.count == 3 and res.out_of_sample.count == 3
    mc = res.monte_carlo
    assert mc is not None and mc.trades == 6 and mc.label is SampleSizeLabel.INSUFFICIENT
    assert (
        mc.total_r_p05 <= mc.total_r_p50 <= mc.total_r_p95 and mc.max_drawdown_r_p50 <= mc.max_drawdown_r_p95
    )
    again = monte_carlo(
        [t.net_r_multiple for t in trades], CFG.monte_carlo_resamples, CFG.monte_carlo_seed, ACFG
    )
    assert again == mc  # seeded, deterministic
    assert monte_carlo([1.0], 100, 1, ACFG) is None
    assert {b.dimension for b in res.breakdowns} == {
        "DIRECTION",
        "ENTRY_MODEL",
        "SETUP_TYPE",
        "RESULT",
        "DAY_OF_WEEK",
    }


def test_entry_modes_exist():
    assert {m.value for m in EntryMode} == {"CONSERVATIVE", "STANDARD", "AGGRESSIVE"}


async def test_ineligibility_reasons_are_counted():
    async def analyze(t):
        return NS(eligible_for_decision=False, setups=[], ineligibility=[NS(value="DATA_SYNTHETIC")])

    funnel, _ = await run_variant(
        BacktestVariant(), analyze, m15_steps(3), flat_m5(10), Timeframe.M15, FREE, 12, T0 + timedelta(days=1)
    )
    assert funnel.ineligible_steps == 3 and funnel.ineligible_reasons == {"DATA_SYNTHETIC": 3}
