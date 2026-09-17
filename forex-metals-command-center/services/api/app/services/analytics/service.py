"""Analytics service: normalises verified closed journal trades or paper sims into samples and describes them.

Descriptive only (authority DESCRIPTIVE_ONLY): no verdict, probability, forecast or trade signal.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.contracts import strategy_version
from app.domain.enums import (
    AnalyticsSource,
    Direction,
    JournalEntryKind,
    JournalStatus,
    LiquidityEventType,
    PaperStatus,
    ProcessClassification,
    SnapshotIntegrity,
    TradeResult,
)
from app.services.analytics.engine import (
    DIMENSIONS,
    averages,
    breakdown,
    dol_accuracy,
    downsample,
    drawdown,
    durations,
    group_stats,
    process_stats,
)
from app.services.analytics.models import (
    AnalyticsConfig,
    AnalyticsFilters,
    AnalyticsReport,
    ExcludedCounts,
    Sample,
    Unavailable,
)
from app.services.journal.models import JournalConfig, JournalEntry, SnapshotSummary
from app.services.journal.service import JournalService
from app.services.journal.store import JournalUnavailableError
from app.services.paper.models import PaperSim
from app.services.paper.service import PaperService

WINS = frozenset({ProcessClassification.VALID_WIN, ProcessClassification.BAD_PROCESS_WIN})
ALERT_USEFULNESS = Unavailable(
    available=False,
    reason="alerts are kept in memory and are not linked to journal or paper records yet",
)


def _session(summary: SnapshotSummary) -> str:
    return "+".join(sorted(summary.active_sessions)) or "NONE"


EVENT_TYPES = frozenset(e.value for e in LiquidityEventType)


def _liquidity(summary: SnapshotSummary) -> str | None:
    """The event type token of "M15 SWEEP BSL PDH @ ..." (the timeframe is not the event)."""
    tokens = (summary.liquidity_event or "").split()
    return next((t for t in tokens if t in EVENT_TYPES), "OTHER" if tokens else None)


def journal_sample(e: JournalEntry, tol: float) -> Sample | None:
    o, t = e.outcome, e.trade
    if e.kind is not JournalEntryKind.TRADE or e.status is not JournalStatus.CLOSED or o is None or t is None:
        return None
    s = e.summary
    return Sample(
        id=e.id,
        source=AnalyticsSource.JOURNAL,
        symbol=e.symbol,
        direction=t.direction,
        closed_at=o.exited_at,
        r=o.r_multiple,
        win=o.classification in WINS,
        breakeven=o.result is TradeResult.BREAK_EVEN
        or (o.r_multiple is not None and abs(o.r_multiple) <= tol),
        result=o.result.value,
        classification=o.classification,
        violations=tuple(o.violations),
        duration_minutes=o.duration_minutes,
        mfe_r=o.mfe_r,
        mae_r=o.mae_r,
        entry_efficiency=o.entry_efficiency,
        exit_efficiency=o.exit_efficiency,
        session=_session(s),
        setup_type=s.setup_type or "NONE",
        timeframe=s.execution_timeframe or "UNKNOWN",
        day_of_week=s.day_of_week,
        no_wick=s.no_wick,
        liquidity_event=_liquidity(s),
        dol=s.primary_dol,
        entry_price=t.entry,
        mfe_price=o.mfe_price,
        synthetic=s.is_synthetic or o.extremes_synthetic,
        strategy_version=e.strategy_version,
    )


def paper_sample(p: PaperSim, tol: float) -> Sample | None:
    r = p.result
    if p.status is not PaperStatus.CLOSED or r is None or p.fill_price is None or p.exited_at is None:
        return None
    sign = 1 if p.direction is Direction.BULLISH else -1
    entry = p.fill_price if sign * (p.fill_price - p.stop) > 0 else p.reference_price
    mfe_price = entry + sign * r.mfe_r * abs(entry - p.stop) if r.mfe_r is not None else None
    s = p.summary
    return Sample(
        id=p.id,
        source=AnalyticsSource.PAPER,
        symbol=p.symbol,
        direction=p.direction,
        closed_at=p.exited_at,
        r=r.net_r_multiple,
        win=r.classification in WINS,
        breakeven=r.result is TradeResult.BREAK_EVEN
        or (r.net_r_multiple is not None and abs(r.net_r_multiple) <= tol),
        result=r.result.value,
        classification=r.classification,
        violations=tuple(r.violations),
        duration_minutes=r.duration_minutes,
        mfe_r=r.mfe_r,
        mae_r=r.mae_r,
        entry_efficiency=r.entry_efficiency,
        exit_efficiency=r.exit_efficiency,
        session=_session(s),
        setup_type=s.setup_type or ("ENGINE_PLAN" if p.source.value == "ENGINE_PLAN" else "NONE"),
        timeframe=s.execution_timeframe or "M5",
        day_of_week=s.day_of_week,
        no_wick=s.no_wick,
        liquidity_event=_liquidity(s),
        dol=s.primary_dol,
        entry_price=entry,
        mfe_price=mfe_price,
        synthetic=p.is_synthetic,
        strategy_version=p.strategy_version,
    )


class AnalyticsService:
    def __init__(
        self,
        journal: JournalService,
        paper: PaperService,
        clock: Callable[[], datetime] | None = None,
        cfg: AnalyticsConfig | None = None,
    ) -> None:
        self._journal = journal
        self._paper = paper
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or AnalyticsConfig.from_spec()
        self._tol = JournalConfig.from_spec().break_even_tolerance_r

    async def report(
        self,
        source: AnalyticsSource = AnalyticsSource.JOURNAL,
        *,
        symbol: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        include_synthetic: bool = False,
        strategy_version_filter: str | None = None,
    ) -> AnalyticsReport:
        now = self._clock()
        filters = AnalyticsFilters(
            source=source,
            symbol=symbol.upper() if symbol else None,
            start=start,
            end=end,
            include_synthetic=include_synthetic,
            strategy_version=strategy_version_filter,
        )
        tampered = synthetic = not_closed = filtered = 0
        decisions = {JournalEntryKind.NO_TRADE.value: 0, JournalEntryKind.MISSED_ENTRY.value: 0}
        candidates: list[Sample] = []
        try:
            if source is AnalyticsSource.JOURNAL:
                for e in (await self._journal.export()).entries:
                    if e.symbol != (filters.symbol or e.symbol):
                        filtered += 1
                        continue
                    if e.kind is not JournalEntryKind.TRADE:
                        decisions[e.kind.value] += 1
                        continue
                    integrity_ok = e.snapshot.integrity is SnapshotIntegrity.VERIFIED and all(
                        o.integrity is SnapshotIntegrity.VERIFIED for o in e.outcome_revisions
                    )
                    if not integrity_ok:
                        tampered += 1
                        continue
                    sample = journal_sample(e, self._tol)
                    if sample is None:
                        not_closed += 1
                    else:
                        candidates.append(sample)
            else:
                for p in await self._paper.export_sims():
                    if p.symbol != (filters.symbol or p.symbol):
                        filtered += 1
                        continue
                    if p.integrity is not SnapshotIntegrity.VERIFIED:
                        tampered += 1
                        continue
                    sample = paper_sample(p, self._tol)
                    if sample is None:
                        not_closed += 1
                    else:
                        candidates.append(sample)
        except JournalUnavailableError as exc:  # PaperUnavailableError is a subclass
            return self._empty(filters, str(exc), now)

        samples: list[Sample] = []
        for s in candidates:
            if (
                (start and s.closed_at < start)
                or (end and s.closed_at >= end)
                or (strategy_version_filter and s.strategy_version != strategy_version_filter)
            ):
                filtered += 1
            elif s.synthetic and not include_synthetic:
                synthetic += 1
            else:
                samples.append(s)
        cfg = self._cfg
        dd, points = drawdown(samples)
        avg_duration, median_duration = durations(samples)
        return AnalyticsReport(
            filters=filters,
            available=True,
            reason=None,
            overall=group_stats("ALL", samples, cfg),
            drawdown=dd,
            avg_duration_minutes=avg_duration,
            median_duration_minutes=median_duration,
            avg_mfe_r=averages(samples, "mfe_r"),
            avg_mae_r=averages(samples, "mae_r"),
            avg_entry_efficiency=averages(samples, "entry_efficiency"),
            avg_exit_efficiency=averages(samples, "exit_efficiency"),
            breakdowns=[breakdown(name, samples, key, cfg) for name, key in DIMENSIONS],
            process=process_stats(samples, cfg),
            dol=dol_accuracy(samples, cfg),
            alert_usefulness=ALERT_USEFULNESS,
            decision_records=decisions if source is AnalyticsSource.JOURNAL else {},
            excluded=ExcludedCounts(
                tampered=tampered, synthetic=synthetic, not_closed=not_closed, filtered=filtered
            ),
            includes_synthetic=any(s.synthetic for s in samples),
            strategy_versions=sorted({s.strategy_version for s in samples}),
            equity_curve=downsample(points, cfg.equity_curve_max_points),
            disclaimer=cfg.disclaimer,
            authority="DESCRIPTIVE_ONLY",
            strategy_version=strategy_version(),
            generated_at=now,
        )

    def _empty(self, filters: AnalyticsFilters, reason: str, now: datetime) -> AnalyticsReport:
        empty = group_stats("ALL", [], self._cfg)
        dd, _ = drawdown([])
        return AnalyticsReport(
            filters=filters,
            available=False,
            reason=reason,
            overall=empty,
            drawdown=dd,
            avg_duration_minutes=None,
            median_duration_minutes=None,
            avg_mfe_r=None,
            avg_mae_r=None,
            avg_entry_efficiency=None,
            avg_exit_efficiency=None,
            breakdowns=[],
            process=process_stats([], self._cfg),
            dol=dol_accuracy([], self._cfg),
            alert_usefulness=ALERT_USEFULNESS,
            decision_records={},
            excluded=ExcludedCounts(tampered=0, synthetic=0, not_closed=0, filtered=0),
            includes_synthetic=False,
            strategy_versions=[],
            equity_curve=[],
            disclaimer=self._cfg.disclaimer,
            authority="DESCRIPTIVE_ONLY",
            strategy_version=strategy_version(),
            generated_at=now,
        )
