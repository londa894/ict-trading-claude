"""Pivot swing detection (deterministic, no lookahead).

A candle at index j is a swing HIGH with pivot length L when
    high[j] >  max(high[j-L .. j-1])   (strictly above the left side: the first of equal highs wins)
    high[j] >= max(high[j+1 .. j+L])   (not exceeded on the right side)
and symmetrically for swing LOWs. The swing is only KNOWN once candle j+L has closed
(`confirm_index = j + L`); nothing may use it before then.

Labels compare with the previous swing of the same kind, using a tolerance of
`equal_tolerance_atr * ATR(atr_period)` where the ATR uses only candles up to the confirmation index.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.candle import Candle
from app.domain.enums import SwingKind, SwingLabel


@dataclass
class RawSwing:
    kind: SwingKind
    index: int
    confirm_index: int
    price: float
    label: SwingLabel
    broken_index: int | None = None
    broken_by: str | None = None

    @property
    def is_broken(self) -> bool:
        return self.broken_index is not None


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    out: list[float] = []
    prev_close: float | None = None
    for c in candles:
        tr = c.high - c.low if prev_close is None else max(c.high, prev_close) - min(c.low, prev_close)
        out.append(tr)
        prev_close = c.close
    return out


def atr_at(trs: Sequence[float], index: int, period: int) -> float:
    window = trs[max(0, index - period + 1) : index + 1]
    return sum(window) / len(window) if window else 0.0


def _label(kind: SwingKind, price: float, previous: RawSwing | None, tolerance: float) -> SwingLabel:
    if previous is None:
        return SwingLabel.NONE
    diff = price - previous.price
    if abs(diff) <= tolerance:
        return SwingLabel.EH if kind is SwingKind.HIGH else SwingLabel.EL
    if kind is SwingKind.HIGH:
        return SwingLabel.HH if diff > 0 else SwingLabel.LH
    return SwingLabel.HL if diff > 0 else SwingLabel.LL


def detect_swings(
    candles: Sequence[Candle], pivot: int, equal_tolerance_atr: float, atr_period: int
) -> list[RawSwing]:
    """Swings in confirmation order (HIGH before LOW when confirmed by the same candle)."""
    if pivot < 1:
        raise ValueError("pivot length must be >= 1")
    trs = true_ranges(candles)
    swings: list[RawSwing] = []
    previous: dict[SwingKind, RawSwing | None] = {SwingKind.HIGH: None, SwingKind.LOW: None}
    for i in range(2 * pivot, len(candles)):
        j = i - pivot
        c = candles[j]
        left = candles[j - pivot : j]
        right = candles[j + 1 : i + 1]
        tolerance = equal_tolerance_atr * atr_at(trs, i, atr_period)
        if c.high > max(x.high for x in left) and c.high >= max(x.high for x in right):
            s = RawSwing(
                SwingKind.HIGH,
                j,
                i,
                c.high,
                _label(SwingKind.HIGH, c.high, previous[SwingKind.HIGH], tolerance),
            )
            swings.append(s)
            previous[SwingKind.HIGH] = s
        if c.low < min(x.low for x in left) and c.low <= min(x.low for x in right):
            s = RawSwing(
                SwingKind.LOW, j, i, c.low, _label(SwingKind.LOW, c.low, previous[SwingKind.LOW], tolerance)
            )
            swings.append(s)
            previous[SwingKind.LOW] = s
    return swings
