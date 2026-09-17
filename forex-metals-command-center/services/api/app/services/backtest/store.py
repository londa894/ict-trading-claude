"""Backtest run persistence. Status/progress are mutable while running; a completed result is hashed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Engine, delete, inspect, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import BacktestRunDbRow
from app.services.journal.store import JournalUnavailableError


class BacktestUnavailableError(JournalUnavailableError):
    pass


@dataclass(frozen=True)
class StoredRun:
    id: str
    created_at: datetime
    symbol: str
    status: str
    request: dict[str, Any]
    progress: dict[str, Any]
    result: dict[str, Any] | None
    result_hash: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    strategy_version: str


class BacktestStore(Protocol):
    backend: str

    def check(self) -> str | None: ...

    def insert(self, run: StoredRun) -> None: ...

    def get(self, run_id: str) -> StoredRun | None: ...

    def recent(self, limit: int) -> list[StoredRun]: ...

    def update(self, run_id: str, **values: object) -> bool: ...

    def delete(self, run_id: str) -> bool: ...


class UnconfiguredBacktestStore:
    backend = "unconfigured"
    REASON = "no backtest store is configured (BACKTEST_STORE): nothing is run or saved"

    def check(self) -> str | None:
        return self.REASON

    def _fail(self) -> BacktestUnavailableError:
        return BacktestUnavailableError(self.REASON)

    def insert(self, run: StoredRun) -> None:
        raise self._fail()

    def get(self, run_id: str) -> StoredRun | None:
        raise self._fail()

    def recent(self, limit: int) -> list[StoredRun]:
        raise self._fail()

    def update(self, run_id: str, **values: object) -> bool:
        raise self._fail()

    def delete(self, run_id: str) -> bool:
        raise self._fail()


class DatabaseBacktestStore:
    TABLES = ("backtest_runs",)

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
            return f"backtest database unreachable ({type(exc).__name__})"
        if "backtest_runs" not in existing:
            return "backtest tables missing (backtest_runs): run `alembic upgrade head`"
        self._ready = True
        return None

    def _session(self) -> Session:
        reason = self.check()
        if reason:
            raise BacktestUnavailableError(reason)
        return Session(self._engine, expire_on_commit=False)

    @staticmethod
    def _run(r: BacktestRunDbRow) -> StoredRun:
        return StoredRun(
            r.id,
            r.created_at,
            r.symbol,
            r.status,
            r.request,
            r.progress,
            r.result,
            r.result_hash,
            r.error,
            r.started_at,
            r.finished_at,
            r.strategy_version,
        )

    def insert(self, run: StoredRun) -> None:
        with self._session() as s, s.begin():
            s.add(
                BacktestRunDbRow(
                    id=run.id,
                    created_at=run.created_at,
                    symbol=run.symbol,
                    status=run.status,
                    request=run.request,
                    progress=run.progress,
                    result=run.result,
                    result_hash=run.result_hash,
                    error=run.error,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    strategy_version=run.strategy_version,
                )
            )

    def get(self, run_id: str) -> StoredRun | None:
        with self._session() as s:
            row = s.get(BacktestRunDbRow, run_id)
            return self._run(row) if row else None

    def recent(self, limit: int) -> list[StoredRun]:
        q = select(BacktestRunDbRow).order_by(BacktestRunDbRow.created_at.desc()).limit(limit)
        with self._session() as s:
            return [self._run(r) for r in s.scalars(q)]

    def update(self, run_id: str, **values: object) -> bool:
        """Never rewrites a completed result (guarded in SQL)."""
        with self._session() as s, s.begin():
            changed = s.execute(
                update(BacktestRunDbRow)
                .where(BacktestRunDbRow.id == run_id, BacktestRunDbRow.result_hash.is_(None))
                .values(**values)
            )
            return bool(getattr(changed, "rowcount", 0))

    def delete(self, run_id: str) -> bool:
        with self._session() as s, s.begin():
            deleted = s.execute(delete(BacktestRunDbRow).where(BacktestRunDbRow.id == run_id))
            return bool(getattr(deleted, "rowcount", 0))
