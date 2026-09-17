"""Paper persistence. Creation records and events are append-only; only the progress marker is updated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine, and_, delete, func, inspect, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import PaperEventDbRow, PaperSimDbRow
from app.services.journal.store import JournalUnavailableError, decode_cursor, encode_cursor


class PaperUnavailableError(JournalUnavailableError):
    pass


@dataclass(frozen=True)
class StoredSim:
    id: str
    created_at: datetime
    symbol: str
    record: dict[str, object]
    record_hash: str
    strategy_version: str
    status: str
    progress: dict[str, object]


@dataclass(frozen=True)
class StoredEvent:
    sim_id: str
    seq: int
    record: dict[str, object]
    record_hash: str


class PaperStore(Protocol):
    backend: str

    def check(self) -> str | None: ...

    def insert_sim(self, sim: StoredSim, events: list[StoredEvent]) -> None: ...

    def get_sim(self, sim_id: str) -> StoredSim | None: ...

    def list_sims(
        self, *, symbol: str | None, status: str | None, limit: int, cursor: str | None
    ) -> tuple[list[StoredSim], str | None]: ...

    def active_ids(self, limit: int) -> list[str]: ...

    def events(self, sim_ids: list[str]) -> dict[str, list[StoredEvent]]: ...

    def advance(
        self,
        sim_id: str,
        expected_status: str,
        status: str,
        progress: dict[str, object],
        events: list[StoredEvent],
    ) -> bool: ...

    def delete_sim(self, sim_id: str) -> bool: ...


class UnconfiguredPaperStore:
    backend = "unconfigured"
    REASON = "no paper store is configured (PAPER_STORE): nothing is simulated or saved"

    def check(self) -> str | None:
        return self.REASON

    def _fail(self) -> PaperUnavailableError:
        return PaperUnavailableError(self.REASON)

    def insert_sim(self, sim: StoredSim, events: list[StoredEvent]) -> None:
        raise self._fail()

    def get_sim(self, sim_id: str) -> StoredSim | None:
        raise self._fail()

    def list_sims(
        self, *, symbol: str | None, status: str | None, limit: int, cursor: str | None
    ) -> tuple[list[StoredSim], str | None]:
        raise self._fail()

    def active_ids(self, limit: int) -> list[str]:
        return []

    def events(self, sim_ids: list[str]) -> dict[str, list[StoredEvent]]:
        raise self._fail()

    def advance(
        self,
        sim_id: str,
        expected_status: str,
        status: str,
        progress: dict[str, object],
        events: list[StoredEvent],
    ) -> bool:
        raise self._fail()

    def delete_sim(self, sim_id: str) -> bool:
        raise self._fail()


class DatabasePaperStore:
    TABLES = ("paper_sims", "paper_events")

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ready = False
        self.backend = f"database:{engine.dialect.name}"

    def check(self) -> str | None:
        if self._ready:
            return None
        try:
            existing = set(inspect(self._engine).get_table_names())
        except SQLAlchemyError as exc:
            return f"paper database unreachable ({type(exc).__name__})"
        missing = [t for t in self.TABLES if t not in existing]
        if missing:
            return f"paper tables missing ({', '.join(missing)}): run `alembic upgrade head`"
        self._ready = True
        return None

    def _session(self) -> Session:
        reason = self.check()
        if reason:
            raise PaperUnavailableError(reason)
        return Session(self._engine, expire_on_commit=False)

    @staticmethod
    def _sim(r: PaperSimDbRow) -> StoredSim:
        return StoredSim(
            r.id, r.created_at, r.symbol, r.record, r.record_hash, r.strategy_version, r.status, r.progress
        )

    @staticmethod
    def _event_row(e: StoredEvent) -> PaperEventDbRow:
        return PaperEventDbRow(sim_id=e.sim_id, seq=e.seq, record=e.record, record_hash=e.record_hash)

    def insert_sim(self, sim: StoredSim, events: list[StoredEvent]) -> None:
        with self._session() as s, s.begin():
            s.add(
                PaperSimDbRow(
                    id=sim.id,
                    created_at=sim.created_at,
                    symbol=sim.symbol,
                    record=sim.record,
                    record_hash=sim.record_hash,
                    strategy_version=sim.strategy_version,
                    status=sim.status,
                    progress=sim.progress,
                )
            )
            s.flush()
            s.add_all([self._event_row(e) for e in events])

    def get_sim(self, sim_id: str) -> StoredSim | None:
        with self._session() as s:
            row = s.get(PaperSimDbRow, sim_id)
            return self._sim(row) if row else None

    def list_sims(
        self, *, symbol: str | None, status: str | None, limit: int, cursor: str | None
    ) -> tuple[list[StoredSim], str | None]:
        q = select(PaperSimDbRow)
        if symbol:
            q = q.where(PaperSimDbRow.symbol == symbol)
        if status:
            q = q.where(PaperSimDbRow.status == status)
        if cursor:
            at, last_id = decode_cursor(cursor)
            q = q.where(
                or_(
                    PaperSimDbRow.created_at < at,
                    and_(PaperSimDbRow.created_at == at, PaperSimDbRow.id < last_id),
                )
            )
        q = q.order_by(PaperSimDbRow.created_at.desc(), PaperSimDbRow.id.desc()).limit(limit + 1)
        with self._session() as s:
            rows = [self._sim(r) for r in s.scalars(q)]
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None

    def active_ids(self, limit: int) -> list[str]:
        if self.check():
            return []
        q = (
            select(PaperSimDbRow.id)
            .where(PaperSimDbRow.status.in_(("PENDING", "OPEN")))
            .order_by(PaperSimDbRow.created_at)
            .limit(limit)
        )
        with self._session() as s:
            return list(s.scalars(q))

    def events(self, sim_ids: list[str]) -> dict[str, list[StoredEvent]]:
        out: dict[str, list[StoredEvent]] = {i: [] for i in sim_ids}
        if not sim_ids:
            return out
        q = (
            select(PaperEventDbRow)
            .where(PaperEventDbRow.sim_id.in_(sim_ids))
            .order_by(PaperEventDbRow.sim_id, PaperEventDbRow.seq)
        )
        with self._session() as s:
            for r in s.scalars(q):
                out[r.sim_id].append(StoredEvent(r.sim_id, r.seq, r.record, r.record_hash))
        return out

    def advance(
        self,
        sim_id: str,
        expected_status: str,
        status: str,
        progress: dict[str, object],
        events: list[StoredEvent],
    ) -> bool:
        """Compare-and-set on the status so concurrent advances never double-append events."""
        with self._session() as s, s.begin():
            current = s.scalar(
                select(func.count()).select_from(PaperEventDbRow).where(PaperEventDbRow.sim_id == sim_id)
            )
            if events and events[0].seq != int(current or 0) + 1:
                return False
            changed = s.execute(
                update(PaperSimDbRow)
                .where(PaperSimDbRow.id == sim_id, PaperSimDbRow.status == expected_status)
                .values(status=status, progress=progress)
            )
            if not getattr(changed, "rowcount", 0):
                return False
            s.add_all([self._event_row(e) for e in events])
        return True

    def delete_sim(self, sim_id: str) -> bool:
        with self._session() as s, s.begin():
            s.execute(delete(PaperEventDbRow).where(PaperEventDbRow.sim_id == sim_id))
            deleted = s.execute(delete(PaperSimDbRow).where(PaperSimDbRow.id == sim_id))
            return bool(getattr(deleted, "rowcount", 0))
