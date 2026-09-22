"""IMR (Immediate Rebalance) detection — reversal-revamp Phase A, spec section 2.

Synthetic OHLC (BASE = 14 flat candles, ATR = 1.0), so a body of 3.0 is an EXCEPTIONAL displacement.
"""

from __future__ import annotations

from app.domain.enums import Direction, PdArrayEventType, PdArrayType, TrendDirection
from app.services.pd_arrays.displacement import detect_displacements
from app.services.pd_arrays.fvg import detect_fvgs
from app.services.pd_arrays.imr import detect_imrs
from tests.pd_helpers import BASE, mirror, ohlc_candles, pd_cfg

# a (i-2): normal candle, high 10.5.  b (i-1): EXCEPTIONAL bullish displacement (body 3.0).
# c (i): closes at 13.2 (> a.high) but wicks to 10.4 (<= a.high) -> overlaps candle 1, no FVG gap = IMR.
_BULL = [(10.0, 10.5, 9.5, 10.0), (10.0, 13.2, 9.9, 13.0), (13.0, 13.5, 10.4, 13.2)]


def test_imr_bullish_detects_immediate_rebalance() -> None:
    candles = ohlc_candles([*BASE, *_BULL])
    disps = detect_displacements(candles, pd_cfg())
    result = detect_imrs(candles, disps, TrendDirection.BULLISH, pd_cfg())

    imrs = [z for z in result.zones if z.type is PdArrayType.IMR]
    assert len(imrs) == 1
    z = imrs[0]
    assert z.direction is Direction.BULLISH
    assert (z.bottom, z.top) == (10.5, 13.2)  # [a.high, c.close]
    assert z.active and z.state.value == "FRESH"
    assert any(e.type is PdArrayEventType.IMR_CREATED and e.zone_type is PdArrayType.IMR for e in result.events)

    # and there is NO FVG on that same 3-candle window (candle 3 overlapped candle 1)
    fvgs = detect_fvgs(candles, disps, TrendDirection.BULLISH, pd_cfg())
    assert not [f for f in fvgs.zones if f.type is PdArrayType.FVG and f.created_at == candles[-1].open_time]


def test_imr_bearish_mirror() -> None:
    candles = ohlc_candles(mirror([*BASE, *_BULL]))
    disps = detect_displacements(candles, pd_cfg())
    result = detect_imrs(candles, disps, TrendDirection.BEARISH, pd_cfg())

    imrs = [z for z in result.zones if z.type is PdArrayType.IMR]
    assert len(imrs) == 1
    assert imrs[0].direction is Direction.BEARISH


def test_fvg_gap_is_not_imr() -> None:
    # Same displacement but candle 3 leaves a gap (low 11.0 > a.high 10.5): a real FVG, never an IMR.
    rows = [*BASE, (10.0, 10.5, 9.5, 10.0), (10.0, 13.2, 10.0, 13.0), (13.0, 13.5, 11.0, 13.2)]
    candles = ohlc_candles(rows)
    disps = detect_displacements(candles, pd_cfg())
    assert not detect_imrs(candles, disps, TrendDirection.BULLISH, pd_cfg()).zones
    # sanity: the FVG engine does see a gap here
    assert [f for f in detect_fvgs(candles, disps, TrendDirection.BULLISH, pd_cfg()).zones if f.type is PdArrayType.FVG]


def test_no_displacement_no_imr() -> None:
    # The overlap geometry holds but there is no displacement -> the expansion gate rejects it.
    rows = [*BASE, (10.0, 10.5, 9.5, 10.0), (10.0, 10.6, 9.9, 10.3), (10.6, 10.7, 10.4, 10.6)]
    candles = ohlc_candles(rows)
    disps = detect_displacements(candles, pd_cfg())
    assert not detect_imrs(candles, disps, TrendDirection.BULLISH, pd_cfg()).zones
