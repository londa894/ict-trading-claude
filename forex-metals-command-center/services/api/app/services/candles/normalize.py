"""Ingestion -> normalization -> validated candle series.

Pipeline (deterministic, order matters):
 1. Per bar: symbol/timeframe match, UTC timestamp, alignment, price sanity. Failing bars are excluded.
 2. Out-of-order input is detected (WARNING) and sorted.
 3. Duplicates: identical copies dropped (WARNING); conflicting copies -> ALL copies excluded (ERROR),
    because we cannot know which one is true.
 4. Missing bars inside market hours are reported (WARNING). Gaps are never filled with fabricated bars.
 5. Range spikes vs. prior median are flagged (WARNING, no lookahead).
 6. Series quality is classified; every candle carries the series quality.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise

from app.domain.base import ApiModel, require_utc
from app.domain.candle import Candle, RawBar
from app.domain.enums import AssetClass, DataQuality, IssueSeverity, Timeframe, ValidationIssueCode
from app.domain.issues import ValidationIssue
from app.services.data_quality.quality import classify_candle_series
from app.services.data_quality.validation import detect_range_spikes, validate_bar_prices
from app.services.timeframes.core import expected_slots, is_aligned

Code = ValidationIssueCode
Sev = IssueSeverity

# Bound gap enumeration work; beyond this the reported count is a lower bound.
MAX_GAP_SLOTS_COUNTED = 50_000


class CandleSeries(ApiModel):
    symbol: str
    timeframe: Timeframe
    source: str
    candles: list[Candle]
    issues: list[ValidationIssue]
    quality: DataQuality

    @property
    def closed(self) -> list[Candle]:
        return [c for c in self.candles if c.is_closed]


def _screen_bar(bar: RawBar, symbol: str, timeframe: Timeframe) -> list[ValidationIssue]:
    at = bar.open_time if bar.open_time.tzinfo is not None else None
    if bar.symbol.upper() != symbol:
        return [ValidationIssue(code=Code.SYMBOL_MISMATCH, severity=Sev.ERROR, message=bar.symbol)]
    if bar.timeframe is not timeframe:
        return [ValidationIssue(code=Code.TIMEFRAME_MISMATCH, severity=Sev.ERROR, message=bar.timeframe)]
    if bar.open_time.tzinfo is None:
        # A naive timestamp's zone is unknowable. Never assume.
        return [ValidationIssue(code=Code.NON_UTC_TIMESTAMP, severity=Sev.ERROR, message="naive timestamp")]
    if not is_aligned(bar.open_time.astimezone(UTC), timeframe):
        return [
            ValidationIssue(
                code=Code.MISALIGNED_OPEN_TIME, severity=Sev.ERROR, message="open_time not on boundary", at=at
            )
        ]
    return validate_bar_prices(bar)


def _merge_counts(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Collapse repeated WARNING/ERROR codes without a timestamp into counted issues."""
    buckets: dict[tuple[Code, Sev, str], list[ValidationIssue]] = defaultdict(list)
    ordered: list[ValidationIssue] = []
    for issue in issues:
        if issue.at is None:
            key = (issue.code, issue.severity, issue.message)
            if not buckets[key]:
                ordered.append(issue)
            buckets[key].append(issue)
        else:
            ordered.append(issue)
    return [
        i.model_copy(update={"count": sum(x.count for x in buckets[(i.code, i.severity, i.message)])})
        if i.at is None
        else i
        for i in ordered
    ]


def detect_missing_bars(candles: Sequence[Candle], asset_class: AssetClass) -> list[ValidationIssue]:
    if not candles or not candles[0].timeframe.is_engine_supported:
        return []
    tf = candles[0].timeframe
    issues: list[ValidationIssue] = []
    for prev, cur in pairwise(candles):
        if cur.open_time - prev.open_time <= tf.duration:
            continue
        slots = list(
            expected_slots(asset_class, tf, prev.open_time, cur.open_time, limit=MAX_GAP_SLOTS_COUNTED)
        )
        if slots:
            issues.append(
                ValidationIssue(
                    code=Code.MISSING_BARS,
                    severity=Sev.WARNING,
                    message=f"{len(slots)} expected {tf} bar(s) missing during market hours",
                    at=slots[0],
                    count=len(slots),
                )
            )
    return issues


