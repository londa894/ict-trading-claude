"""Builders for hand-crafted structure scenarios (synthetic test prices, not market facts)."""

from __future__ import annotations

import random
from datetime import timedelta

from app.domain.candle import Candle, RawBar
from app.domain.enums import AssetClass, StructureLevel, Timeframe
from app.services.candles.normalize import build_series
from app.services.structure.models import StructureConfig
from tests.helpers import TUE_10_UTC, consecutive_bars


def hlc_candles(rows: list[tuple[float, float, float]], tf: Timeframe = Timeframe.M5) -> list[Candle]:
    """Closed candles from (high, low, close) rows on consecutive market-open slots. open = previous close."""
    slots = [b.open_time for b in consecutive_bars(TUE_10_UTC, len(rows), tf=tf)]
    raw: list[RawBar] = []
    prev_close: float | None = None
    for t, (h, low, c) in zip(slots, rows, strict=True):
        o = prev_close if prev_close is not None and low <= prev_close <= h else (h + low) / 2
        raw.append(
            RawBar(symbol="XAUUSD", timeframe=tf, open_time=t, open=o, high=h, low=low, close=c, volume=1.0)
        )
        prev_close = c
    now = slots[-1] + tf.duration + timedelta(seconds=1)
    series = build_series(
        raw, symbol="XAUUSD", timeframe=tf, source="t", asset_class=AssetClass.METAL, now=now
    )
    assert not [i for i in series.issues if i.severity == "ERROR"], series.issues
    assert all(c.is_closed for c in series.candles)
    return series.candles


def cfg(
    pivot: int = 1, min_candles: int = 1, ranging_bars: int = 10_000, tol_atr: float = 0.0
) -> StructureConfig:
    return StructureConfig(
        pivot_length={StructureLevel.INTERNAL: pivot, StructureLevel.EXTERNAL: pivot},
        equal_tolerance_atr=tol_atr,
        atr_period=14,
        ranging_bars_without_break={
            StructureLevel.INTERNAL: ranging_bars,
            StructureLevel.EXTERNAL: ranging_bars,
        },
        min_candles=min_candles,
    )


def random_walk_candles(n: int, seed: int, tf: Timeframe = Timeframe.M5) -> list[Candle]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    rows = []
    price = 2000.0
    for _ in range(n):
        o = price
        c = o + rng.gauss(0, 1.5)
        h = max(o, c) + abs(rng.gauss(0, 0.8))
        low = min(o, c) - abs(rng.gauss(0, 0.8))
        rows.append((round(h, 2), round(low, 2), round(c, 2)))
        price = c
    return hlc_candles(rows, tf)


# Scenario used by several tests (pivot length 1). Index -> intent:
#  1 swing high 11.0 (confirmed @2)          3 swing low 8.8 (confirmed @4)
#  5 close 11.3 > 11.0 -> BOS bullish (trend_before NONE); protected low = 8.8
#  5 swing high 11.5 (HH, confirmed @6)      7 swing low 10.0 (HL, confirmed @8)
#  9 close 11.9 > 11.5 -> BOS bullish; protected low = 10.0
#  9 swing high 12.0 (HH, @10)               11 swing low 10.6 (HL, @12)
# 13 close 10.4 < 10.6 (not < 10.0) -> CHOCH bearish (TRANSITIONING); 12 swing high 11.5 (LH, @13)
# 14 close 9.7 < protected 10.0 -> MSS bearish; protected high = 11.5
UPTREND_THEN_MSS = [
    (10.0, 9.0, 9.5),
    (11.0, 9.5, 10.8),
    (10.5, 9.2, 9.4),
    (10.0, 8.8, 9.0),
    (10.2, 9.1, 10.0),
    (11.5, 9.9, 11.3),
    (11.2, 10.5, 10.7),
    (10.9, 10.0, 10.2),
    (11.0, 10.3, 10.9),
    (12.0, 10.8, 11.9),
    (11.8, 11.0, 11.2),
    (11.4, 10.6, 10.8),
    (11.5, 10.7, 11.3),
    (11.3, 10.2, 10.4),
    (10.6, 9.6, 9.7),
]
