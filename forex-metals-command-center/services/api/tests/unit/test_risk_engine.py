"""Risk locks, budget and position sizing (Phase 9)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import load_spec
from app.domain.enums import (
    Blocker,
    Direction,
    PositionSizeStatus,
    RiskLock,
    RiskProfileName,
    RiskStatus,
    RiskWarning,
)
from app.services.risk import models as risk_models
from app.services.risk.engine import assess, conversion_factor, floor_to_step, spec_consistent
from app.services.risk.models import OpenPosition, RiskConfig, RiskProfileFile, TradeInput
from tests.risk_helpers import (
    CFG,
    LONG_TRADE,
    NOW,
    TODAY,
    account,
    custom,
    rate,
    spec,
    state,
    volatility_candles,
)


def run(**over: Any):
    kw: dict[str, Any] = dict(
        account=account(),
        state=state(),
        spec=spec(),
        spec_verified=False,
        rates=[],
        trade=LONG_TRADE,
        candles=volatility_candles(),
        trading_day=TODAY,
        cfg=CFG,
    )
    kw.update(over)
    return assess("XAUUSD", NOW, **kw)


def locks(a) -> set[RiskLock]:
    return {x.lock for x in a.locks}


def test_xauusd_sizing_from_a_user_spec():
    a = run()
    p = a.position
    assert a.status is RiskStatus.WITHIN_LIMITS and a.blockers == [] and a.locks == []
    assert a.size_status is PositionSizeStatus.SIZED_FROM_USER_SPEC
    assert a.budget is not None and a.budget.effective_risk_amount == 100.0  # 1% of 10,000 (STANDARD)
    assert p is not None
    assert (p.price_distance, p.points, p.pips, p.spread, p.sizing_distance) == (3.2, 320.0, 32.0, 0.3, 3.5)
    assert p.risk_per_volume == 350.0  # 3.5 / 0.01 x 1.0 USD per tick
    assert p.volume == 0.28  # floor(100 / 350 = 0.2857) to the 0.01 step: never rounded up
    assert (p.risk_amount, p.risk_amount_without_spread, p.risk_pct) == (98.0, 89.6, 0.98)
    assert p.margin_required == 568.96  # 0.28 x 100 oz x 2032 / 100
    assert RiskWarning.USER_SUPPLIED_SPEC in a.warnings and RiskWarning.NEWS_NOT_EVALUATED in a.warnings
    assert a.authority == "NOT_AUTHORIZED" and a.news == "NOT_EVALUATED"
    body = a.model_dump_json(by_alias=True)
    assert "LONG" not in body and "SHORT" not in body


def test_catalog_spec_would_be_verified():
    assert run(spec_verified=True).size_status is PositionSizeStatus.VERIFIED


@pytest.mark.parametrize(
    ("over", "warning", "blocker"),
    [
        ({"spec": None}, None, Blocker.POSITION_SIZE_UNVERIFIED),
        ({"spec": spec(tick_value=0.1)}, RiskWarning.SPEC_INCONSISTENT, Blocker.POSITION_SIZE_UNVERIFIED),
        (
            {"account": account(currency="EUR")},
            RiskWarning.CONVERSION_UNAVAILABLE,
            Blocker.POSITION_SIZE_UNVERIFIED,
        ),
    ],
)
def test_unknown_or_inconsistent_specs_are_never_guessed(over, warning, blocker):
    a = run(**over)
    assert (
        a.status is RiskStatus.SIZE_UNVERIFIED
        and a.size_status is PositionSizeStatus.POSITION_SIZE_UNVERIFIED
    )
    assert a.position is not None and a.position.volume is None and a.position.risk_amount is None
    assert a.blockers == [blocker]
    if warning:
        assert warning in a.warnings


def test_currency_conversion_uses_only_fresh_direct_or_inverse_rates():
    eur = account(currency="EUR")
    direct = run(account=eur, rates=[rate("USD", "EUR", 0.9)])
    inverse = run(account=eur, rates=[rate("EUR", "USD", 1 / 0.9)])
    assert direct.position.risk_per_volume == inverse.position.risk_per_volume == 315.0
    assert direct.budget.effective_risk_amount == 100.0 and direct.position.volume == 0.31
    stale = run(account=eur, rates=[rate("USD", "EUR", 0.9, age_hours=25)])
    future = run(account=eur, rates=[rate("USD", "EUR", 0.9, age_hours=-1)])
    assert stale.status is future.status is RiskStatus.SIZE_UNVERIFIED
    assert conversion_factor("USD", "USD", [], NOW, 24) == 1.0
    assert conversion_factor("JPY", "USD", [rate("USD", "EUR", 0.9)], NOW, 24) is None  # never chained


def test_volume_floors_and_minimum_volume():
    assert (
        floor_to_step(0.2857, 0.01) == 0.28
        and floor_to_step(0.3, 0.1) == 0.3
        and floor_to_step(1.99, 0.5) == 1.5
    )
    a = run(account=account(balance=500.0), spec=spec(min_volume=0.1, volume_step=0.1))
    assert a.status is RiskStatus.LOCKED and locks(a) == {RiskLock.BELOW_MIN_VOLUME}
    assert a.position.volume is None and a.blockers == [Blocker.RISK_LOCKED]
    assert "35.0" in a.locks[0].detail  # min volume 0.1 would risk 35 against a 5 budget


def test_spec_consistency_rule():
    assert spec_consistent(spec(), 1.0)
    assert spec_consistent(spec(symbol="XAUUSD", contract_size=100000, tick_size=0.001, tick_value=100), 1.0)
    assert not spec_consistent(spec(tick_value=1.02), 1.0)


def test_spread_rules():
    unsafe = run(spec=spec(typical_spread=0.5), trade=TradeInput(Direction.BULLISH, 2032.0, 2029.0))
    assert locks(unsafe) == {RiskLock.UNSAFE_SPREAD}  # 0.5 is 16.7% of a 3.0 stop
    assert unsafe.blockers == [Blocker.RISK_LOCKED, Blocker.UNSAFE_SPREAD]
    unknown = run(spec=spec(typical_spread=None))
    assert RiskWarning.SPREAD_UNKNOWN in unknown.warnings and unknown.position.sizing_distance == 3.2


@pytest.mark.parametrize(
    "trade",
    [
        TradeInput(Direction.BULLISH, 2032.0, 2032.0),
        TradeInput(Direction.BULLISH, 2032.0, 2035.0),
        TradeInput(Direction.BEARISH, 2032.0, 2030.0),
    ],
)
def test_invalid_stop_locks(trade):
    a = run(trade=trade)
    assert locks(a) == {RiskLock.INVALID_STOP} and a.position.volume is None


def test_margin_and_leverage():
    assert RiskWarning.LEVERAGE_NOT_SET in run(account=account(leverage=None)).warnings
    tight = run(account=account(leverage=1.0), trade=TradeInput(Direction.BULLISH, 2032.0, 2031.0))
    assert RiskLock.INSUFFICIENT_MARGIN in locks(tight)


@pytest.mark.parametrize(
    ("st", "lock"),
    [
        (dict(realized_pnl_today=-310.0), RiskLock.DAILY_LOSS_LIMIT),  # 3% of the 10,310 day start = 309.3
        (dict(realized_pnl_week=-640.0), RiskLock.WEEKLY_LOSS_LIMIT),  # 6% of 10,640 = 638.4
        (dict(trades_today=3), RiskLock.MAX_TRADES_PER_DAY),
        (dict(consecutive_losses=3), RiskLock.CONSECUTIVE_LOSSES),
    ],
)
def test_account_locks(st, lock):
    a = run(account=account(balance=10_000.0), state=state(**st))
    assert lock in locks(a) and a.status is RiskStatus.LOCKED and Blocker.RISK_LOCKED in a.blockers


def test_open_risk_and_position_count_locks():
    two = [OpenPosition(symbol="EURUSD", direction="BULLISH", risk_amount=100.0, in_loss=False)] * 2
    a = run(state=state(open_positions=two))
    assert {RiskLock.MAX_OPEN_RISK, RiskLock.MAX_POSITIONS} <= locks(a)
    assert a.budget.open_risk == 200.0 and a.budget.effective_risk_amount == 0.0
    assert a.position.volume is None and a.position.detail == "no risk budget left"


def test_limits_reduce_risk_before_they_lock():
    a = run(state=state(realized_pnl_today=-250.0))
    # day start 10,250 -> daily limit 307.5; 307.5 - 250 = 57.5 left, below the 100 per-trade amount
    assert a.status is RiskStatus.WITHIN_LIMITS and RiskWarning.RISK_REDUCED_BY_LIMITS in a.warnings
    assert a.budget.effective_risk_amount == 57.5 and a.position.risk_amount <= 57.5


def test_stale_account_state_locks():
    a = run(state=state(trading_day=date(2024, 1, 8)))
    assert locks(a) == {RiskLock.ACCOUNT_STATE_STALE} and "2024-01-08" in a.locks[0].detail


def test_prop_rules():
    prop = {"starting_balance": 10_000.0, "max_daily_drawdown_pct": 2.0, "max_total_drawdown_pct": 5.0}
    daily = run(account=account(balance=9_800.0, prop_rules=prop), state=state(realized_pnl_today=-200.0))
    assert RiskLock.PROP_DAILY_DRAWDOWN in locks(daily)
    total = run(account=account(balance=9_500.0, prop_rules=prop))
    assert RiskLock.PROP_TOTAL_DRAWDOWN in locks(total)
    near = run(account=account(balance=9_550.0, prop_rules=prop))
    assert near.budget.prop_remaining == 50.0 and near.budget.effective_risk_amount == 50.0


def test_adding_to_a_loser_locks_and_correlated_exposure_warns():
    loser = OpenPosition(symbol="XAUUSD", direction="BULLISH", risk_amount=10.0, in_loss=True)
    assert RiskLock.ADDING_TO_LOSER in locks(run(state=state(open_positions=[loser])))
    silver = OpenPosition(symbol="XAGUSD", direction="BULLISH", risk_amount=10.0, in_loss=False)
    a = run(state=state(open_positions=[silver]))
    assert RiskWarning.CORRELATED_EXPOSURE in a.warnings and a.status is RiskStatus.WITHIN_LIMITS
    usd_long = OpenPosition(symbol="USDJPY", direction="BULLISH", risk_amount=10.0, in_loss=False)
    assert RiskWarning.CORRELATED_EXPOSURE not in run(state=state(open_positions=[usd_long])).warnings


def test_volatility_lock_and_unavailable():
    spike = run(candles=volatility_candles(spike_range=3.0))
    assert RiskLock.VOLATILITY in locks(spike) and spike.volatility.ratio == 3.0
    calm = run(candles=volatility_candles(spike_range=2.0))
    assert RiskLock.VOLATILITY not in locks(calm) and calm.volatility.ratio == 2.0
    assert RiskWarning.VOLATILITY_UNAVAILABLE in run(candles=volatility_candles(bars=50)).warnings
    assert RiskWarning.VOLATILITY_NOT_CHECKED in run(candles=None).warnings


def test_account_only_assessment_is_clear():
    a = run(trade=None)
    assert a.status is RiskStatus.CLEAR and a.position is None and a.size_status is None and a.blockers == []


def test_missing_state_skips_account_locks_with_a_warning():
    a = run(state=None)
    assert RiskWarning.ACCOUNT_STATE_NOT_PROVIDED in a.warnings and a.status is RiskStatus.WITHIN_LIMITS


def test_custom_profiles_and_hard_limits():
    ok = run(account=custom(risk_per_trade_pct=0.5))
    assert ok.limits.risk_per_trade_pct == 0.5 and ok.budget.risk_per_trade_amount == 50.0
    over = run(account=custom(risk_per_trade_pct=5.0))
    assert over.status is RiskStatus.INVALID_PROFILE and over.blockers == [Blocker.RISK_PROFILE_INVALID]
    assert "risk_per_trade_pct" in over.profile_error and over.position is None
    with pytest.raises(ValidationError, match="CUSTOM"):
        account(risk_per_trade_pct=0.5)  # a preset cannot be edited
    with pytest.raises(ValidationError, match="every limit"):
        account(profile="CUSTOM", risk_per_trade_pct=0.5)


def test_presets_come_from_the_spec_and_respect_hard_limits(monkeypatch):
    spec_json = load_spec("risk")
    for name in ("CONSERVATIVE", "STANDARD", "AGGRESSIVE"):
        assert (
            CFG.profiles[RiskProfileName(name)].risk_per_trade_pct
            == spec_json["profiles"][name]["riskPerTradePct"]
        )
    bad = {
        **spec_json,
        "profiles": {
            **spec_json["profiles"],
            "AGGRESSIVE": {**spec_json["profiles"]["AGGRESSIVE"], "riskPerTradePct": 3.0},
        },
    }
    monkeypatch.setattr(risk_models, "load_spec", lambda _name: bad)
    with pytest.raises(ValueError, match="exceeds the hard limits"):
        RiskConfig.from_spec()


def test_profile_file_validation():
    good = {
        "account": {"balance": 1000, "currency": "USD", "profile": "CONSERVATIVE"},
        "state": {
            "tradingDay": "2024-01-09",
            "realizedPnlToday": 0,
            "realizedPnlWeek": 0,
            "tradesToday": 0,
            "consecutiveLosses": 0,
        },
        "instrumentSpecs": {"XAUUSD": spec().model_dump(by_alias=True)},
    }
    assert RiskProfileFile.model_validate(good).instrument_specs["XAUUSD"].contract_size == 100
    with pytest.raises(ValidationError):
        RiskProfileFile.model_validate(
            {**good, "instrumentSpecs": {"XAGUSD": spec().model_dump(by_alias=True)}}
        )
    with pytest.raises(ValidationError):
        RiskProfileFile.model_validate({**good, "account": {**good["account"], "currency": "usd"}})
    bad_position = {
        **good["state"],
        "openPositions": [{"symbol": "BTCUSD", "direction": "BULLISH", "riskAmount": 1, "inLoss": False}],
    }
    with pytest.raises(ValidationError):
        RiskProfileFile.model_validate({**good, "state": bad_position})


def test_committed_example_profile_is_valid():
    path = Path(__file__).resolve().parents[4] / "config" / "risk_profile.example.json"
    example = RiskProfileFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert example.account.profile is RiskProfileName.STANDARD and "XAUUSD" in example.instrument_specs
