"""Analytics V1 computations (pure).

Win = process class VALID_WIN / BAD_PROCESS_WIN (R above the break-even tolerance). Break-even = a non-win
whose R is within the break-even tolerance (or result BREAK_EVEN). Losses = the rest. Win rate = wins / count.
avg/total R over samples with a defined R. Expectancy (R) = p x avgWinR + (1 - p) x avgNonWinR with p the win
share of the samples with R (equals the average R). Profit factor = sum(R > 0) / |sum(R < 0)|, null when there
is no negative R. Drawdown: cumulative R (from 0) in close order; the largest peak-to-trough fall and whether
and when that peak was regained.
Sample-size labels (spec): < 30 INSUFFICIENT, 30-99 LIMITED, 100-299 MODERATE, 300+ STRONGER_EVIDENCE.
A breakdown names a best group (highest expectancy) only among groups at or above bestGroupMinLabel.
DOL accuracy: the snapshot DOL "... BSL|SSL ... @ price"; aligned = BSL for longs / SSL for shorts. For
aligned records, reached when the most favourable price got to the DOL price (BSL: >=, SSL: <=).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from statistics import median

from app.domain.enums import Direction, SampleSizeLabel
from app.services.analytics.models import (
    AnalyticsConfig,
    Breakdown,
    DolAccuracy,
    Drawdown,
    EquityPoint,
    GroupStats,
    ProcessStats,
    Sample,
)

LABEL_ORDER = list(SampleSizeLabel)
DOL_RE = re.compile(r"\b(BSL|SSL)\b.*@\s*(-?\d+(?:\.\d+)?)")


def label_for(count: int, cfg: AnalyticsConfig) -> SampleSizeLabel:
    if count >= cfg.stronger_from:
        return SampleSizeLabel.STRONGER_EVIDENCE
    if count >= cfg.moderate_from:
        return SampleSizeLabel.MODERATE
    if count >= cfg.limited_from:
        return SampleSizeLabel.LIMITED
    return SampleSizeLabel.INSUFFICIENT


def _avg(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def group_stats(key: str, samples: Sequence[Sample], cfg: AnalyticsConfig) -> GroupStats:
    n = len(samples)
    wins = sum(1 for s in samples if s.win)
    breakeven = sum(1 for s in samples if not s.win and s.breakeven)
    with_r = [s for s in samples if s.r is not None]
    rs = [s.r for s in with_r if s.r is not None]
    win_rs = [s.r for s in with_r if s.win and s.r is not None]
    other_rs = [s.r for s in with_r if not s.win and s.r is not None]
    expectancy = None
    if rs:
        p = len(win_rs) / len(rs)
        expectancy = round(p * (_avg(win_rs) or 0.0) + (1 - p) * (_avg(other_rs) or 0.0), 4)
    negative = -sum(r for r in rs if r < 0)
    return GroupStats(
        key=key,
        count=n,
        label=label_for(n, cfg),
        wins=wins,
        losses=n - wins - breakeven,
        breakeven=breakeven,
        win_rate=round(wins / n, 4) if n else None,
        r_count=len(rs),
        avg_r=_avg(rs),
        total_r=round(sum(rs), 4) if rs else None,
        avg_win_r=_avg(win_rs),
        avg_loss_r=_avg(other_rs),
        expectancy_r=expectancy,
        profit_factor=round(sum(r for r in rs if r > 0) / negative, 4) if negative > 0 else None,
    )


def breakdown(
    dimension: str, samples: Sequence[Sample], key: Callable[[Sample], str], cfg: AnalyticsConfig
) -> Breakdown:
    buckets: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        buckets[key(s)].append(s)
    groups = sorted((group_stats(k, v, cfg) for k, v in buckets.items()), key=lambda g: (-g.count, g.key))
    floor = LABEL_ORDER.index(cfg.best_group_min_label)
    eligible = [g for g in groups if LABEL_ORDER.index(g.label) >= floor and g.expectancy_r is not None]
    if len(groups) < 2:
        return Breakdown(
            dimension=dimension, groups=groups, best=None, best_reason="fewer than two groups to compare"
        )
    if not eligible:
        return Breakdown(
            dimension=dimension,
            groups=groups,
            best=None,
            best_reason=f"no group reaches {cfg.best_group_min_label.value} ({cfg.limited_from}+ records)",
        )
    top = max(eligible, key=lambda g: (g.expectancy_r or 0.0, g.count))
    return Breakdown(
        dimension=dimension,
        groups=groups,
        best=top.key,
        best_reason=f"highest expectancy among groups with {cfg.best_group_min_label.value}+ samples",
    )


def drawdown(samples: Sequence[Sample]) -> tuple[Drawdown, list[EquityPoint]]:
    """Largest peak-to-trough fall of cumulative R (starting at 0) and when that peak was regained."""
    ordered = sorted((s for s in samples if s.r is not None), key=lambda s: (s.closed_at, s.id))
    points: list[EquityPoint] = []
    equity = peak = worst = 0.0
    peak_at: datetime | None = None
    worst_peak = 0.0
    worst_peak_at: datetime | None = None
    worst_trough_at: datetime | None = None
    worst_index = -1
    for i, s in enumerate(ordered):
        equity += s.r or 0.0
        points.append(EquityPoint(at=s.closed_at, cumulative_r=round(equity, 4)))
        if equity > peak:
            peak, peak_at = equity, s.closed_at
        if peak - equity > worst:
            worst, worst_peak, worst_peak_at, worst_trough_at, worst_index = (
                peak - equity,
                peak,
                peak_at,
                s.closed_at,
                i,
            )
    recovered_at: datetime | None = None
    recovery_trades: int | None = None
    if worst_index >= 0:
        for j in range(worst_index + 1, len(points)):
            if points[j].cumulative_r >= round(worst_peak, 4):
                recovered_at, recovery_trades = points[j].at, j - worst_index
                break
    return (
        Drawdown(
            max_drawdown_r=round(worst, 4),
            peak_at=worst_peak_at,
            trough_at=worst_trough_at,
            recovered_at=recovered_at,
            recovery_trades=recovery_trades,
        ),
        points,
    )


def process_stats(samples: Sequence[Sample], cfg: AnalyticsConfig) -> ProcessStats:
    violations = Counter(v.value for s in samples for v in s.violations)
    return ProcessStats(
        classifications=dict(Counter(s.classification.value for s in samples)),
        violation_counts=dict(sorted(violations.items(), key=lambda kv: (-kv[1], kv[0]))),
        with_violations=group_stats("WITH_VIOLATIONS", [s for s in samples if s.violations], cfg),
        without_violations=group_stats("WITHOUT_VIOLATIONS", [s for s in samples if not s.violations], cfg),
    )


def parse_dol(dol: str | None) -> tuple[str, float] | None:
    if not dol:
        return None
    m = DOL_RE.search(dol)
    return (m.group(1), float(m.group(2))) if m else None


def dol_accuracy(samples: Sequence[Sample], cfg: AnalyticsConfig) -> DolAccuracy:
    evaluated = reached = unknown = 0
    aligned: list[Sample] = []
    opposed: list[Sample] = []
    for s in samples:
        parsed = parse_dol(s.dol)
        if parsed is None:
            unknown += 1
            continue
        side, price = parsed
        is_aligned = (side == "BSL") == (s.direction is Direction.BULLISH)
        (aligned if is_aligned else opposed).append(s)
        sign = 1 if s.direction is Direction.BULLISH else -1
        if not is_aligned or s.mfe_price is None or sign * (price - s.entry_price) <= 0:
            continue  # only a draw ahead of the entry, in the record's own direction, can be reached
        evaluated += 1
        if (s.mfe_price >= price) if side == "BSL" else (s.mfe_price <= price):
            reached += 1
    return DolAccuracy(
        evaluated=evaluated,
        reached=reached,
        rate=round(reached / evaluated, 4) if evaluated else None,
        label=label_for(evaluated, cfg),
        aligned=group_stats("ALIGNED_WITH_DOL", aligned, cfg),
        opposed=group_stats("AGAINST_DOL", opposed, cfg),
        unknown=unknown,
        detail="aligned records with the DOL ahead of the entry: reached = best price got to the DOL",
    )


def downsample(points: list[EquityPoint], limit: int) -> list[EquityPoint]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(i * step)] for i in range(limit - 1)]
    return [*picked, points[-1]]


DIMENSIONS: list[tuple[str, Callable[[Sample], str]]] = [
    ("ASSET", lambda s: s.symbol),
    ("SESSION", lambda s: s.session),
    ("SETUP_TYPE", lambda s: s.setup_type),
    ("TIMEFRAME", lambda s: s.timeframe),
    ("DAY_OF_WEEK", lambda s: s.day_of_week),
    ("NO_WICK", lambda s: s.no_wick or "NONE"),
    ("LIQUIDITY_EVENT", lambda s: s.liquidity_event or "NONE"),
    ("DIRECTION", lambda s: s.direction.value),
    ("RESULT", lambda s: s.result),
]


def durations(samples: Sequence[Sample]) -> tuple[float | None, float | None]:
    values = [s.duration_minutes for s in samples]
    return (_avg(values), round(median(values), 2) if values else None)


def averages(samples: Sequence[Sample], attr: str) -> float | None:
    return _avg([v for s in samples if (v := getattr(s, attr)) is not None])
