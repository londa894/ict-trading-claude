"""Deterministic validators for candles and quotes. Validators report; they never repair prices."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from statistics import median

from app.contracts import load_spec
from app.domain.candle import Candle, RawBar
from app.domain.enums import AssetClass, IssueSeverity, ValidationIssueCode
from app.domain.issues import ValidationIssue
from app.domain.quote import Quote

Code = ValidationIssueCode
Sev = IssueSeverity


def _issue(code: Code, severity: Sev, message: str, at: datetime | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message, at=at)


def validate_bar_prices(bar: RawBar) -> list[ValidationIssue]:
    """Price sanity for a single bar. ERROR issues make the bar unusable."""
    at = bar.open_time
    values = [bar.open, bar.high, bar.low, bar.close]
    if bar.volume is not None:
        values.append(bar.volume)
    if not all(math.isfinite(v) for v in values):
        return [_issue(Code.NON_FINITE_VALUE, Sev.ERROR, "bar contains NaN/inf", at=at)]

    issues: list[ValidationIssue] = []
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        issues.append(_issue(Code.NON_POSITIVE_PRICE, Sev.ERROR, "price <= 0", at=at))
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.high < bar.low:
        issues.append(_issue(Code.IMPOSSIBLE_OHLC, Sev.ERROR, "high/low do not bound open/close", at=at))
    elif bar.high == bar.low:
        issues.append(_issue(Code.ZERO_RANGE, Sev.WARNING, "zero-range bar (high == low)", at=at))
    if bar.volume is not None and bar.volume < 0:
        issues.append(_issue(Code.NEGATIVE_VOLUME, Sev.ERROR, "negative volume", at=at))
    return issues


def detect_range_spikes(candles: Sequence[Candle]) -> list[ValidationIssue]:
    """Flag candles whose range is an extreme multiple of the preceding median range.

    Uses only candles *before* the one being judged (no lookahead). WARNING only: spikes can be real
    (e.g. high-impact news), but they must never silently pass as clean data.
    """
    cfg = load_spec("data_quality")["badTick"]
    multiple = float(cfg["candleRangeMedianMultiple"])
    lookback = int(cfg["candleRangeLookback"])
    min_samples = int(cfg["candleRangeMinSamples"])
    issues: list[ValidationIssue] = []
    for i, candle in enumerate(candles):
        window = [c.high - c.low for c in candles[max(0, i - lookback) : i] if c.high > c.low]
        if len(window) < min_samples:
            continue
        ref = median(window)
        if ref > 0 and (candle.high - candle.low) > multiple * ref:
            issues.append(
                _issue(
                    Code.SUSPECT_BAD_TICK,
                    Sev.WARNING,
                    f"range {candle.high - candle.low:.5g} > {multiple:g}x median {ref:.5g}",
                    at=candle.open_time,
                )
            )
    return issues


def validate_quote(
    quote: Quote,
    asset_class: AssetClass,
    reference_price: float | None = None,
    max_spread: float | None = None,
) -> list[ValidationIssue]:
    at = quote.timestamp
    if not (math.isfinite(quote.bid) and math.isfinite(quote.ask)):
        return [_issue(Code.NON_FINITE_VALUE, Sev.ERROR, "quote contains NaN/inf", at=at)]
    issues: list[ValidationIssue] = []
    if quote.bid <= 0 or quote.ask <= 0:
        issues.append(_issue(Code.NON_POSITIVE_PRICE, Sev.ERROR, "bid/ask <= 0", at=at))
        return issues
    if quote.bid > quote.ask:
        issues.append(_issue(Code.CROSSED_QUOTE, Sev.ERROR, "bid > ask", at=at))
    if max_spread is not None and quote.spread > max_spread:
        issues.append(
            _issue(Code.WIDE_SPREAD, Sev.WARNING, f"spread {quote.spread:.5g} > {max_spread}", at=at)
        )
    if reference_price is not None and reference_price > 0:
        max_jump = float(load_spec("data_quality")["badTick"][asset_class.value]["maxJumpPct"])
        jump_pct = abs(quote.mid - reference_price) / reference_price * 100
        if jump_pct > max_jump:
            issues.append(
                _issue(Code.SUSPECT_BAD_TICK, Sev.ERROR, f"mid moved {jump_pct:.3f}% > {max_jump}%", at=at)
            )
    return issues


def compare_providers(primary: Quote, secondary: Quote, asset_class: AssetClass) -> list[ValidationIssue]:
    """Cross-check two independent providers. Disagreement is an ERROR: we cannot know which is right."""
    if primary.symbol != secondary.symbol:
        return [_issue(Code.SYMBOL_MISMATCH, Sev.ERROR, "cannot compare different symbols")]
    threshold = float(load_spec("data_quality")["badTick"][asset_class.value]["providerDisagreementPct"])
    diff_pct = abs(primary.mid - secondary.mid) / primary.mid * 100
    if diff_pct > threshold:
        return [
            _issue(
                Code.PROVIDER_DISAGREEMENT,
                Sev.ERROR,
                f"{primary.source} vs {secondary.source} differ {diff_pct:.4f}% > {threshold}%",
                at=primary.timestamp,
            )
        ]
    return []
