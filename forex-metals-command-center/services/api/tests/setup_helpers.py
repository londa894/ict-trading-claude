"""Scenario builders for the setup state machine (synthetic prices and events, not market facts).

The engine only reads a few attributes of upstream facts, so the scenarios use SimpleNamespace stand-ins and a
flat M15 base (true range 1.0 -> ATR 1.0). `flip=True` mirrors prices around 2030 and swaps sides/directions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.domain.candle import Candle
from app.domain.enums import (
    Direction,
    LiquidityEventType,
    LiquidityPoolType,
    LiquiditySide,
    NoWickStrength,
    PdArrayEventType,
    PdArrayType,
    QualifierStatus,
    StructureEventStatus,
    StructureEventType,
    StructureLevel,
    Timeframe,
)
from app.services.entry.models import EntryConfig
from app.services.sessions.clock import trading_day_of
from app.services.setup_state.engine import SetupInputs, SetupResult, analyze_setups
from app.services.setup_state.models import BiasPoint, SetupConfig
from tests.session_helpers import FLAT, candle, slots, utc

START = utc(2024, 1, 9, 7, 0)  # Tuesday 02:00 EST
Row = tuple[float, float, float, float]


def mirror_price(p: float) -> float:
    return round(4060.0 - p, 6)


@dataclass
class Scenario:
    """Bullish by default. Rows/events are keyed by candle index."""

    rows: dict[int, Row] = field(default_factory=dict)
    count: int = 40
    start: datetime = START
    target_price: float = 2040.0
    opposite_price: float = 2029.2
    sweeps: dict[int, float] = field(default_factory=lambda: {20: 2028.9})  # index -> extreme
    target_taken_at: int | None = None
    breaks: dict[int, QualifierStatus] = field(default_factory=lambda: {24: QualifierStatus.PRESENT})
    zones: dict[int, tuple[float, float]] = field(
        default_factory=lambda: {25: (2030.5, 2031.5)}
    )  # created index
    zone_invalidated_at: int | None = None
    bias: list[tuple[Timeframe, Direction, int | None]] = field(
        default_factory=lambda: [
            (Timeframe.H4, Direction.BULLISH, None),
            (Timeframe.H1, Direction.BULLISH, None),
        ]
    )  # (tf, direction, known at candle index close; None = before the start)
    cfg: SetupConfig = field(default_factory=SetupConfig.from_spec)
    entry_cfg: EntryConfig = field(default_factory=EntryConfig.from_spec)
    zone_type: PdArrayType = PdArrayType.FVG
    no_wick: dict[int, NoWickStrength] = field(default_factory=dict)  # index -> strength (setup direction)
    ltf: list[tuple[int, int, float]] = field(default_factory=list)  # (M15 index, minute offset, M5 close)
    further_targets: list[float] = field(default_factory=list)

    def run(self, flip: bool = False) -> tuple[list[Candle], SetupResult]:
        times = slots(self.start, self.start + timedelta(days=4))[: self.count]
        px = mirror_price if flip else (lambda p: p)

        def row(r: Row) -> Row:
            o, h, low, c = r
            return (px(o), px(low), px(h), px(c)) if flip else r

        candles = [candle(t, row(self.rows.get(i, FLAT))) for i, t in enumerate(times)]
        bull, bear = (
            (Direction.BEARISH, Direction.BULLISH) if flip else (Direction.BULLISH, Direction.BEARISH)
        )
        before = self.start - timedelta(hours=2)
        target_side, opposite_side = (
            (LiquiditySide.SSL, LiquiditySide.BSL) if flip else (LiquiditySide.BSL, LiquiditySide.SSL)
        )
        pools = [
            SimpleNamespace(
                id="TARGET",
                type=LiquidityPoolType.PDH,
                side=target_side,
                label="PDH",
                price=px(self.target_price),
                known_at=before,
            ),
            *[
                SimpleNamespace(
                    id=f"FURTHER:{k}",
                    type=LiquidityPoolType.PWH,
                    side=target_side,
                    label=f"PWH {k}",
                    price=px(price),
                    known_at=before,
                )
                for k, price in enumerate(self.further_targets)
            ],
            SimpleNamespace(
                id="OPPOSITE",
                type=LiquidityPoolType.ASIA_LOW,
                side=opposite_side,
                label="Asia low",
                price=px(self.opposite_price),
                known_at=before,
            ),
        ]
        liq = [
            SimpleNamespace(
                pool_id="OPPOSITE",
                pool_type=LiquidityPoolType.ASIA_LOW,
                side=opposite_side,
                type=LiquidityEventType.SWEEP,
                time=times[i],
                extreme=px(extreme),
            )
            for i, extreme in self.sweeps.items()
        ]
        if self.target_taken_at is not None:
            liq.append(
                SimpleNamespace(
                    pool_id="TARGET",
                    pool_type=LiquidityPoolType.PDH,
                    side=target_side,
                    type=LiquidityEventType.BREAK,
                    time=times[self.target_taken_at],
                    extreme=px(self.target_price + 1),
                )
            )
        structure = [
            SimpleNamespace(
                id=f"MSS:{i}",
                status=StructureEventStatus.CONFIRMED,
                type=StructureEventType.MSS,
                level=StructureLevel.INTERNAL,
                direction=bull,
                time=times[i],
                price=px(2031.0),
                displacement_qualifier=q,
            )
            for i, q in self.breaks.items()
        ]
        zones, pd_events = [], []
        for i, (bottom, top) in self.zones.items():
            lo, hi = sorted((px(bottom), px(top)))
            zid = f"FVG:{i}"
            zones.append(SimpleNamespace(id=zid, type=self.zone_type, direction=bull, bottom=lo, top=hi))
            kind = (
                PdArrayEventType.CREATED
                if self.zone_type is PdArrayType.FVG
                else PdArrayEventType.IFVG_CONFIRMED
            )
            pd_events.append(SimpleNamespace(zone_id=zid, type=kind, time=times[i]))
            if self.zone_invalidated_at is not None:
                pd_events.append(
                    SimpleNamespace(
                        zone_id=zid, type=PdArrayEventType.INVALIDATED, time=times[self.zone_invalidated_at]
                    )
                )
        points = [
            BiasPoint(
                timeframe=tf,
                direction=(bull if d is Direction.BULLISH else bear),
                known_at=before if at is None else times[at] + Timeframe.M15.duration,
                event_id=f"{tf.value}:{at}",
            )
            for tf, d, at in self.bias
        ]
        inputs = SetupInputs(
            structure_events=structure,  # type: ignore[arg-type]
            pools=pools,  # type: ignore[arg-type]
            liquidity_events=liq,  # type: ignore[arg-type]
            pd_zones=zones,  # type: ignore[arg-type]
            pd_events=pd_events,  # type: ignore[arg-type]
            bias_points=points,
            bias_timeframes=[Timeframe.H4, Timeframe.H1],
            no_wick_events=[  # type: ignore[arg-type]
                SimpleNamespace(time=times[i], direction=bull, strength=st) for i, st in self.no_wick.items()
            ],
            ltf_candles=[
                candle(times[i] + timedelta(minutes=m), row((close, close, close, close)), Timeframe.M5)
                for i, m, close in self.ltf
            ],
            ltf_breaks=[  # type: ignore[arg-type]
                SimpleNamespace(
                    time=times[i] + timedelta(minutes=m),
                    status=StructureEventStatus.CONFIRMED,
                    type=StructureEventType.CHOCH,
                    direction=bull,
                )
                for i, m, _ in self.ltf
            ],
        )
        return candles, analyze_setups(candles, inputs, self.cfg, trading_day_of, self.entry_cfg)

    def with_cfg(self, **overrides: object) -> Scenario:
        self.cfg = replace(self.cfg, **overrides)  # type: ignore[arg-type]
        return self


# Happy path: sweep @20, MSS @24, leg FVG @25, approach @27, touch @28, waiting @29 (unconfirmed).
HAPPY_ROWS: dict[int, Row] = {
    20: (2030.0, 2030.5, 2028.9, 2029.6),
    24: (2030.0, 2033.0, 2029.8, 2032.8),
    25: (2032.8, 2034.0, 2032.5, 2033.8),
    26: (2033.8, 2034.2, 2033.2, 2033.5),
    27: (2033.5, 2033.6, 2031.9, 2032.0),
    28: (2032.0, 2032.2, 2031.2, 2031.8),
    29: (2032.3, 2032.4, 2031.6, 2032.1),  # bearish: no 15M close confirmation
}


def happy(**kwargs: object) -> Scenario:
    rows = dict(HAPPY_ROWS)
    rows.update(kwargs.pop("rows", {}))  # type: ignore[call-overload]
    return Scenario(rows=rows, count=int(kwargs.pop("count", 30)), **kwargs)  # type: ignore[arg-type]


def _run_with_entry(self: Scenario, **entry_overrides: object) -> tuple[list[Candle], SetupResult]:
    self.entry_cfg = replace(self.entry_cfg, **entry_overrides)  # type: ignore[arg-type]
    return self.run()


Scenario.run_with_entry = _run_with_entry  # type: ignore[attr-defined]
