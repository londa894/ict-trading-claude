"""Builders for Phase 9 risk tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import Direction
from app.domain.instrument import InstrumentSpec
from app.services.risk.models import AccountProfile, AccountState, ConversionRate, RiskConfig, TradeInput
from app.services.sessions.clock import trading_day_of
from tests.session_helpers import candle, slots

CFG = RiskConfig.from_spec()
NOW = datetime(2024, 1, 9, 16, 0, tzinfo=UTC)
TODAY = trading_day_of(NOW)
LONG_TRADE = TradeInput(direction=Direction.BULLISH, entry=2032.0, stop=2028.8)


def spec(**over: Any) -> InstrumentSpec:
    base: dict[str, Any] = dict(
        symbol="XAUUSD",
        contract_size=100.0,
        tick_size=0.01,
        tick_value=1.0,
        min_volume=0.01,
        volume_step=0.01,
        quote_currency="USD",
        typical_spread=0.3,
        platform_pip_size=0.1,
    )
    base.update(over)
    return InstrumentSpec(**base)


def account(**over: Any) -> AccountProfile:
    base: dict[str, Any] = dict(balance=10_000.0, currency="USD", profile="STANDARD", leverage=100.0)
    base.update(over)
    return AccountProfile(**base)


def custom(**over: Any) -> AccountProfile:
    limits: dict[str, Any] = dict(
        risk_per_trade_pct=1.0,
        daily_risk_limit_pct=3.0,
        weekly_risk_limit_pct=6.0,
        max_open_risk_pct=2.0,
        max_trades_per_day=3,
        max_positions=2,
        max_consecutive_losses=3,
    )
    limits.update(over)
    return account(profile="CUSTOM", **limits)


def state(**over: Any) -> AccountState:
    base: dict[str, Any] = dict(
        trading_day=TODAY,
        realized_pnl_today=0.0,
        realized_pnl_week=0.0,
        trades_today=0,
        consecutive_losses=0,
        open_positions=[],
    )
    base.update(over)
    return AccountState(**base)


def rate(base: str, quote: str, value: float, age_hours: float = 1.0) -> ConversionRate:
    return ConversionRate(base=base, quote=quote, rate=value, as_of=NOW - timedelta(hours=age_hours))


def volatility_candles(spike_range: float | None = None, bars: int = 120, spike_bars: int = 14) -> list:
    """Flat M15 candles with a 1.0 true range; the last `spike_bars` have `spike_range` when given."""
    times = slots(NOW - timedelta(days=4), NOW)[-bars:]
    out = []
    for i, t in enumerate(times):
        r = spike_range if spike_range is not None and i >= len(times) - spike_bars else 1.0
        out.append(candle(t, (2000.0, 2000.0 + r / 2, 2000.0 - r / 2, 2000.0)))
    return out
