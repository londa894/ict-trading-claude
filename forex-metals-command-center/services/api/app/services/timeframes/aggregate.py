"""Deterministic candle aggregation from a fixed intraday source timeframe to a higher timeframe.

Rules:
- Source candles must be validated, same symbol/source, ascending, of the source timeframe (M1..H1).
- Target (M3..H1, H4, D1) must be a whole multiple of the source. H4/D1 use New York 17:00 buckets.
- A bucket is closed only when every expected constituent (market-open source slot) is present and
  closed, and the bucket's own close_time <= now.
- A past bucket (close_time <= now) with missing constituents is emitted with is_closed=False and an
  INCOMPLETE_BUCKET issue so gaps stay visible. A still-forming bucket is not an issue.
- Aggregates inherit the worst data quality of their constituents. No bars are fabricated.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import groupby, pairwise

from app.domain.candle import Candle
from app.domain.enums import AssetClass, IssueSeverity, Timeframe, ValidationIssueCode
from app.domain.issues import ValidationIssue
from app.services.data_quality.market_hours import is_market_open
from app.services.data_quality.quality import worst_quality
from app.services.timeframes.core import bucket_start, next_bucket_start


class AggregationError(ValueError):
    pass


def aggregate(
    candles: Sequence[Candle],
    source_tf: Timeframe,
    target_tf: Timeframe,
    asset_class: AssetClass,
    now: datetime,
) -> tuple[list[Candle], list[ValidationIssue]]:
    if not source_tf.is_fixed_intraday or not target_tf.is_engine_supported:
        raise AggregationError(f"cannot aggregate {source_tf} -> {target_tf}")
    ratio, remainder = divmod(target_tf.duration, source_tf.duration)
    if remainder or ratio < 2:
        raise AggregationError(f"{target_tf} is not a higher whole multiple of {source_tf}")
    if not candles:
        return [], []

    if len({c.symbol for c in candles}) != 1 or len({c.source for c in candles}) != 1:
        raise AggregationError("cannot aggregate mixed symbols or sources")
    if any(c.timeframe is not source_tf for c in candles):
        raise AggregationError("all candles must be of the source timeframe")
    if any(b.open_time <= a.open_time for a, b in pairwise(candles)):
        raise AggregationError("candles must be strictly ascending by open_time (normalize first)")

    out: list[Candle] = []
    issues: list[ValidationIssue] = []
    for bucket_open, group in groupby(candles, key=lambda c: bucket_start(c.open_time, target_tf)):
        members = list(group)
        bucket_close = bucket_open + target_tf.duration
        if next_bucket_start(bucket_open, target_tf) != bucket_close:
            # Would only happen if a DST change fell inside trading hours. Never silently mis-size.
            raise AggregationError(f"{target_tf} bucket at {bucket_open} is not {target_tf.duration} long")
        expected = [
            bucket_open + i * source_tf.duration
            for i in range(ratio)
            if is_market_open(asset_class, bucket_open + i * source_tf.duration)
        ]
        present = {c.open_time for c in members}
        complete = all(t in present for t in expected)
        closed = complete and all(c.is_closed for c in members) and bucket_close <= now
        if not complete and bucket_close <= now:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.INCOMPLETE_BUCKET,
                    severity=IssueSeverity.WARNING,
                    message=f"{target_tf} bucket {len(present & set(expected))}/{len(expected)} constituents",
                    at=bucket_open,
                )
            )
        volumes = [c.volume for c in members]
        out.append(
            Candle(
                symbol=members[0].symbol,
                timeframe=target_tf,
                open_time=bucket_open,
                close_time=bucket_close,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                volume=None if any(v is None for v in volumes) else sum(v for v in volumes if v is not None),
                source=members[0].source,
                is_closed=closed,
                data_quality=worst_quality([c.data_quality for c in members]),
            )
        )
    return out, issues
