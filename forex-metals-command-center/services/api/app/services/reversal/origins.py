"""Build reversal origin zones from HTF No-Wick and IMR analyses (reversal spec section 6).

Origins come from D1/H4/H1 (weighted D1 > H4 > H1). The reversal direction is the origin candle's body
direction; the rebalance zone is its body; the far edge is the origin-side extreme (a close beyond it
invalidates the setup).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domain.enums import Direction, PdArrayType, ReversalOriginKind, Timeframe
from app.services.no_wick.models import NoWickZone
from app.services.pd_arrays.models import PdArrayZone
from app.services.reversal.models import OriginZone, ReversalConfig


def _no_wick_origin(z: NoWickZone, tf: Timeframe, weight: float) -> OriginZone:
    body_top = max(z.open_level, z.close_level)
    body_bottom = min(z.open_level, z.close_level)
    return OriginZone(
        id=f"NWORIGIN:{tf.value}:{z.id}",
        kind=ReversalOriginKind.NO_WICK,
        timeframe=tf,
        direction=z.direction,
        body_top=body_top,
        body_bottom=body_bottom,
        far_edge=z.origin_extreme,  # candle low (bullish) / high (bearish)
        weight=weight,
        known_at=z.known_at,
    )


def _imr_origin(z: PdArrayZone, tf: Timeframe, weight: float) -> OriginZone:
    far_edge = z.bottom if z.direction is Direction.BULLISH else z.top  # origin-side edge
    return OriginZone(
        id=f"IMRORIGIN:{tf.value}:{z.id}",
        kind=ReversalOriginKind.IMR,
        timeframe=tf,
        direction=z.direction,
        body_top=z.top,
        body_bottom=z.bottom,
        far_edge=far_edge,
        weight=weight,
        known_at=z.known_at,
    )


def build_origins(
    no_wick_zones: Mapping[Timeframe, Sequence[NoWickZone]],
    imr_zones: Mapping[Timeframe, Sequence[PdArrayZone]],
    cfg: ReversalConfig,
) -> list[OriginZone]:
    """Active no-wick + IMR zones on the configured origin timeframes, as reversal origins."""
    origins: list[OriginZone] = []
    for tf in cfg.origin_timeframes:
        weight = cfg.origin_weights.get(tf.value, 0.0)
        for z in no_wick_zones.get(tf, ()):
            if z.active:
                origins.append(_no_wick_origin(z, tf, weight))
        for z in imr_zones.get(tf, ()):
            if z.active and z.type is PdArrayType.IMR:
                origins.append(_imr_origin(z, tf, weight))
    return origins
