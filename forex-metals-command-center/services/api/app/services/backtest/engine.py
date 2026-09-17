"""Backtest runner (sequential, closed bars only).

For every closed setup-timeframe candle close t in the range, the live setup engine is asked for its analysis
as of t (`analyze(t)`, the same code path the live decision uses). A plan counts only on the step where it was
confirmed (confirmedAt + setup timeframe == t) and only on data eligible for a decision. Fills and exits come
from the paper engine (limit at the plan entry created at t, plan stop, TP1, assumed costs, pending expiry)
over closed execution bars; one position at a time (later plans are SKIPPED_OVERLAP); a position still
pending/open at the end is OPEN_AT_END and excluded from statistics. Risk, news and verdict-authority gates
are not applied (research).
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.candle import Candle
from app.domain.enums import (
    AnalyticsSource,
    BacktestTradeStatus,
    PaperEntryType,
    PaperStatus,
    ProcessClassification,
    Timeframe,
    TradeResult,
)
from app.services.analytics.engine import breakdown, drawdown, group_stats, label_for
from app.services.analytics.models import AnalyticsConfig, Sample
from app.services.backtest.models import (
    BacktestConfig,
    BacktestTrade,
    BacktestVariant,
    Funnel,
    MonteCarlo,
    SegmentStats,
    VariantResult,
)
from app.services.journal.engine import candle_extremes, compute_outcome
from app.services.journal.models import JournalConfig, JournalTradeFill, RecordOutcomeRequest
from app.services.paper.engine import SimParams, SimState, simulate
from app.services.paper.models import AssumedCosts
from app.services.setup_state.models import SetupAnalysis
from app.services.timeframes.core import NEW_YORK

Analyze = Callable[[datetime], Awaitable[SetupAnalysis]]
Progress = Callable[[int, int], Awaitable[None]]
ACTIVE = (PaperStatus.PENDING, PaperStatus.OPEN)


@dataclass
class _Position:
    trade: dict[str, object]
    params: SimParams
    state: SimState = field(default_factory=SimState)


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, math.ceil(p / 100 * len(sorted_values)) - 1))
    return round(sorted_values[k], 4)


def monte_carlo(rs: Sequence[float], resamples: int, seed: int, cfg: AnalyticsConfig) -> MonteCarlo | None:
    """Bootstrap (with replacement) of the per-trade R sequence: spread of total R and max drawdown."""
    if len(rs) < 2 or resamples < 1:
        return None
    rng = random.Random(seed)  # noqa: S311 - seeded research resampling, not security
    totals: list[float] = []
    drawdowns: list[float] = []
    for _ in range(resamples):
        equity = peak = worst = 0.0
        for _i in range(len(rs)):
            equity += rs[rng.randrange(len(rs))]
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        totals.append(equity)
        drawdowns.append(worst)
    totals.sort()
    drawdowns.sort()
    return MonteCarlo(
        resamples=resamples,
        seed=seed,
        trades=len(rs),
        label=label_for(len(rs), cfg),
        total_r_p05=_percentile(totals, 5),
        total_r_p50=_percentile(totals, 50),
        total_r_p95=_percentile(totals, 95),
        max_drawdown_r_p50=_percentile(drawdowns, 50),
        max_drawdown_r_p95=_percentile(drawdowns, 95),
        detail="bootstrap of recorded trade R (seeded); describes sequence sensitivity, not future results",
    )


def _finish(pos: _Position, jcfg: JournalConfig, status: BacktestTradeStatus | None = None) -> BacktestTrade:
    st, p = pos.state, pos.params
    t = dict(pos.trade)
    ambiguous = bool(t.pop("ambiguous", False))
    if status is None:
        status = (
            BacktestTradeStatus.CLOSED if st.status is PaperStatus.CLOSED else BacktestTradeStatus.EXPIRED
        )
    result = r = net = mfe_r = mae_r = None
    if status is BacktestTradeStatus.CLOSED and st.fill_price is not None and st.exit_price is not None:
        assert st.filled_at is not None and st.exited_at is not None and st.exit_reason is not None
        entry = st.fill_price if p.s * (st.fill_price - p.stop) > 0 else float(p.limit_price or st.fill_price)
        fill = JournalTradeFill(
            direction=p.direction, entry=entry, stop=p.stop, targets=[p.target], opened_at=st.filled_at
        )
        exited = st.exited_at if st.exited_at > st.filled_at else st.filled_at + timedelta(seconds=1)
        outcome = RecordOutcomeRequest(exit_price=st.exit_price, exited_at=exited, exit_reason=st.exit_reason)
        bars = (
            [(max(st.best, st.worst), min(st.best, st.worst))]
            if st.best is not None and st.worst is not None
            else []
        )
        m = compute_outcome(
            fill, outcome, candle_extremes(fill, st.exit_price, bars, False, "bars"), [], jcfg
        )
        risk = abs(entry - p.stop)
        result, r, mfe_r, mae_r = m.result, m.r_multiple, m.mfe_r, m.mae_r
        net = (
            round(m.r_multiple - 2 * p.costs.commission / risk, 3)
            if m.r_multiple is not None and risk
            else None
        )
    return BacktestTrade(
        **t,
        status=status,
        fill_price=st.fill_price,
        filled_at=st.filled_at,
        exit_price=st.exit_price if status is BacktestTradeStatus.CLOSED else None,
        exited_at=st.exited_at if status is BacktestTradeStatus.CLOSED else None,
        exit_reason=st.exit_reason if status is BacktestTradeStatus.CLOSED else None,
        result=result,
        r_multiple=r,
        net_r_multiple=net,
        mfe_r=mfe_r,
        mae_r=mae_r,
        ambiguous=ambiguous,
    )


async def run_variant(
    variant: BacktestVariant,
    analyze: Analyze,
    step_bars: Sequence[Candle],
    execution_bars: Sequence[Candle],
    setup_timeframe: Timeframe,
    base_costs: AssumedCosts,
    pending_expiry_bars: int,
    end: datetime,
    progress: Progress | None = None,
    progress_every: int = 10,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[Funnel, list[BacktestTrade]]:
    costs = AssumedCosts(
        spread=base_costs.spread * variant.cost_multiplier,
        slippage=base_costs.slippage * variant.cost_multiplier,
        commission=base_costs.commission * variant.cost_multiplier,
    )
    jcfg = JournalConfig.from_spec()
    exec_bars = sorted((b for b in execution_bars if b.is_closed), key=lambda b: b.open_time)
    steps = sorted(b.close_time for b in step_bars if b.is_closed and b.close_time <= end)
    cursor = 0
    position: _Position | None = None
    trades: list[BacktestTrade] = []
    seen_setups: set[str] = set()
    reached: set[tuple[str, str]] = set()
    seen_plans: set[tuple[str, datetime]] = set()
    ineligible = confirmed = skipped = fills = 0
    reasons: Counter[str] = Counter()

    def advance(until: datetime) -> None:
        nonlocal cursor, position, fills
        batch: list[Candle] = []
        while cursor < len(exec_bars) and exec_bars[cursor].close_time <= until:
            batch.append(exec_bars[cursor])
            cursor += 1
        if position is None or not batch:
            return
        was_filled = position.state.fill_price is not None
        position.state, events = simulate(position.params, position.state, batch)
        if any(e.ambiguous for e in events):
            position.trade["ambiguous"] = True
        if not was_filled and position.state.fill_price is not None:
            fills += 1
        if position.state.status not in ACTIVE:
            trades.append(_finish(position, jcfg))
            position = None

    for i, t in enumerate(steps, start=1):
        if cancelled():
            raise RuntimeError("cancelled")
        advance(t)
        analysis = await analyze(t)
        if not analysis.eligible_for_decision:
            ineligible += 1
            reasons.update(str(getattr(r, "value", r)) for r in getattr(analysis, "ineligibility", []))
        else:
            for s in analysis.setups:
                seen_setups.add(s.id)
                reached.add((s.id, s.state.value))
                plan = s.entry_plan
                if plan is None or plan.confirmed_at + setup_timeframe.duration != t:
                    continue
                key = (s.id, plan.confirmed_at)
                if key in seen_plans:
                    continue
                seen_plans.add(key)
                confirmed += 1
                trade: dict[str, object] = {
                    "variant": variant.name,
                    "setup_id": s.id,
                    "setup_type": s.setup_type.value,
                    "model": plan.model.value,
                    "direction": plan.direction,
                    "confirmed_at": t,
                    "limit_price": plan.entry,
                    "stop": plan.stop,
                    "target": plan.tp1,
                    "ambiguous": False,
                    "detail": f"{plan.model.value} plan confirmed; limit at the plan entry",
                }
                if position is not None:
                    skipped += 1
                    trades.append(
                        BacktestTrade(
                            **{**trade, "detail": "skipped: a position was already pending or open"},
                            status=BacktestTradeStatus.SKIPPED_OVERLAP,
                            fill_price=None,
                            filled_at=None,
                            exit_price=None,
                            exited_at=None,
                            exit_reason=None,
                            result=None,
                            r_multiple=None,
                            net_r_multiple=None,
                            mfe_r=None,
                            mae_r=None,
                        )
                    )
                    continue
                params = SimParams(
                    direction=plan.direction,
                    entry_type=PaperEntryType.LIMIT,
                    limit_price=plan.entry,
                    stop=plan.stop,
                    target=plan.tp1,
                    created_at=t,
                    costs=costs,
                    pending_expiry_bars=pending_expiry_bars,
                )
                position = _Position(trade=trade, params=params)
        if progress is not None and (i % progress_every == 0 or i == len(steps)):
            await progress(i, len(steps))
    advance(end)
    open_at_end = 0
    if position is not None:
        open_at_end = 1
        trades.append(_finish(position, jcfg, BacktestTradeStatus.OPEN_AT_END))
    counts = Counter(state for _, state in reached)
    funnel = Funnel(
        steps=len(steps),
        ineligible_steps=ineligible,
        ineligible_reasons=dict(sorted(reasons.items())),
        setups_discovered=len(seen_setups),
        states_reached=dict(sorted(counts.items())),
        plans_confirmed=confirmed,
        plans_skipped_overlap=skipped,
        fills=fills,
        expired=sum(1 for x in trades if x.status is BacktestTradeStatus.EXPIRED),
        closed=sum(1 for x in trades if x.status is BacktestTradeStatus.CLOSED),
        open_at_end=open_at_end,
    )
    return funnel, trades


def trade_sample(t: BacktestTrade, symbol: str, synthetic: bool, version: str, tol: float) -> Sample:
    assert t.exited_at is not None and t.result is not None
    r = t.net_r_multiple
    win = r is not None and r > tol
    return Sample(
        id=f"{t.variant}:{t.setup_id}:{t.confirmed_at.isoformat()}",
        source=AnalyticsSource.PAPER,
        symbol=symbol,
        direction=t.direction,
        closed_at=t.exited_at,
        r=r,
        win=win,
        breakeven=t.result is TradeResult.BREAK_EVEN or (r is not None and abs(r) <= tol),
        result=t.result.value,
        classification=ProcessClassification.VALID_WIN if win else ProcessClassification.VALID_LOSS,
        violations=(),
        duration_minutes=round(((t.exited_at - (t.filled_at or t.confirmed_at)).total_seconds()) / 60, 2),
        mfe_r=t.mfe_r,
        mae_r=t.mae_r,
        entry_efficiency=None,
        exit_efficiency=None,
        session="N/A",
        setup_type=t.setup_type,
        timeframe=t.model,
        day_of_week=t.exited_at.astimezone(NEW_YORK).strftime("%A"),
        no_wick=None,
        liquidity_event=None,
        dol=None,
        entry_price=t.fill_price or t.limit_price,
        mfe_price=None,
        synthetic=synthetic,
        strategy_version=version,
    )


def summarize_variant(
    variant: BacktestVariant,
    funnel: Funnel,
    trades: list[BacktestTrade],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    segments: int,
    out_of_sample_from: datetime | None,
    synthetic: bool,
    version: str,
    cfg: BacktestConfig,
    acfg: AnalyticsConfig,
    tol: float,
) -> VariantResult:
    closed = [t for t in trades if t.status is BacktestTradeStatus.CLOSED]
    samples = [trade_sample(t, symbol, synthetic, version, tol) for t in closed]
    dd, points = drawdown(samples)
    span = (end - start) / segments
    seg_stats: list[SegmentStats] = []
    for k in range(segments):
        s0, s1 = start + span * k, start + span * (k + 1)
        members = [s for s in samples if s0 <= s.closed_at < s1 or (k == segments - 1 and s.closed_at == end)]
        seg_stats.append(
            SegmentStats(name=f"S{k + 1}", start=s0, end=s1, stats=group_stats(f"S{k + 1}", members, acfg))
        )
    in_sample = out_sample = None
    if out_of_sample_from is not None:
        in_sample = group_stats("IN_SAMPLE", [s for s in samples if s.closed_at < out_of_sample_from], acfg)
        out_sample = group_stats(
            "OUT_OF_SAMPLE", [s for s in samples if s.closed_at >= out_of_sample_from], acfg
        )
    rs = [s.r for s in samples if s.r is not None]
    dims = [
        ("DIRECTION", lambda s: s.direction.value),
        ("ENTRY_MODEL", lambda s: s.timeframe),
        ("SETUP_TYPE", lambda s: s.setup_type),
        ("RESULT", lambda s: s.result),
        ("DAY_OF_WEEK", lambda s: s.day_of_week),
    ]
    return VariantResult(
        variant=variant,
        funnel=funnel,
        stats=group_stats("ALL", samples, acfg),
        drawdown=dd,
        equity_curve=points,
        breakdowns=[breakdown(name, samples, key, acfg) for name, key in dims],
        segments=seg_stats,
        in_sample=in_sample,
        out_of_sample=out_sample,
        monte_carlo=monte_carlo(rs, cfg.monte_carlo_resamples, cfg.monte_carlo_seed, acfg),
        ambiguous_trades=sum(1 for t in closed if t.ambiguous),
        trades=trades,
    )
