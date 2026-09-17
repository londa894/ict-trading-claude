"""Journal service: captures the engine snapshot, verifies integrity on every read, computes outcomes.

The journal records the user's own manual decisions and trades. It never places, sizes or authorizes anything.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.contracts import strategy_version
from app.domain.enums import (
    DataQuality,
    ExtremeSource,
    JournalEntryKind,
    JournalStatus,
    RuleViolation,
    SnapshotIntegrity,
    SnapshotTiming,
    TradeResult,
)
from app.domain.instrument import get_instrument
from app.services.candles.service import MAX_LIMIT, CandleService, UnknownSymbolError
from app.services.journal.capture import capture_engine_state
from app.services.journal.engine import (
    Extremes,
    OutcomeInputError,
    candle_extremes,
    canonical_hash,
    check_outcome,
    compute_outcome,
    detect_violations,
    snapshot_timing,
    summarize,
)
from app.services.journal.models import (
    CreateJournalEntryRequest,
    JournalConfig,
    JournalEntry,
    JournalEntryRow,
    JournalExport,
    JournalListResponse,
    JournalOutcome,
    JournalSnapshot,
    JournalStoreInfo,
    JournalTradeFill,
    RecordOutcomeRequest,
)
from app.services.journal.store import JournalStore, JournalUnavailableError, StoredEntry, StoredOutcome
from app.services.market_state.service import MarketStateService
from app.services.scoring.service import EvaluationService

logger = logging.getLogger("fmcc.journal")
_WITHHELD = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})


class JournalEntryNotFoundError(LookupError):
    pass


class JournalRequestError(ValueError):
    """A request that contradicts the stored record (safe message, no submitted values)."""


def entry_hash(
    entry_id: str, created_at: datetime, symbol: str, kind: str, record: dict[str, object], version: str
) -> str:
    return canonical_hash(
        {
            "id": entry_id,
            "createdAt": created_at.isoformat(),
            "symbol": symbol,
            "kind": kind,
            "record": record,
            "strategyVersion": version,
        }
    )


def outcome_hash(entry_id: str, revision: int, recorded_at: datetime, record: dict[str, object]) -> str:
    return canonical_hash(
        {"entryId": entry_id, "revision": revision, "recordedAt": recorded_at.isoformat(), "record": record}
    )


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


class JournalService:
    def __init__(
        self,
        store: JournalStore,
        market_state: MarketStateService,
        evaluation: EvaluationService,
        candles: CandleService,
        clock: Callable[[], datetime] | None = None,
        cfg: JournalConfig | None = None,
    ) -> None:
        self._store = store
        self._market = market_state
        self._evaluation = evaluation
        self._candles = candles
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or JournalConfig.from_spec()

    @property
    def cfg(self) -> JournalConfig:
        return self._cfg

    async def info(self) -> JournalStoreInfo:
        return await asyncio.to_thread(self._store.info)

    # --- create ----------------------------------------------------------------------

    async def create(self, req: CreateJournalEntryRequest) -> JournalEntry:
        instrument = get_instrument(req.symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(req.symbol)
        info = await self.info()
        if not info.available:
            raise JournalUnavailableError(info.reason or "journal unavailable")
        now = self._clock()
        fill = req.trade
        if fill is not None and fill.opened_at > now + timedelta(minutes=1):
            raise JournalRequestError("openedAt cannot be in the future")
        symbol = instrument.symbol
        state = await capture_engine_state(self._market, self._evaluation, symbol, now)
        decision, evaluation = state.decision, state.evaluation
        detected = detect_violations(fill, decision, evaluation, self._cfg) if fill is not None else []
        record: dict[str, object] = {
            "trade": fill.model_dump(mode="json", by_alias=True) if fill is not None else None,
            "notes": req.notes,
            "detectedViolations": [v.value for v in detected],
            "snapshot": {
                "capturedAt": now.isoformat(),
                "timing": snapshot_timing(req.kind, fill, now, self._cfg).value,
                "decision": decision,
                "evaluation": evaluation,
                "data": state.data,
            },
        }
        entry_id = str(uuid.uuid4())
        version = strategy_version()
        stored = StoredEntry(
            entry_id,
            now,
            symbol,
            req.kind.value,
            record,
            entry_hash(entry_id, now, symbol, req.kind.value, record, version),
            version,
        )
        await asyncio.to_thread(self._store.insert_entry, stored)
        return self._build(stored, [])

    # --- outcomes ----------------------------------------------------------------------

    async def record_outcome(self, entry_id: str, req: RecordOutcomeRequest) -> JournalEntry:
        stored = await asyncio.to_thread(self._store.get_entry, entry_id)
        if stored is None:
            raise JournalEntryNotFoundError(entry_id)
        entry = self._build(stored, (await asyncio.to_thread(self._store.outcomes, [entry_id]))[entry_id])
        if entry.kind is not JournalEntryKind.TRADE or entry.trade is None:
            raise JournalRequestError("outcomes can only be recorded for TRADE entries")
        if entry.snapshot.integrity is SnapshotIntegrity.TAMPERED:
            raise JournalRequestError("the entry failed its integrity check; no outcome can be added")
        now = self._clock()
        try:
            check_outcome(entry.trade, req, now)
        except OutcomeInputError as exc:
            raise JournalRequestError(str(exc)) from exc
        extremes = await self._extremes(entry.symbol, entry.trade, req, now)
        metrics = compute_outcome(entry.trade, req, extremes, entry.detected_violations, self._cfg)
        record: dict[str, object] = {
            "request": req.model_dump(mode="json", by_alias=True),
            "mfePrice": extremes.mfe,
            "maePrice": extremes.mae,
            "extremeSource": extremes.source.value,
            "extremesSynthetic": extremes.synthetic,
            "extremesDetail": extremes.detail,
            "result": metrics.result.value,
            "rMultiple": metrics.r_multiple,
            "plannedR": metrics.planned_r,
            "mfeR": metrics.mfe_r,
            "maeR": metrics.mae_r,
            "entryEfficiency": metrics.entry_efficiency,
            "exitEfficiency": metrics.exit_efficiency,
            "durationMinutes": metrics.duration_minutes,
            "violations": [v.value for v in metrics.violations],
            "classification": metrics.classification.value,
        }
        await asyncio.to_thread(self._store.insert_outcome, entry_id, now, record, outcome_hash)
        return await self.get(entry_id)

    async def _extremes(
        self, symbol: str, fill: JournalTradeFill, req: RecordOutcomeRequest, now: datetime
    ) -> Extremes:
        if req.mfe_price is not None and req.mae_price is not None:
            return Extremes(req.mfe_price, req.mae_price, ExtremeSource.MANUAL, False, None)
        candles = await self._candle_extremes(symbol, fill, req, now)
        if candles.source is ExtremeSource.UNAVAILABLE:
            return candles
        manual = [n for n, v in (("MFE", req.mfe_price), ("MAE", req.mae_price)) if v is not None]
        detail = f"{candles.detail}; {' and '.join(manual)} entered manually" if manual else candles.detail
        return Extremes(
            req.mfe_price if req.mfe_price is not None else candles.mfe,
            req.mae_price if req.mae_price is not None else candles.mae,
            ExtremeSource.CANDLES,
            candles.synthetic,
            detail,
        )

    async def _candle_extremes(
        self, symbol: str, fill: JournalTradeFill, req: RecordOutcomeRequest, now: datetime
    ) -> Extremes:
        if req.exited_at - fill.opened_at > timedelta(days=self._cfg.max_open_days):
            return Extremes(
                None, None, ExtremeSource.UNAVAILABLE, False, "trade longer than the candle look-up limit"
            )
        for tf in self._cfg.extremes_timeframes:
            bars = math.ceil((now - fill.opened_at) / tf.duration) + 2
            if bars > MAX_LIMIT:
                continue
            try:
                series = await self._candles.load_series(symbol, tf, bars, now)
            except Exception:
                logger.exception("journal extremes load failed for %s", symbol)
                break
            if series.quality in _WITHHELD:
                return Extremes(
                    None, None, ExtremeSource.UNAVAILABLE, False, f"{tf.value} data {series.quality.value}"
                )
            covering = [
                (c.high, c.low)
                for c in series.candles
                if c.open_time + tf.duration > fill.opened_at and c.open_time < req.exited_at
            ]
            if not series.candles or series.candles[0].open_time > fill.opened_at:
                return Extremes(
                    None,
                    None,
                    ExtremeSource.UNAVAILABLE,
                    False,
                    f"{tf.value} history does not reach the entry",
                )
            last = series.candles[-1].open_time + tf.duration
            if last < req.exited_at and not covering:
                return Extremes(
                    None,
                    None,
                    ExtremeSource.UNAVAILABLE,
                    False,
                    f"{tf.value} data ends {last.isoformat()}, before the trade",
                )
            return candle_extremes(fill, req.exit_price, covering, series.is_synthetic, f"{tf.value} candles")
        return Extremes(None, None, ExtremeSource.UNAVAILABLE, False, "no candle data covers the trade")

    # --- read ----------------------------------------------------------------------

    def _build(self, stored: StoredEntry, outcomes: list[StoredOutcome]) -> JournalEntry:
        rec: dict[str, Any] = stored.record
        verified = stored.record_hash == entry_hash(
            stored.id, _utc(stored.created_at), stored.symbol, stored.kind, rec, stored.strategy_version
        )
        raw_snap = rec.get("snapshot")
        snap: dict[str, Any] = raw_snap if isinstance(raw_snap, dict) else {}
        captured = (
            datetime.fromisoformat(str(snap.get("capturedAt")))
            if snap.get("capturedAt")
            else _utc(stored.created_at)
        )
        raw_decision = snap.get("decision")
        decision: dict[str, Any] = raw_decision if isinstance(raw_decision, dict) else {}
        evaluation = snap.get("evaluation") if isinstance(snap.get("evaluation"), dict) else None
        data = snap.get("data") if isinstance(snap.get("data"), dict) else None
        trade = JournalTradeFill.model_validate(rec["trade"]) if rec.get("trade") else None
        detected = [RuleViolation(v) for v in rec.get("detectedViolations") or []]
        integrity = SnapshotIntegrity.VERIFIED if verified else SnapshotIntegrity.TAMPERED
        revisions = [self._outcome(stored.id, o) for o in outcomes]
        latest = revisions[-1] if revisions else None
        kind = JournalEntryKind(stored.kind)
        if kind is JournalEntryKind.TRADE:
            status = JournalStatus.CLOSED if latest else JournalStatus.OPEN
            result = latest.result if latest else None
        else:
            status = JournalStatus.CLOSED
            result = (
                TradeResult.MISSED_ENTRY if kind is JournalEntryKind.MISSED_ENTRY else TradeResult.NO_TRADE
            )
        return JournalEntry(
            id=stored.id,
            kind=kind,
            status=status,
            symbol=stored.symbol,
            created_at=_utc(stored.created_at),
            trade=trade,
            notes=str(rec.get("notes") or ""),
            detected_violations=detected,
            result=result,
            classification=latest.classification if latest else None,
            r_multiple=latest.r_multiple if latest else None,
            summary=summarize(decision, evaluation, data, captured),
            snapshot=JournalSnapshot(
                captured_at=captured,
                timing=SnapshotTiming(str(snap.get("timing") or SnapshotTiming.NOT_APPLICABLE.value)),
                integrity=integrity,
                hash=stored.record_hash,
                decision=decision,
                evaluation=evaluation,
                data=data,
            ),
            outcome=latest,
            outcome_revisions=revisions,
            strategy_version=stored.strategy_version,
        )

    @staticmethod
    def _outcome(entry_id: str, o: StoredOutcome) -> JournalOutcome:
        r: dict[str, Any] = o.record
        req = RecordOutcomeRequest.model_validate(r["request"])
        verified = o.record_hash == outcome_hash(entry_id, o.revision, _utc(o.recorded_at), o.record)
        return JournalOutcome(
            revision=o.revision,
            recorded_at=_utc(o.recorded_at),
            exit_price=req.exit_price,
            exited_at=req.exited_at,
            exit_reason=req.exit_reason,
            mfe_price=r.get("mfePrice"),
            mae_price=r.get("maePrice"),
            extreme_source=ExtremeSource(r["extremeSource"]),
            extremes_synthetic=bool(r.get("extremesSynthetic")),
            extremes_detail=r.get("extremesDetail"),
            result=r["result"],
            r_multiple=r.get("rMultiple"),
            planned_r=r.get("plannedR"),
            mfe_r=r.get("mfeR"),
            mae_r=r.get("maeR"),
            entry_efficiency=r.get("entryEfficiency"),
            exit_efficiency=r.get("exitEfficiency"),
            duration_minutes=float(r["durationMinutes"]),
            reported_violations=req.reported_violations,
            violations=[RuleViolation(v) for v in r.get("violations") or []],
            classification=r["classification"],
            notes=req.notes,
            integrity=SnapshotIntegrity.VERIFIED if verified else SnapshotIntegrity.TAMPERED,
        )

    async def get(self, entry_id: str) -> JournalEntry:
        stored = await asyncio.to_thread(self._store.get_entry, entry_id)
        if stored is None:
            raise JournalEntryNotFoundError(entry_id)
        outcomes = await asyncio.to_thread(self._store.outcomes, [entry_id])
        return self._build(stored, outcomes[entry_id])

    async def list_entries(
        self,
        *,
        symbol: str | None = None,
        kind: JournalEntryKind | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JournalListResponse:
        now = self._clock()
        info = await self.info()
        if not info.available:
            return JournalListResponse(entries=[], next_cursor=None, store=info, generated_at=now)
        rows, next_cursor = await asyncio.to_thread(
            lambda: self._store.list_entries(
                symbol=symbol.upper() if symbol else None,
                kind=kind.value if kind else None,
                start=start,
                end=end,
                limit=min(limit or self._cfg.list_default_limit, self._cfg.list_max_limit),
                cursor=cursor,
            )
        )
        outcomes = await asyncio.to_thread(self._store.outcomes, [r.id for r in rows])
        entries = [self._row(self._build(r, outcomes[r.id])) for r in rows]
        return JournalListResponse(entries=entries, next_cursor=next_cursor, store=info, generated_at=now)

    @staticmethod
    def _row(e: JournalEntry) -> JournalEntryRow:
        return JournalEntryRow(
            id=e.id,
            kind=e.kind,
            status=e.status,
            symbol=e.symbol,
            created_at=e.created_at,
            direction=e.trade.direction if e.trade else None,
            entry=e.trade.entry if e.trade else None,
            result=e.result,
            classification=e.classification,
            r_multiple=e.r_multiple,
            detected_violations=e.detected_violations,
            snapshot_timing=e.snapshot.timing,
            integrity=(
                SnapshotIntegrity.TAMPERED
                if e.snapshot.integrity is SnapshotIntegrity.TAMPERED
                or any(o.integrity is SnapshotIntegrity.TAMPERED for o in e.outcome_revisions)
                else SnapshotIntegrity.VERIFIED
            ),
            is_synthetic=e.summary.is_synthetic,
            setup_type=e.summary.setup_type,
            verdict=e.summary.verdict,
        )

    async def export(self) -> JournalExport:
        now = self._clock()
        info = await self.info()
        if not info.available:
            raise JournalUnavailableError(info.reason or "journal unavailable")
        entries: list[JournalEntry] = []
        cursor: str | None = None
        while True:
            rows, cursor = await asyncio.to_thread(self._page, cursor)
            outcomes = await asyncio.to_thread(self._store.outcomes, [r.id for r in rows])
            entries += [self._build(r, outcomes[r.id]) for r in rows]
            if cursor is None:
                break
        return JournalExport(
            exported_at=now, strategy_version=strategy_version(), store=info, entries=entries
        )

    def _page(self, cursor: str | None) -> tuple[list[StoredEntry], str | None]:
        return self._store.list_entries(
            symbol=None, kind=None, start=None, end=None, limit=self._cfg.list_max_limit, cursor=cursor
        )

    async def delete(self, entry_id: str) -> None:
        if not await asyncio.to_thread(self._store.delete_entry, entry_id):
            raise JournalEntryNotFoundError(entry_id)
