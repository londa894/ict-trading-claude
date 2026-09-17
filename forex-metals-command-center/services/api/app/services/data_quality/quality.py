"""Data-quality classification (spec STEP 12: LIVE / CURRENT / DELAYED / STALE / DISCONNECTED / INVALID)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from app.contracts import load_spec
from app.domain.base import require_utc
from app.domain.candle import Candle
from app.domain.enums import AssetClass, DataQuality
from app.domain.issues import ValidationIssue, has_errors
from app.services.timeframes.core import expected_slots

# Best -> worst. Used to combine qualities; the worst always wins (fail-safe).
QUALITY_ORDER: tuple[DataQuality, ...] = (
    DataQuality.LIVE,
    DataQuality.CURRENT,
    DataQuality.DELAYED,
    DataQuality.STALE,
    DataQuality.DISCONNECTED,
    DataQuality.INVALID,
)

USABLE_QUALITIES = frozenset({DataQuality.LIVE, DataQuality.CURRENT, DataQuality.DELAYED})


def worst_quality(qualities: Iterable[DataQuality]) -> DataQuality:
    items = list(qualities)
    if not items:
        return DataQuality.INVALID
    return max(items, key=QUALITY_ORDER.index)


def classify_quote_age(timestamp: datetime, now: datetime) -> DataQuality:
    cfg = load_spec("data_quality")["quoteAge"]
    age = (require_utc(now, "now") - require_utc(timestamp, "timestamp")).total_seconds()
    if age < -cfg["liveMaxSeconds"]:
        # Timestamp materially in the future: clock skew or bad data. Never trust it.
        return DataQuality.INVALID
    if age <= cfg["liveMaxSeconds"]:
        return DataQuality.LIVE
    if age <= cfg["currentMaxSeconds"]:
        return DataQuality.CURRENT
    if age <= cfg["delayedMaxSeconds"]:
        return DataQuality.DELAYED
    return DataQuality.STALE


def missing_bars_since(last_closed: Candle, asset_class: AssetClass, now: datetime, *, limit: int) -> int:
    """Count expected-but-absent closed bars after the latest closed candle (bounded by `limit`)."""
    cfg = load_spec("data_quality")["candleLag"]
    until = require_utc(now, "now") - timedelta(seconds=cfg["graceSeconds"])
    tf = last_closed.timeframe
    if tf.is_engine_supported:
        return sum(1 for _ in expected_slots(asset_class, tf, last_closed.open_time, until, limit=limit))
    return limit  # W1/MN1: freshness cannot be proven by the candle engine yet -> fail safe


def classify_candle_series(
    candles: Sequence[Candle],
    issues: Sequence[ValidationIssue],
    asset_class: AssetClass,
    now: datetime,
) -> DataQuality:
    """Series-level quality. Historical candles can at best be CURRENT (LIVE requires a live stream)."""
    if has_errors(list(issues)):
        return DataQuality.INVALID
    closed = [c for c in candles if c.is_closed]
    if not closed:
        return DataQuality.INVALID
    cfg = load_spec("data_quality")["candleLag"]
    delayed_max = int(cfg["delayedMaxMissingBars"])
    missing = missing_bars_since(closed[-1], asset_class, now, limit=delayed_max + 1)
    if missing <= int(cfg["currentMaxMissingBars"]):
        return DataQuality.CURRENT
    if missing <= delayed_max:
        return DataQuality.DELAYED
    return DataQuality.STALE
