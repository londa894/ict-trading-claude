"""Journal persistence behind one interface. `unconfigured` stores nothing and never pretends to save."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine, and_, delete, func, inspect, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import JournalEntryDbRow, JournalOutcomeDbRow
from app.services.journal.models import JournalStoreInfo

Hasher = Callable[[str, int, datetime, dict[str, object]], str]


class JournalUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredEntry:
    id: str
    created_at: datetime
    symbol: str
    kind: str
    record: dict[str, object]
    record_hash: str
    strategy_version: str


@dataclass(frozen=True)
class StoredOutcome:
    entry_id: str
    revision: int
    recorded_at: datetime
    record: dict[str, object]
    record_hash: str


class JournalStore(Protocol):
    def info(self) -> JournalStoreInfo: ...

    def insert_entry(self, entry: StoredEntry) -> None: ...

    def get_entry(self, entry_id: str) -> StoredEntry | None: ...

    def list_entries(
        self,
        *,
        symbol: str | None,
        kind: str | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[StoredEntry], str | None]: ...

    def outcomes(self, entry_ids: list[str]) -> dict[str, list[StoredOutcome]]: ...

    def insert_outcome(
        self, entry_id: str, recorded_at: datetime, record: dict[str, object], hasher: Hasher
    ) -> int: ...

    def delete_entry(self, entry_id: str) -> bool: ...


class UnconfiguredJournalStore:
    def info(self) -> JournalStoreInfo:
        return JournalStoreInfo(
            available=False,
            backend="unconfigured",
            reason="no journal store is configured (JOURNAL_STORE): nothing is saved",
        )

    def _fail(self) -> JournalUnavailableError:
        return JournalUnavailableError(self.info().reason or "journal unavailable")

    def insert_entry(self, entry: StoredEntry) -> None:
        raise self._fail()

    def get_entry(self, entry_id: str) -> StoredEntry | None:
        raise self._fail()

    def list_entries(self, **_: object) -> tuple[list[StoredEntry], str | None]:
        raise self._fail()

    def outcomes(self, entry_ids: list[str]) -> dict[str, list[StoredOutcome]]:
        raise self._fail()

    def insert_outcome(
        self, entry_id: str, recorded_at: datetime, record: dict[str, object], hasher: Hasher
    ) -> int:
        raise self._fail()

    def delete_entry(self, entry_id: str) -> bool:
        raise self._fail()


def encode_cursor(created_at: datetime, entry_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{entry_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, entry_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(stamp), entry_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


class DatabaseJournalStore:
    """SQLAlchemy store (Postgres or SQLite). Tables come from Alembic; missing tables = unavailable."""

    TABLES = ("journal_entries", "journal_outcomes")

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ready = False

    def info(self) -> JournalStoreInfo:
        backend = f"database:{self._engine.dialect.name}"
        try:
            self._check()
        except JournalUnavailableError as exc:
            return JournalStoreInfo(available=False, backend=backend, reason=str(exc))
        return JournalStoreInfo(available=True, backend=backend, reason=None)

    def _check(self) -> None:
        if self._ready:
            return
        try:
            existing = set(inspect(self._engine).get_table_names())
        except SQLAlchemyError as exc:
            raise JournalUnavailableError(f"journal database unreachable ({type(exc).__name__})") from exc
        missing = [t for t in self.TABLES if t not in existing]
        if missing:
            raise JournalUnavailableError(
                f"journal tables missing ({', '.join(missing)}): run `alembic upgrade head`"
            )
        self._ready = True

    def _session(self) -> Session:
        self._check()
        return Session(self._engine, expire_on_commit=False)

    @staticmethod
    def _entry(row: JournalEntryDbRow) -> StoredEntry:
        return StoredEntry(
            row.id, row.created_at, row.symbol, row.kind, row.record, row.record_hash, row.strategy_version
        )

    def insert_entry(self, entry: StoredEntry) -> None:
        with self._session() as s, s.begin():
            s.add(
                JournalEntryDbRow(
                    id=entry.id,
                    created_at=entry.created_at,
                    symbol=entry.symbol,
                    kind=entry.kind,
                    record=entry.record,
                    record_hash=entry.record_hash,
                    strategy_version=entry.strategy_version,
                )
            )

    def get_entry(self, entry_id: str) -> StoredEntry | None:
        with self._session() as s:
            row = s.get(JournalEntryDbRow, entry_id)
            return self._entry(row) if row else None

    def list_entries(
        self,
        *,
        symbol: str | None,
        kind: str | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[StoredEntry], str | None]:
        q = select(JournalEntryDbRow)
        if symbol:
            q = q.where(JournalEntryDbRow.symbol == symbol)
        if kind:
            q = q.where(JournalEntryDbRow.kind == kind)
        if start:
            q = q.where(JournalEntryDbRow.created_at >= start)
        if end:
            q = q.where(JournalEntryDbRow.created_at < end)
        if cursor:
            at, last_id = decode_cursor(cursor)
            q = q.where(
                or_(
                    JournalEntryDbRow.created_at < at,
                    and_(JournalEntryDbRow.created_at == at, JournalEntryDbRow.id < last_id),
                )
            )
        q = q.order_by(JournalEntryDbRow.created_at.desc(), JournalEntryDbRow.id.desc()).limit(limit + 1)
        with self._session() as s:
            rows = [self._entry(r) for r in s.scalars(q)]
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None

    def outcomes(self, entry_ids: list[str]) -> dict[str, list[StoredOutcome]]:
        out: dict[str, list[StoredOutcome]] = {i: [] for i in entry_ids}
        if not entry_ids:
            return out
        q = (
            select(JournalOutcomeDbRow)
            .where(JournalOutcomeDbRow.entry_id.in_(entry_ids))
            .order_by(JournalOutcomeDbRow.entry_id, JournalOutcomeDbRow.revision)
        )
        with self._session() as s:
            for r in s.scalars(q):
                out[r.entry_id].append(
                    StoredOutcome(r.entry_id, r.revision, r.recorded_at, r.record, r.record_hash)
                )
        return out

    def insert_outcome(
        self, entry_id: str, recorded_at: datetime, record: dict[str, object], hasher: Hasher
    ) -> int:
        """Append the next revision. `hasher(entry_id, revision, recorded_at, record)` returns its hash."""
        with self._session() as s, s.begin():
            current = s.scalar(
                select(func.max(JournalOutcomeDbRow.revision)).where(JournalOutcomeDbRow.entry_id == entry_id)
            )
            revision = int(current or 0) + 1
            s.add(
                JournalOutcomeDbRow(
                    entry_id=entry_id,
                    revision=revision,
                    recorded_at=recorded_at,
                    record=record,
                    record_hash=hasher(entry_id, revision, recorded_at, record),
                )
            )
        return revision

    def delete_entry(self, entry_id: str) -> bool:
        with self._session() as s, s.begin():
            s.execute(delete(JournalOutcomeDbRow).where(JournalOutcomeDbRow.entry_id == entry_id))
            deleted = s.execute(delete(JournalEntryDbRow).where(JournalEntryDbRow.id == entry_id))
            return bool(getattr(deleted, "rowcount", 0))
