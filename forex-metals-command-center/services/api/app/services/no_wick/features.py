"""Per-candle features and no-wick classification (candle-local, no lookahead).

ATR and medians use only candles BEFORE the candle being measured, so a large candle never shrinks its own
yardstick. Ratios are null when undefined (zero range, or no prior candles).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from app.domain.candle import Candle
from app.domain.enums import Direction, NoWickClassification, NoWickStrength
from app.services.no_wick.models import CandleFeatures, NoWickConfig
from app.services.pd_arrays.displacement import atr_before
from app.services.structure.swings import true_ranges

EPS = 1e-9
C = NoWickClassification


def compute_features(candles: Sequence[Candle], cfg: NoWickConfig) -> list[CandleFeatures]:
    trs = true_ranges(candles)
    out: list[CandleFeatures] = []
    for i, c in enumerate(candles):
        rng = c.high - c.low
        body = abs(c.close - c.open)
        upper = c.high - max(c.open, c.close)
        lower = min(c.open, c.close) - c.low
        atr = atr_before(trs, i, cfg.atr_period)
        prior = candles[max(0, i - cfg.median_lookback) : i]
        med_body = median(abs(p.close - p.open) for p in prior) if prior else 0.0
        med_range = median(p.high - p.low for p in prior) if prior else 0.0
        out.append(
            CandleFeatures(
                time=c.open_time,
                range=rng,
                body=body,
                upper_wick=upper,
                lower_wick=lower,
                body_pct=body / rng if rng > 0 else None,
                upper_wick_pct=upper / rng if rng > 0 else None,
                lower_wick_pct=lower / rng if rng > 0 else None,
                body_atr=body / atr if atr else None,
                range_atr=rng / atr if atr else None,
                body_to_median=body / med_body if med_body > 0 else None,
                range_to_median=rng / med_range if med_range > 0 else None,
                close_location_pct=(c.close - c.low) / rng * 100 if rng > 0 else None,
            )
        )
    return out


@dataclass(frozen=True)
class Classification:
    direction: Direction
    shape: NoWickClassification
    classification: NoWickClassification
    tags: list[NoWickClassification]
    strength: NoWickStrength
    origin_wick_pct: float
    destination_wick_pct: float


def classify(c: Candle, f: CandleFeatures, cfg: NoWickConfig) -> Classification | None:
    """Shape priority: TRUE marubozu > NEAR marubozu > one-sided origin > one-sided destination.

    NEWS_DRIVEN_NO_WICK is never assigned here: it needs the economic calendar (Phase 13) and the system must
    never invent that fact.
    """
    if f.body_pct is None or f.upper_wick_pct is None or f.lower_wick_pct is None or f.body_atr is None:
        return None
    if c.close == c.open:
        return None
    bullish = c.close > c.open
    direction = Direction.BULLISH if bullish else Direction.BEARISH
    body, up, lo = f.body_pct, f.upper_wick_pct, f.lower_wick_pct
    origin, destination = (lo, up) if bullish else (up, lo)

    if (
        body >= cfg.true_body_pct - EPS
        and up <= cfg.true_max_wick_pct + EPS
        and lo <= cfg.true_max_wick_pct + EPS
    ):
        shape = C.TRUE_BULLISH_MARUBOZU if bullish else C.TRUE_BEARISH_MARUBOZU
    elif (
        body >= cfg.near_body_pct - EPS
        and up <= cfg.near_max_wick_pct + EPS
        and lo <= cfg.near_max_wick_pct + EPS
    ):
        shape = C.NEAR_BULLISH_MARUBOZU if bullish else C.NEAR_BEARISH_MARUBOZU
    elif body >= cfg.one_sided_min_body_pct - EPS and origin <= cfg.one_sided_max_wick_pct + EPS:
        shape = C.BULLISH_NO_LOWER_WICK if bullish else C.BEARISH_NO_UPPER_WICK
    elif body >= cfg.one_sided_min_body_pct - EPS and destination <= cfg.one_sided_max_wick_pct + EPS:
        shape = C.BULLISH_NO_UPPER_WICK if bullish else C.BEARISH_NO_LOWER_WICK
    else:
        return None

    tags = []
    if origin <= cfg.one_sided_max_wick_pct + EPS:
        tags.append(C.NO_ORIGIN_SIDE_WICK)
    if destination <= cfg.one_sided_max_wick_pct + EPS:
        tags.append(C.NO_DESTINATION_SIDE_WICK)

    if f.body_atr >= cfg.exceptional_body_atr - EPS:
        strength = NoWickStrength.EXCEPTIONAL
    elif f.body_atr >= cfg.strong_body_atr - EPS:
        strength = NoWickStrength.STRONG
    elif f.body_atr >= cfg.meaningful_body_atr - EPS:
        strength = NoWickStrength.MEANINGFUL
    else:
        strength = NoWickStrength.INSIGNIFICANT
    return Classification(
        direction=direction,
        shape=shape,
        classification=C.INSIGNIFICANT_NO_WICK if strength is NoWickStrength.INSIGNIFICANT else shape,
        tags=tags,
        strength=strength,
        origin_wick_pct=origin,
        destination_wick_pct=destination,
    )