def build_series(
    raw_bars: Sequence[RawBar],
    *,
    symbol: str,
    timeframe: Timeframe,
    source: str,
    asset_class: AssetClass,
    now: datetime,
) -> CandleSeries:
    now = require_utc(now, "now")
    symbol = symbol.upper()
    issues: list[ValidationIssue] = []

    if not raw_bars:
        return CandleSeries(
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            candles=[],
            issues=[ValidationIssue(code=Code.EMPTY_SERIES, severity=Sev.ERROR, message="no bars returned")],
            quality=DataQuality.INVALID,
        )

    # 1. screen each bar
    accepted: list[RawBar] = []
    for bar in raw_bars:
        bar_issues = _screen_bar(bar, symbol, timeframe)
        issues.extend(bar_issues)
        if not any(i.severity is Sev.ERROR for i in bar_issues):
            accepted.append(bar)

    # 2. ordering
    utc_times = [b.open_time.astimezone(UTC) for b in accepted]
    inversions = sum(1 for a, b in pairwise(utc_times) if b < a)
    if inversions:
        issues.append(
            ValidationIssue(
                code=Code.OUT_OF_ORDER,
                severity=Sev.WARNING,
                message=f"{inversions} out-of-order bar(s) re-sorted",
                count=inversions,
            )
        )

    # 3. duplicates
    by_time: dict[datetime, list[RawBar]] = defaultdict(list)
    for bar in accepted:
        by_time[bar.open_time.astimezone(UTC)].append(bar)

    candles: list[Candle] = []
    for open_time in sorted(by_time):
        copies = by_time[open_time]
        first = copies[0]
        if len(copies) > 1:
            fields = {(b.open, b.high, b.low, b.close, b.volume) for b in copies}
            if len(fields) > 1:
                issues.append(
                    ValidationIssue(
                        code=Code.DUPLICATE_CONFLICT,
                        severity=Sev.ERROR,
                        message=f"{len(copies)} conflicting bars share one open_time; all excluded",
                        at=open_time,
                    )
                )
                continue
            issues.append(
                ValidationIssue(
                    code=Code.DUPLICATE_IDENTICAL,
                    severity=Sev.WARNING,
                    message="identical duplicate dropped",
                    at=open_time,
                    count=len(copies) - 1,
                )
            )
        close_time = open_time + timeframe.duration
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=first.open,
                high=first.high,
                low=first.low,
                close=first.close,
                volume=first.volume,
                source=source,
                is_closed=close_time <= now,
                data_quality=DataQuality.INVALID,  # replaced below once series quality is known
            )
        )

    # A bar that has not closed yet can only be the latest one; anything else is a clock/data problem.
    future = [c for c in candles if c.open_time > now]
    if future:
        issues.append(
            ValidationIssue(
                code=Code.NON_UTC_TIMESTAMP,
                severity=Sev.ERROR,
                message=f"{len(future)} bar(s) open in the future (timezone or clock error)",
                at=future[0].open_time,
            )
        )

    # 4. gaps, 5. spikes (closed candles only; the forming bar is not judged)
    closed = [c for c in candles if c.is_closed]
    issues.extend(detect_missing_bars(candles, asset_class))
    issues.extend(detect_range_spikes(closed))

    issues = _merge_counts(issues)
    if not candles and not any(i.severity is Sev.ERROR for i in issues):
        issues.append(ValidationIssue(code=Code.EMPTY_SERIES, severity=Sev.ERROR, message="no usable bars"))

    quality = classify_candle_series(candles, issues, asset_class, now)
    candles = [c.model_copy(update={"data_quality": quality}) for c in candles]
    return CandleSeries(
        symbol=symbol, timeframe=timeframe, source=source, candles=candles, issues=issues, quality=quality
    )
