"""Candle persistence with integrity rules.

- New candle identity (symbol, timeframe, open_time, source) -> insert.
- Existing row still forming (is_closed=False) -> update with the newer snapshot.
- Existing CLOSED row with identical prices -> no-op.
- Existing CLOSED row with different prices -> keep the stored row, record DUPLICATE_CONFLICT event.
  Closed history is never silently rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CandleRow, DataQualityEventRow, InstrumentRow
from app.domain.candle import Candle
from app.domain.enums import IssueSeverity, ValidationIssueCode
from app.domain.instrument import Instrument


@dataclass
class UpsertReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0


def sync_instruments(session: Session, instruments: Sequence[Instrument]) -> None:
    for inst in instruments:
        row = session.get(InstrumentRow, inst.symbol) or InstrumentRow(symbol=inst.symbol)
        row.asset_class = inst.asset_class.value
        row.base = inst.base
        row.quote = inst.quote
        row.price_precision = inst.price_precision
        row.priority = inst.priority
        row.deeply_validated = inst.deeply_validated
        row.spec = inst.spec.model_dump(mode="json") if inst.spec else None
        session.add(row)
    session.flush()


def _to_row(c: Candle, now: datetime) -> CandleRow:
    return CandleRow(
        symbol=c.symbol,
        timeframe=c.timeframe.value,
        open_time=c.open_time,
        close_time=c.close_time,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close,
        volume=c.volume,
        source=c.source,
        is_closed=c.is_closed,
        data_quality=c.data_quality.value,
        ingested_at=now,
    )


def upsert_candles(session: Session, candles: Sequence[Candle], now: datetime) -> UpsertReport:
    report = UpsertReport()
    for c in candles:
        existing = session.scalars(
            select(CandleRow).where(
                CandleRow.symbol == c.symbol,
                CandleRow.timeframe == c.timeframe.value,
                CandleRow.open_time == c.open_time,
                CandleRow.source == c.source,
            )
        ).one_or_none()
        if existing is None:
            session.add(_to_row(c, now))
            report.inserted += 1
            continue
        same = (existing.open, existing.high, existing.low, existing.close, existing.volume) == (
            c.open,
            c.high,
            c.low,
            c.close,
            c.volume,
        )
        if not existing.is_closed:
            for field in ("open", "high", "low", "close", "volume", "is_closed"):
                setattr(existing, field, getattr(c, field))
            existing.data_quality = c.data_quality.value
            existing.ingested_at = now
            report.updated += 1
        elif same:
            report.unchanged += 1
        else:
            session.add(
                DataQualityEventRow(
                    symbol=c.symbol,
                    timeframe=c.timeframe.value,
                    source=c.source,
                    code=ValidationIssueCode.DUPLICATE_CONFLICT.value,
                    severity=IssueSeverity.ERROR.value,
                    message="closed candle revision rejected; stored history kept",
                    at=c.open_time,
                    recorded_at=now,
                )
            )
            report.conflicts += 1
    session.flush()
    return report


def load_candles(session: Session, symbol: str, timeframe: str, source: str) -> list[CandleRow]:
    return list(
        session.scalars(
            select(CandleRow)
            .where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe, CandleRow.source == source)
            .order_by(CandleRow.open_time)
        )
    )
