"""Risk locks, budget and position sizing (pure).

Budget (account currency), for balance B and the resolved limits:
  risk per trade      B x riskPerTradePct
  daily remaining     (B - realizedToday) x dailyPct - lossToday - openRisk
  weekly remaining    (B - realizedWeek) x weeklyPct - lossWeek - openRisk
  open-risk remaining B x maxOpenRiskPct - openRisk
  prop remaining      min(dayStart x propDailyPct - lossToday, B - start x (1 - propTotalPct)) - openRisk
  effective           max(0, min of the above)
Profits never raise a loss limit (loss = max(0, -pnl)) and every cap shrinks after a loss, so risk can only go
down after losing (no martingale, no revenge sizing). A cap at or below zero is a lock.

Sizing (never guessed): volume = floor_step(effective / riskPerVolume), with
riskPerVolume = (distance + spread) / tickSize x tickValue x conversion. A missing spec, an internally
inconsistent spec (tickValue != contractSize x tickSize in the quote currency) or a missing/stale conversion
rate leaves the size POSITION_SIZE_UNVERIFIED.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from statistics import median

from app.contracts import strategy_version
from app.domain.candle import Candle
from app.domain.enums import (
    Blocker,
    Direction,
    PositionSizeStatus,
    RiskLock,
    RiskProfileName,
    RiskStatus,
    RiskWarning,
)
from app.domain.instrument import Instrument, InstrumentSpec, get_instrument
from app.services.risk.models import (
    AccountProfile,
    AccountState,
    ConversionRate,
    PositionSize,
    RiskAssessment,
    RiskBudget,
    RiskConfig,
    RiskLimits,
    RiskLockItem,
    TradeInput,
    VolatilityState,
)
from app.services.structure.swings import atr_at, true_ranges

EPS = 1e-9
NOT_AUTHORIZED = "NOT_AUTHORIZED"
NEWS_NOT_EVALUATED = "NOT_EVALUATED"


def _money(x: float) -> float:
    return round(x, 2)


def resolve_limits(account: AccountProfile, cfg: RiskConfig) -> RiskLimits:
    if account.profile is RiskProfileName.CUSTOM:
        return RiskLimits(
            risk_per_trade_pct=account.risk_per_trade_pct,
            daily_risk_limit_pct=account.daily_risk_limit_pct,
            weekly_risk_limit_pct=account.weekly_risk_limit_pct,
            max_open_risk_pct=account.max_open_risk_pct,
            max_trades_per_day=account.max_trades_per_day,
            max_positions=account.max_positions,
            max_consecutive_losses=account.max_consecutive_losses,
        )
    return cfg.profiles[account.profile]


def budget_and_locks(
    account: AccountProfile,
    state: AccountState | None,
    limits: RiskLimits,
    trading_day: object,
) -> tuple[RiskBudget, list[RiskLockItem], list[RiskWarning]]:
    b = account.balance
    locks: list[RiskLockItem] = []
    warnings: list[RiskWarning] = []

    def lock(kind: RiskLock, detail: str) -> None:
        locks.append(RiskLockItem(lock=kind, detail=detail))

    pnl_today = state.realized_pnl_today if state else 0.0
    pnl_week = state.realized_pnl_week if state else 0.0
    positions = state.open_positions if state else []
    open_risk = sum(p.risk_amount for p in positions)
    loss_today, loss_week = max(0.0, -pnl_today), max(0.0, -pnl_week)
    day_start, week_start = max(0.0, b - pnl_today), max(0.0, b - pnl_week)

    per_trade = b * limits.risk_per_trade_pct / 100
    daily_limit = day_start * limits.daily_risk_limit_pct / 100
    weekly_limit = week_start * limits.weekly_risk_limit_pct / 100
    daily_remaining = daily_limit - loss_today - open_risk
    weekly_remaining = weekly_limit - loss_week - open_risk
    open_remaining = b * limits.max_open_risk_pct / 100 - open_risk
    caps = [per_trade, daily_remaining, weekly_remaining, open_remaining]

    prop_remaining: float | None = None
    prop = account.prop_rules
    if prop is not None:
        prop_daily = day_start * prop.max_daily_drawdown_pct / 100 - loss_today - open_risk
        floor = prop.starting_balance * (1 - prop.max_total_drawdown_pct / 100)
        prop_total = b - floor - open_risk
        prop_remaining = min(prop_daily, prop_total)
        caps.append(prop_remaining)
        if prop_daily <= EPS:
            lock(
                RiskLock.PROP_DAILY_DRAWDOWN,
                f"prop daily drawdown budget used (loss today {loss_today:.2f})",
            )
        if prop_total <= EPS:
            lock(RiskLock.PROP_TOTAL_DRAWDOWN, f"balance is at the prop drawdown floor {floor:.2f}")

    if state is None:
        warnings.append(RiskWarning.ACCOUNT_STATE_NOT_PROVIDED)
    else:
        if state.trading_day != trading_day:
            lock(
                RiskLock.ACCOUNT_STATE_STALE,
                f"account state is for trading day {state.trading_day}, not {trading_day}: update it",
            )
        if len(positions) >= limits.max_positions:
            lock(RiskLock.MAX_POSITIONS, f"{len(positions)} open position(s), max {limits.max_positions}")
        if state.trades_today >= limits.max_trades_per_day:
            lock(
                RiskLock.MAX_TRADES_PER_DAY,
                f"{state.trades_today} trade(s) today, max {limits.max_trades_per_day}",
            )
        if state.consecutive_losses >= limits.max_consecutive_losses:
            lock(
                RiskLock.CONSECUTIVE_LOSSES,
                f"{state.consecutive_losses} consecutive losses, max {limits.max_consecutive_losses}",
            )
    if daily_remaining <= EPS:
        lock(
            RiskLock.DAILY_LOSS_LIMIT,
            f"loss today {loss_today:.2f} + open risk {open_risk:.2f} reach the daily limit "
            f"{daily_limit:.2f}",
        )
    if weekly_remaining <= EPS:
        lock(
            RiskLock.WEEKLY_LOSS_LIMIT,
            f"loss this week {loss_week:.2f} + open risk {open_risk:.2f} reach the weekly limit "
            f"{weekly_limit:.2f}",
        )
    if open_remaining <= EPS:
        lock(RiskLock.MAX_OPEN_RISK, f"open risk {open_risk:.2f} is at the maximum")

    effective = max(0.0, min(caps))
    if EPS < effective < per_trade - 0.005:
        warnings.append(RiskWarning.RISK_REDUCED_BY_LIMITS)
    budget = RiskBudget(
        risk_per_trade_amount=_money(per_trade),
        daily_remaining=_money(daily_remaining),
        weekly_remaining=_money(weekly_remaining),
        open_risk=_money(open_risk),
        open_risk_remaining=_money(open_remaining),
        prop_remaining=None if prop_remaining is None else _money(prop_remaining),
        effective_risk_amount=_money(effective) if effective > EPS else 0.0,
    )
    return budget, locks, warnings


def volatility_state(candles: Sequence[Candle], cfg: RiskConfig) -> VolatilityState | None:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < cfg.atr_period + cfg.baseline_bars:
        return None
    trs = true_ranges(closed)
    last = len(closed) - 1
    atr = atr_at(trs, last, cfg.atr_period)
    baseline = median(atr_at(trs, i, cfg.atr_period) for i in range(last - cfg.baseline_bars, last))
    if baseline <= 0:
        return None
    return VolatilityState(
        timeframe=cfg.volatility_timeframe,
        atr=round(atr, 6),
        baseline_atr=round(baseline, 6),
        ratio=round(atr / baseline, 3),
        lock_ratio=cfg.volatility_lock_ratio,
    )


def conversion_factor(
    quote: str, account_currency: str, rates: Sequence[ConversionRate], now: datetime, max_age_hours: float
) -> float | None:
    """Account-currency value of 1 unit of `quote`, from a fresh user rate. Never inferred or chained."""
    if quote == account_currency:
        return 1.0
    for r in rates:
        if not (timedelta(0) <= now - r.as_of <= timedelta(hours=max_age_hours)):
            continue
        if r.base == quote and r.quote == account_currency:
            return r.rate
        if r.base == account_currency and r.quote == quote:
            return 1.0 / r.rate
    return None


def spec_consistent(spec: InstrumentSpec, tolerance_pct: float) -> bool:
    expected = spec.contract_size * spec.tick_size
    return abs(spec.tick_value - expected) <= expected * tolerance_pct / 100


def floor_to_step(value: float, step: float) -> float:
    steps = (Decimal(repr(value)) / Decimal(repr(step))).to_integral_value(rounding=ROUND_FLOOR)
    return float(steps * Decimal(repr(step)))


def _exposure(symbol: str, direction: Direction) -> dict[str, int]:
    inst = get_instrument(symbol)
    if inst is None:
        return {}
    sign = 1 if direction is Direction.BULLISH else -1
    return {inst.base: sign, inst.quote: -sign}


def _position(
    trade: TradeInput,
    instrument: Instrument,
    spec: InstrumentSpec | None,
    spec_verified: bool,
    conversion: float | None,
    account: AccountProfile,
    effective: float,
    cfg: RiskConfig,
) -> tuple[PositionSize, PositionSizeStatus | None, list[RiskLockItem], list[RiskWarning]]:
    locks: list[RiskLockItem] = []
    warnings: list[RiskWarning] = []
    precision = instrument.price_precision
    distance = abs(trade.entry - trade.stop)
    valid_side = (
        trade.stop < trade.entry if trade.direction is Direction.BULLISH else trade.stop > trade.entry
    )
    spread = spec.typical_spread if spec else None
    base = {
        "direction": trade.direction,
        "entry": trade.entry,
        "stop": trade.stop,
        "price_distance": round(distance, precision + 2),
        "points": round(distance / spec.tick_size, 1) if spec else None,
        "pips": round(distance / spec.platform_pip_size, 1) if spec and spec.platform_pip_size else None,
        "spread": spread,
        "sizing_distance": round(distance + (spread or 0.0), precision + 2),
        "min_volume": spec.min_volume if spec else None,
        "volume_step": spec.volume_step if spec else None,
    }
    empty = {
        "risk_per_volume": None,
        "volume": None,
        "risk_amount": None,
        "risk_amount_without_spread": None,
        "risk_pct": None,
        "margin_required": None,
    }
    if distance <= EPS or not valid_side:
        locks.append(
            RiskLockItem(
                lock=RiskLock.INVALID_STOP, detail="the stop must be on the losing side of the entry"
            )
        )
        return PositionSize(**base, **empty, detail="invalid stop"), None, locks, warnings

    if spec is None:
        detail = "no instrument contract spec: size not computed"
        return (
            PositionSize(**base, **empty, detail=detail),
            PositionSizeStatus.POSITION_SIZE_UNVERIFIED,
            locks,
            warnings,
        )
    if spread is None:
        warnings.append(RiskWarning.SPREAD_UNKNOWN)
    elif spread / distance * 100 > cfg.max_spread_to_stop_pct:
        locks.append(
            RiskLockItem(
                lock=RiskLock.UNSAFE_SPREAD,
                detail=f"typical spread {spread} is over {cfg.max_spread_to_stop_pct}% of the stop distance",
            )
        )
    problems: list[str] = []
    if not spec_verified:
        warnings.append(RiskWarning.USER_SUPPLIED_SPEC)
    if not spec_consistent(spec, cfg.spec_tolerance_pct):
        warnings.append(RiskWarning.SPEC_INCONSISTENT)
        problems.append("tick value is not contract size x tick size in the quote currency")
    if conversion is None:
        warnings.append(RiskWarning.CONVERSION_UNAVAILABLE)
        problems.append(f"no fresh {spec.quote_currency}->{account.currency} conversion rate")
    if problems:
        detail = "size not computed: " + "; ".join(problems)
        return (
            PositionSize(**base, **empty, detail=detail),
            PositionSizeStatus.POSITION_SIZE_UNVERIFIED,
            locks,
            warnings,
        )

    assert conversion is not None
    per_tick = spec.tick_value * conversion
    risk_per_volume = (distance + (spread or 0.0)) / spec.tick_size * per_tick
    status = PositionSizeStatus.VERIFIED if spec_verified else PositionSizeStatus.SIZED_FROM_USER_SPEC
    if effective <= EPS:
        detail = "no risk budget left"
        values = {**empty, "risk_per_volume": _money(risk_per_volume)}
        return PositionSize(**base, **values, detail=detail), status, locks, warnings
    volume = floor_to_step(effective / risk_per_volume, spec.volume_step)
    if volume + EPS < spec.min_volume:
        min_risk = spec.min_volume * risk_per_volume
        locks.append(
            RiskLockItem(
                lock=RiskLock.BELOW_MIN_VOLUME,
                detail=(
                    f"minimum volume {spec.min_volume} would risk {min_risk:.2f} (budget {effective:.2f})"
                ),
            )
        )
        values = {**empty, "risk_per_volume": _money(risk_per_volume)}
        return (
            PositionSize(**base, **values, detail="the stop is too wide for the budget"),
            status,
            locks,
            warnings,
        )

    risk_amount = volume * risk_per_volume
    margin: float | None = None
    if account.leverage is None:
        warnings.append(RiskWarning.LEVERAGE_NOT_SET)
    else:
        margin = volume * spec.contract_size * trade.entry * conversion / account.leverage
        if margin > account.balance:
            locks.append(
                RiskLockItem(
                    lock=RiskLock.INSUFFICIENT_MARGIN,
                    detail=f"margin {margin:.2f} exceeds the balance {account.balance:.2f}",
                )
            )
    step_places = max(0, -Decimal(repr(spec.volume_step)).normalize().as_tuple().exponent)  # type: ignore[operator]
    position = PositionSize(
        **base,
        risk_per_volume=_money(risk_per_volume),
        volume=round(volume, step_places),
        risk_amount=_money(risk_amount),
        risk_amount_without_spread=_money(volume * distance / spec.tick_size * per_tick),
        risk_pct=round(risk_amount / account.balance * 100, 3),
        margin_required=None if margin is None else _money(margin),
        detail=f"{round(volume, step_places)} volume risks {risk_amount:.2f} {account.currency} at stop",
    )
    return position, status, locks, warnings


def assess(
    symbol: str,
    now: datetime,
    *,
    account: AccountProfile,
    state: AccountState | None,
    spec: InstrumentSpec | None,
    spec_verified: bool,
    rates: Sequence[ConversionRate],
    trade: TradeInput | None,
    candles: Sequence[Candle] | None,
    trading_day: object,
    cfg: RiskConfig,
) -> RiskAssessment:
    """`candles` None = volatility not checked (what-if); a too-short series = volatility unavailable."""
    instrument = get_instrument(symbol)
    if instrument is None:
        raise ValueError(f"unknown symbol {symbol}")
    limits = resolve_limits(account, cfg)
    over = limits.exceeds(cfg.hard_limits)
    if over:
        return not_assessed(
            symbol, now, RiskStatus.INVALID_PROFILE, f"limits above the hard caps: {', '.join(over)}", account
        )

    budget, locks, warnings = budget_and_locks(account, state, limits, trading_day)
    volatility: VolatilityState | None = None
    if candles is None:
        warnings.append(RiskWarning.VOLATILITY_NOT_CHECKED)
    else:
        volatility = volatility_state(candles, cfg)
        if volatility is None:
            warnings.append(RiskWarning.VOLATILITY_UNAVAILABLE)
        elif volatility.ratio >= volatility.lock_ratio:
            locks.append(
                RiskLockItem(
                    lock=RiskLock.VOLATILITY,
                    detail=f"{volatility.timeframe.value} ATR is {volatility.ratio}x its baseline",
                )
            )

    position: PositionSize | None = None
    size_status: PositionSizeStatus | None = None
    if trade is not None:
        positions = state.open_positions if state else []
        mine = _exposure(symbol, trade.direction)
        if any(p.symbol == symbol and p.direction is trade.direction and p.in_loss for p in positions):
            locks.append(
                RiskLockItem(
                    lock=RiskLock.ADDING_TO_LOSER, detail="an open same-direction position is in loss"
                )
            )
        if any(
            mine.get(ccy) == sign for p in positions for ccy, sign in _exposure(p.symbol, p.direction).items()
        ):
            warnings.append(RiskWarning.CORRELATED_EXPOSURE)
        quote = spec.quote_currency if spec else instrument.quote
        conversion = conversion_factor(quote, account.currency, rates, now, cfg.conversion_max_age_hours)
        position, size_status, trade_locks, trade_warnings = _position(
            trade, instrument, spec, spec_verified, conversion, account, budget.effective_risk_amount, cfg
        )
        locks += trade_locks
        warnings += trade_warnings
    warnings.append(RiskWarning.NEWS_NOT_EVALUATED)

    if locks:
        status = RiskStatus.LOCKED
    elif trade is not None and size_status is PositionSizeStatus.POSITION_SIZE_UNVERIFIED:
        status = RiskStatus.SIZE_UNVERIFIED
    elif trade is not None:
        status = RiskStatus.WITHIN_LIMITS
    else:
        status = RiskStatus.CLEAR
    blockers: list[Blocker] = []
    if locks:
        blockers.append(Blocker.RISK_LOCKED)
    if any(x.lock is RiskLock.UNSAFE_SPREAD for x in locks):
        blockers.append(Blocker.UNSAFE_SPREAD)
    if size_status is PositionSizeStatus.POSITION_SIZE_UNVERIFIED:
        blockers.append(Blocker.POSITION_SIZE_UNVERIFIED)
    return RiskAssessment(
        symbol=symbol.upper(),
        status=status,
        profile=account.profile,
        currency=account.currency,
        profile_error=None,
        limits=limits,
        budget=budget,
        locks=locks,
        warnings=list(dict.fromkeys(warnings)),
        volatility=volatility,
        position=position,
        size_status=size_status,
        blockers=blockers,
        news=NEWS_NOT_EVALUATED,
        authority=NOT_AUTHORIZED,
        strategy_version=strategy_version(),
        generated_at=now,
    )


_STATUS_BLOCKER = {
    RiskStatus.NOT_CONFIGURED: Blocker.RISK_PROFILE_MISSING,
    RiskStatus.INVALID_PROFILE: Blocker.RISK_PROFILE_INVALID,
    RiskStatus.UNAVAILABLE: Blocker.RISK_GATE_MISSING,
}


def not_assessed(
    symbol: str,
    now: datetime,
    status: RiskStatus,
    error: str | None,
    account: AccountProfile | None = None,
) -> RiskAssessment:
    """NOT_CONFIGURED / INVALID_PROFILE / UNAVAILABLE: nothing is sized and the decision is blocked."""
    return RiskAssessment(
        symbol=symbol.upper(),
        status=status,
        profile=account.profile if account else None,
        currency=account.currency if account else None,
        profile_error=error,
        limits=None,
        budget=None,
        locks=[],
        warnings=[RiskWarning.NEWS_NOT_EVALUATED],
        volatility=None,
        position=None,
        size_status=None,
        blockers=[_STATUS_BLOCKER[status]],
        news=NEWS_NOT_EVALUATED,
        authority=NOT_AUTHORIZED,
        strategy_version=strategy_version(),
        generated_at=now,
    )
