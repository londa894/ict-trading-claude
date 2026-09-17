"""Builders for liquidity scenarios (synthetic test prices, not market facts)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.candle import Candle, RawBar
from app.domain.enums import AssetClass, StructureLevel, Timeframe, TrendDirection
from app.services.candles.normalize import build_series
from app.services.liquidity.engine import LiquidityResult, analyze_liquidity
from app.services.liquidity.models import KeyLevel, LiquidityConfig
from app.services.structure.engine import analyze_level
from app.services.structure.models import StructureConfig
from tests.structure_helpers import cfg as structure_cfg


def liq_cfg(**overrides: object) -> LiquidityConfig:
    """Spec defaults with test overrides (dataclass field names)."""
    return replace(LiquidityConfig.from_spec(), **overrides)  # type: ignore[arg-type]


def run(
    candles: list[Candle],
    *,
    pivot: int = 1,
    external_pivot: int | None = None,
    key_levels: list[KeyLevel] | None = None,
    trend: TrendDirection = TrendDirection.NONE,
    cfg: LiquidityConfig | None = None,
) -> LiquidityResult:
    s_cfg: StructureConfig = structure_cfg(pivot=pivot)
    if external_pivot is not None:
        s_cfg = replace(
            s_cfg, pivot_length={StructureLevel.INTERNAL: pivot, StructureLevel.EXTERNAL: external_pivot}
        )
    internal = analyze_level(candles, StructureLevel.INTERNAL, s_cfg)
    external = analyze_level(candles, StructureLevel.EXTERNAL, s_cfg)
    return analyze_liquidity(candles, internal, external, key_levels or [], trend, cfg or liq_cfg())


def pool_events(result: LiquidityResult, pool_id: str) -> list[tuple[int, str]]:
    return [(e.time, e.type.value) for e in result.events if e.pool_id == pool_id]  # type: ignore[misc]


def mirror(rows: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Reflect (high, low, close) rows around 20 so highs become lows (BSL scenario -> SSL scenario)."""
    return [(20 - low, 20 - h, 20 - c) for h, low, c in rows]


def d1_candles(days: list[tuple[datetime, float, float]], now: datetime | None = None) -> list[Candle]:
    """Closed D1 candles. `days` = (New York close date as UTC midnight, high, low). EST window (UTC-5)."""
    raw = []
    for close_date, high, low in days:
        close = close_date.replace(hour=22, minute=0, tzinfo=UTC)  # 17:00 New York (EST)
        open_time = close - timedelta(days=1)
        mid = (high + low) / 2
        raw.append(
            RawBar(
                symbol="XAUUSD",
                timeframe=Timeframe.D1,
                open_time=open_time,
                open=mid,
                high=high,
                low=low,
                close=mid,
                volume=1.0,
            )
        )
    last_close = raw[-1].open_time + timedelta(days=1)
    series = build_series(
        raw,
        symbol="XAUUSD",
        timeframe=Timeframe.D1,
        source="t",
        asset_class=AssetClass.METAL,
        now=now or last_close + timedelta(seconds=1),
    )
    assert not [i for i in series.issues if i.severity == "ERROR"], series.issues
    return series.candles
