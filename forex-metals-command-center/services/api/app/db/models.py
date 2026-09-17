"""Phase 0 persistence schema: instruments, candles, data-quality events.

Later phases add their own tables via new Alembic revisions (sessions, events, setups, journal ...).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Rejects naive datetimes on write and always returns aware UTC datetimes on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be persisted")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class InstrumentRow(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    base: Mapped[str] = mapped_column(String(8))
    quote: Mapped[str] = mapped_column(String(8))
    price_precision: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer)
    deeply_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    spec: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class CandleRow(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", "source", name="uq_candle_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), index=True)
    timeframe: Mapped[str] = mapped_column(String(4))
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    close_time: Mapped[datetime] = mapped_column(UTCDateTime())
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    is_closed: Mapped[bool] = mapped_column(Boolean)
    data_quality: Mapped[str] = mapped_column(String(16))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DataQualityEventRow(Base):
    __tablename__ = "data_quality_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str | None] = mapped_column(String(4), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(String(512))
    at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


# --- Phase 15: journal (private user records; the pre-trade record is never updated) ------------------------


class JournalEntryDbRow(Base):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    record: Mapped[dict[str, object]] = mapped_column(JSON)  # fill, notes, snapshot, detected violations
    record_hash: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))


class JournalOutcomeDbRow(Base):
    __tablename__ = "journal_outcomes"
    __table_args__ = (UniqueConstraint("entry_id", "revision", name="uq_journal_outcome_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    record: Mapped[dict[str, object]] = mapped_column(JSON)
    record_hash: Mapped[str] = mapped_column(String(64))


# --- Phase 16: paper trading (simulation records; the creation record and events are never updated) --------


class PaperSimDbRow(Base):
    __tablename__ = "paper_sims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    record: Mapped[dict[str, object]] = mapped_column(JSON)  # params, costs, snapshot, detected violations
    record_hash: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)  # progress marker (mutable)
    progress: Mapped[dict[str, object]] = mapped_column(JSON)  # simulation state (mutable, not hashed)


class PaperEventDbRow(Base):
    __tablename__ = "paper_events"
    __table_args__ = (UniqueConstraint("sim_id", "seq", name="uq_paper_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[str] = mapped_column(ForeignKey("paper_sims.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    record: Mapped[dict[str, object]] = mapped_column(JSON)
    record_hash: Mapped[str] = mapped_column(String(64))


# --- Phase 18: backtesting (research runs; a completed result is hashed and never updated) ------------------


class BacktestRunDbRow(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    request: Mapped[dict[str, object]] = mapped_column(JSON)
    progress: Mapped[dict[str, object]] = mapped_column(JSON)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(32))
