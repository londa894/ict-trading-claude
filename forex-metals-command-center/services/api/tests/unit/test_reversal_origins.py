"""Reversal origin sourcing from HTF No-Wick + IMR zones (Phase D4b)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import (
    Direction,
    NoWickZoneState,
    PdArrayState,
    PdArrayType,
    ReversalOriginKind,
    ScoreComponentStatus,
    Timeframe,
)
from app.services.no_wick.models import NoWickZone
from app.services.pd_arrays.models import PdArrayZone
from app.services.reversal.models import ReversalConfig
from app.services.reversal.origins import build_origins

CFG = ReversalConfig.from_spec()
T = datetime(2024, 1, 9, tzinfo=UTC)


def _nw_zone(direction, open_level, close_level, origin_extreme, active=True):
    return NoWickZone(
        id="NWZ:x", event_id="e", direction=direction, close_level=close_level,
        level_25=0.0, level_50=0.0, level_75=0.0, open_level=open_level, origin_extreme=origin_extreme,
        fvg_overlap_ids=[], ob_overlap=ScoreComponentStatus.NOT_EVALUATED, state=NoWickZoneState.FRESH,
        rebalance_pct=0.0, created_at=T, known_at=T, state_changed_at=None, age_bars=0, active=active,
        relevance_score=80.0,
    )


def _imr_zone(direction, top, bottom, active=True):
    return PdArrayZone(
        id="IMR:x", type=PdArrayType.IMR, direction=direction, top=top, bottom=bottom,
        midpoint=(top + bottom) / 2, size_atr=1.0, source_times=[T], created_at=T, known_at=T,
        state=PdArrayState.FRESH, fill_pct=0.0, ifvg_status=None, parent_id=None, displacement_grade=None,
        state_changed_at=None, invalidated_at=None, age_bars=0, active=active, quality_score=50.0,
    )


def test_no_wick_origin_geometry_bearish():
    z = _nw_zone(Direction.BEARISH, open_level=110.0, close_level=105.0, origin_extreme=111.0)
    origins = build_origins({Timeframe.H1: [z]}, {}, CFG)
    assert len(origins) == 1
    o = origins[0]
    assert o.kind is ReversalOriginKind.NO_WICK and o.direction is Direction.BEARISH
    assert (o.body_top, o.body_bottom, o.far_edge) == (110.0, 105.0, 111.0)
    assert o.timeframe is Timeframe.H1 and o.weight == CFG.origin_weights["H1"]


def test_imr_origin_geometry_bullish():
    z = _imr_zone(Direction.BULLISH, top=13.2, bottom=10.5)
    origins = build_origins({}, {Timeframe.D1: [z]}, CFG)
    assert len(origins) == 1
    o = origins[0]
    assert o.kind is ReversalOriginKind.IMR and o.direction is Direction.BULLISH
    assert (o.body_top, o.body_bottom, o.far_edge) == (13.2, 10.5, 10.5)  # bullish far edge = bottom
    assert o.weight == 1.0  # D1 weighted highest


def test_inactive_zones_and_non_origin_timeframes_excluded():
    active = _nw_zone(Direction.BEARISH, 110.0, 105.0, 111.0)
    inactive = _nw_zone(Direction.BEARISH, 110.0, 105.0, 111.0, active=False)
    # M15 is not an origin timeframe; inactive zones are skipped.
    origins = build_origins({Timeframe.H1: [active, inactive], Timeframe.M15: [active]}, {}, CFG)
    assert len(origins) == 1 and origins[0].timeframe is Timeframe.H1


def test_weights_rank_d1_over_h4_over_h1():
    z = _nw_zone(Direction.BEARISH, 110.0, 105.0, 111.0)
    origins = build_origins({tf: [z] for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1)}, {}, CFG)
    weights = {o.timeframe: o.weight for o in origins}
    assert weights[Timeframe.D1] > weights[Timeframe.H4] > weights[Timeframe.H1]
