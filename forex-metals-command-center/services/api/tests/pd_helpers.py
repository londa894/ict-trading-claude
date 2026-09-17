"""Exact-OHLC builders for displacement/FVG scenarios (synthetic test prices, not market facts)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from app.domain.candle import Candle, RawBar
from app.domain.enums import AssetClass, Timeframe
from app.services.candles.normalize import build_series
from app.services.pd_arrays.models import PdArrayConfig
from tests.helpers import TUE_10_UTC, consecutive_bars

# 14 flat candles with true range exactly 1.0 -> ATR = 1.0 for anything starting at index 14.
BASE: list[tuple[float, float, float, float]] = [(10.0, 10.5, 9.5, 10.0)] * 14


def ohlc_candles(rows: list[tuple[float, float, float, float]], tf: Timeframe = Timeframe.M5) -> list[Candle]:
    slots = [b.open_time for b in consecutive_bars(TUE_10_UTC, len(rows), tf=tf)]
    raw = [
        RawBar(symbol="XAUUSD", timeframe=tf, open_time=t, open=o, high=h, low=low, close=c, volume=1.0)
        for t, (o, h, low, c) in zip(slots, rows, strict=True)
    ]
    now = slots[-1] + tf.duration + timedelta(seconds=1)
    series = build_series(
        raw, symbol="XAUUSD", timeframe=tf, source="t", asset_class=AssetClass.METAL, now=now
    )
    errors = [i for i in series.issues if i.severity == "ERROR"]
    assert not errors, errors
    return series.candles


def mirror(rows: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Reflect around 20: bullish scenarios become bearish ones (high <-> low)."""
    return [(20 - o, 20 - low, 20 - h, 20 - c) for o, h, low, c in rows]


def pd_cfg(**overrides: object) -> PdArrayConfig:
    return replace(PdArrayConfig.from_spec(), **overrides)  # type: ignore[arg-type]
