"""Property tests: sized risk never exceeds any cap, and losses can only shrink it (no martingale)."""

import random
from decimal import Decimal

import pytest

from app.domain.enums import Direction, PositionSizeStatus, RiskLock, RiskStatus
from app.services.risk.engine import assess
from app.services.risk.models import OpenPosition, TradeInput
from tests.risk_helpers import CFG, NOW, TODAY, account, rate, spec, state, volatility_candles

SEEDS = range(40)
TRADE_LOCKS = {
    RiskLock.INVALID_STOP,
    RiskLock.ADDING_TO_LOSER,
    RiskLock.UNSAFE_SPREAD,
    RiskLock.INSUFFICIENT_MARGIN,
    RiskLock.BELOW_MIN_VOLUME,
}
CANDLES = volatility_candles()


def random_case(rng: random.Random):
    profile = rng.choice(["CONSERVATIVE", "STANDARD", "AGGRESSIVE"])
    currency = rng.choice(["USD", "EUR"])
    acct = account(balance=round(rng.uniform(500, 200_000), 2), currency=currency, profile=profile)
    positions = [
        OpenPosition(
            symbol=rng.choice(["EURUSD", "USDJPY", "XAGUSD"]),
            direction=rng.choice(list(Direction)),
            risk_amount=round(rng.uniform(0, acct.balance * 0.01), 2),
            in_loss=False,
        )
        for _ in range(rng.randint(0, 1))
    ]
    st = state(
        realized_pnl_today=round(rng.uniform(-0.02, 0.02) * acct.balance, 2),
        realized_pnl_week=round(rng.uniform(-0.04, 0.04) * acct.balance, 2),
        trades_today=rng.randint(0, 1),
        consecutive_losses=rng.randint(0, 1),
        open_positions=positions,
    )
    entry = round(rng.uniform(1800, 2600), 2)
    distance = round(rng.uniform(0.5, 25), 2)
    direction = rng.choice(list(Direction))
    stop = entry - distance if direction is Direction.BULLISH else entry + distance
    step = rng.choice([0.01, 0.1])
    sp = spec(typical_spread=round(rng.uniform(0, 0.04) * distance, 3), volume_step=step, min_volume=step)
    return acct, st, sp, TradeInput(direction, entry, round(stop, 2))


def run(acct, st, sp, trade):
    return assess(
        "XAUUSD",
        NOW,
        account=acct,
        state=st,
        spec=sp,
        spec_verified=False,
        rates=[rate("USD", "EUR", 0.92)],
        trade=trade,
        candles=CANDLES,
        trading_day=TODAY,
        cfg=CFG,
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_sized_risk_respects_every_cap(seed):
    acct, st, sp, trade = random_case(random.Random(seed))  # noqa: S311 - deterministic test data
    a = run(acct, st, sp, trade)
    assert a.authority == "NOT_AUTHORIZED" and a.size_status is PositionSizeStatus.SIZED_FROM_USER_SPEC
    b, p = a.budget, a.position
    assert b is not None and p is not None
    caps = [b.risk_per_trade_amount, b.daily_remaining, b.weekly_remaining, b.open_risk_remaining]
    assert b.effective_risk_amount == pytest.approx(max(0.0, min(caps)), abs=0.011)
    if p.volume is not None:
        assert a.status is RiskStatus.WITHIN_LIMITS or a.locks
        assert p.risk_amount is not None and p.risk_amount <= b.effective_risk_amount + 0.01
        assert p.risk_pct is not None and p.risk_pct <= a.limits.risk_per_trade_pct + 1e-6
        steps = Decimal(repr(p.volume)) / Decimal(repr(sp.volume_step))
        assert steps == steps.to_integral_value() and p.volume >= sp.min_volume
        # one more step would exceed the budget (floor, never round up)
        assert (p.volume + sp.volume_step) * p.risk_per_volume > b.effective_risk_amount - 0.02
    else:
        assert a.status is RiskStatus.LOCKED


@pytest.mark.parametrize("seed", SEEDS)
def test_losses_never_increase_risk(seed):
    rng = random.Random(1000 + seed)  # noqa: S311 - deterministic test data
    acct, st, sp, trade = random_case(rng)
    before = run(acct, st, sp, trade)
    loss = round(rng.uniform(1, acct.balance * 0.03), 2)
    after_account = acct.model_copy(update={"balance": round(acct.balance - loss, 2)})
    after_state = st.model_copy(
        update={
            "realized_pnl_today": round(st.realized_pnl_today - loss, 2),
            "realized_pnl_week": round(st.realized_pnl_week - loss, 2),
            "trades_today": st.trades_today + 1,
            "consecutive_losses": st.consecutive_losses + 1,
        }
    )
    after = run(after_account, after_state, sp, trade)
    assert after.budget.effective_risk_amount <= before.budget.effective_risk_amount + 1e-9
    assert (after.position.volume or 0.0) <= (before.position.volume or 0.0)
    # account-level locks never clear because of a further loss
    account_locks = set(RiskLock) - TRADE_LOCKS
    kinds = lambda a: {x.lock for x in a.locks} & account_locks  # noqa: E731
    assert kinds(before) <= kinds(after)
