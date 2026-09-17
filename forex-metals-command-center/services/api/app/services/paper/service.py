"""Paper trading service: broker-free simulation on the independent market data. Never an authorization."""

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
    Direction,
    ExitReason,
    PaperEntryType,
    PaperEventType,
    PaperSource,
    PaperStatus,
    RuleViolation,
    SnapshotIntegrity,
)
from app.domain.instrument import get_instrument
from app.services.candles.service import MAX_LIMIT, CandleService, UnknownSymbolError
from app.services.journal.capture import capture_engine_state
from app.services.journal.engine import (
    candle_extremes,
    canonical_hash,
    compute_outcome,
    detect_violations,
    summarize,
)
from app.services.journal.models import JournalConfig, JournalTradeFill, RecordOutcomeRequest
from app.services.market_state.service import MarketStateService
from app.services.paper.engine import SimEvent, SimParams, SimState, manual_close_price, simulate
from app.services.paper.models import (
    AssumedCosts,
    CreatePaperSimRequest,
    PaperConfig,
    PaperEvent,
    PaperListResponse,
    PaperResult,
    PaperSim,
    PaperSimRow,
    PaperStoreInfo,
)
from app.services.paper.store import PaperStore, PaperUnavailableError, StoredEvent, StoredSim
from app.services.scoring.service import EvaluationService

logger = logging.getLogger("fmcc.paper")
_WITHHELD = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})
ACTIVE = (PaperStatus.PENDING, PaperStatus.OPEN)
MAX_CHUNKS = 20


class PaperSimNotFoundError(LookupError):
    pass


class PaperRequestError(ValueError):
    """Safe message, never containing submitted values."""


def sim_hash(sim_id: str, created_at: datetime, symbol: str, record: dict[str, object], version: str) -> str:
    return canonical_hash(
        {
            "id": sim_id,
            "createdAt": created_at.isoformat(),
            "symbol": symbol,
            "record": record,
            "strategyVersion": version,
        }
    )


def event_hash(sim_id: str, seq: int, record: dict[str, object]) -> str:
    return canonical_hash({"simId": sim_id, "seq": seq, "record": record})


def _dt(v: object) -> datetime | None:
    return datetime.fromisoformat(str(v)) if v else None


def _float(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def _str(v: object) -> str | None:
    return str(v) if v is not None else None


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def state_to_progress(
    state: SimState, quality: DataQuality | None, note: str | None, synthetic: bool
) -> dict[str, object]:
    return {
        "status": state.status.value,
        "fillPrice": state.fill_price,
        "filledAt": state.filled_at.isoformat() if state.filled_at else None,
        "exitPrice": state.exit_price,
        "exitedAt": state.exited_at.isoformat() if state.exited_at else None,
        "exitReason": state.exit_reason.value if state.exit_reason else None,
        "pendingBars": state.pending_bars,
        "best": state.best,
        "worst": state.worst,
        "processedThrough": state.processed_through.isoformat() if state.processed_through else None,
        "dataQuality": quality.value if quality else None,
        "dataNote": note,
        "synthetic": synthetic,
    }


def progress_to_state(p: dict[str, Any]) -> SimState:
    return SimState(
        status=PaperStatus(p["status"]),
        fill_price=p.get("fillPrice"),
        filled_at=_dt(p.get("filledAt")),
        exit_price=p.get("exitPrice"),
        exited_at=_dt(p.get("exitedAt")),
        exit_reason=ExitReason(p["exitReason"]) if p.get("exitReason") else None,
        pending_bars=int(p.get("pendingBars") or 0),
        best=p.get("best"),
        worst=p.get("worst"),
        processed_through=_dt(p.get("processedThrough")),
    )


def params_of(record: dict[str, Any], created_at: datetime, expiry: int) -> SimParams:
    return SimParams(
        direction=Direction(record["direction"]),
        entry_type=PaperEntryType(record["entryType"]),
        limit_price=record.get("limitPrice"),
        stop=float(record["stop"]),
        target=float(record["target"]),
        created_at=created_at,
        costs=AssumedCosts.model_validate(record["costs"]),
        pending_expiry_bars=int(record.get("pendingExpiryBars") or expiry),
    )


class PaperService:
    def __init__(
        self,
        store: PaperStore,
        market_state: MarketStateService,
        evaluation: EvaluationService,
        candles: CandleService,
        clock: Callable[[], datetime] | None = None,
        cfg: PaperConfig | None = None,
        monitor_enabled: bool = False,
    ) -> None:
        self._store = store
        self._market = market_state
        self._evaluation = evaluation
        self._candles = candles
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or PaperConfig.from_spec()
        self._jcfg = JournalConfig.from_spec()
        self._monitor = monitor_enabled
        self._lock = asyncio.Lock()

    @property
    def cfg(self) -> PaperConfig:
        return self._cfg

    async def info(self) -> PaperStoreInfo:
        reason = await asyncio.to_thread(self._store.check)
        return PaperStoreInfo(
            available=reason is None,
            backend=self._store.backend,
            reason=reason,
            monitor_enabled=self._monitor,
        )

    async def _require(self) -> None:
        info = await self.info()
        if not info.available:
            raise PaperUnavailableError(info.reason or "paper trading unavailable")

    # --- create ------------------------------------------------------------

    async def create(self, req: CreatePaperSimRequest) -> PaperSim:
        instrument = get_instrument(req.symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(req.symbol)
        await self._require()
        symbol = instrument.symbol
        costs = self._cfg.costs.get(symbol)
        if costs is None:
            raise PaperRequestError(f"no assumed costs are configured for {symbol} (paper.json)")
        if (
            len(await asyncio.to_thread(self._store.active_ids, self._cfg.max_open_sims))
            >= self._cfg.max_open_sims
        ):
            raise PaperRequestError(f"at most {self._cfg.max_open_sims} pending/open paper sims")
        now = self._clock()
        series = await self._candles.load_series(symbol, self._cfg.timeframe, 50, now)
        closed = [c for c in series.candles if c.is_closed]
        if series.quality in _WITHHELD or not closed:
            raise PaperRequestError("no usable market data to simulate against")
        state = await capture_engine_state(self._market, self._evaluation, symbol, now)
        limit: float | None
        if req.source is PaperSource.ENGINE_PLAN:
            plan = (state.evaluation or {}).get("plan")
            if not isinstance(plan, dict):
                raise PaperRequestError("there is no confirmed plan to forward-test")
            direction = Direction(plan["direction"])
            entry_type, limit, stop, target = (
                PaperEntryType.LIMIT,
                float(plan["entry"]),
                float(plan["stop"]),
                float(plan["tp1"]),
            )
        else:
            assert req.direction is not None and req.stop is not None and req.target is not None
            direction, entry_type, limit, stop, target = (
                req.direction,
                req.entry_type,
                req.limit_price,
                req.stop,
                req.target,
            )
        limit = round(limit, 10) if limit is not None else None
        stop, target = round(stop, 10), round(target, 10)
        reference = limit if limit is not None else closed[-1].close
        s = 1 if direction is Direction.BULLISH else -1
        if s * (reference - stop) <= 0 or s * (target - reference) <= 0:
            raise PaperRequestError(
                "stop must be on the losing side and target on the winning side of the entry"
            )
        fill = JournalTradeFill(
            direction=direction, entry=reference, stop=stop, targets=[target], opened_at=now
        )
        detected = detect_violations(fill, state.decision, state.evaluation, self._jcfg)
        record: dict[str, object] = {
            "source": req.source.value,
            "direction": direction.value,
            "entryType": entry_type.value,
            "limitPrice": limit,
            "referencePrice": reference,
            "stop": stop,
            "target": target,
            "costs": costs.model_dump(mode="json", by_alias=True),
            "pendingExpiryBars": self._cfg.pending_expiry_bars,
            "timeframe": self._cfg.timeframe.value,
            "notes": req.notes,
            "detectedViolations": [v.value for v in detected],
            "snapshot": {
                "capturedAt": now.isoformat(),
                "decision": state.decision,
                "evaluation": state.evaluation,
                "data": state.data,
            },
        }
        sim_id = str(uuid.uuid4())
        version = strategy_version()
        created = {
            "type": PaperEventType.CREATED.value,
            "at": now.isoformat(),
            "price": reference,
            "ambiguous": False,
            "detail": f"{entry_type.value} {direction.value} created ({req.source.value})",
        }
        progress = state_to_progress(SimState(), series.quality, None, series.is_synthetic)
        stored = StoredSim(
            sim_id,
            now,
            symbol,
            record,
            sim_hash(sim_id, now, symbol, record, version),
            version,
            PaperStatus.PENDING.value,
            progress,
        )
        await asyncio.to_thread(
            self._store.insert_sim, stored, [StoredEvent(sim_id, 1, created, event_hash(sim_id, 1, created))]
        )
        return await self.get(sim_id, advance=False)

    # --- advance ------------------------------------------------------------

    async def advance(self, sim_id: str) -> None:
        async with self._lock:
            stored = await asyncio.to_thread(self._store.get_sim, sim_id)
            if stored is None or PaperStatus(stored.status) not in ACTIVE:
                return
            if self._build(stored, []).integrity is SnapshotIntegrity.TAMPERED:
                return  # a changed creation record is never simulated further
            await self._advance(stored)

    async def _advance(self, stored: StoredSim) -> SimState:
        tf = self._cfg.timeframe
        params = params_of(stored.record, _utc(stored.created_at), self._cfg.pending_expiry_bars)
        state = progress_to_state(stored.progress)
        now = self._clock()
        events: list[SimEvent] = []
        quality: DataQuality | None = None
        note: str | None = None
        synthetic = bool(stored.progress.get("synthetic"))
        limit = min(self._cfg.max_bars_per_advance, MAX_LIMIT)
        for _ in range(MAX_CHUNKS):
            since = state.processed_through or (params.created_at - tf.duration)
            end = min(now, since + tf.duration * (limit - 2))
            bars_needed = math.ceil((end - since) / tf.duration) + 2
            try:
                series = await self._candles.load_series(stored.symbol, tf, min(bars_needed, limit), end)
            except Exception:
                logger.exception("paper advance load failed for %s", stored.symbol)
                quality, note = None, "market data load failed"
                break
            quality, synthetic = series.quality, synthetic or series.is_synthetic
            if series.quality in _WITHHELD:
                note = f"{tf.value} data {series.quality.value}: not advancing"
                break
            bars = [c for c in series.candles if c.is_closed and c.open_time > since]
            state, new = simulate(params, state, bars)
            events += new
            if state.status not in ACTIVE or end >= now:
                break
        if state.status in ACTIVE and quality is DataQuality.STALE:
            note = "market data is stale: waiting for new closed bars"
        await self._persist(stored, state, events, quality, note, synthetic)
        return state

    async def _persist(
        self,
        stored: StoredSim,
        state: SimState,
        events: list[SimEvent],
        quality: DataQuality | None,
        note: str | None,
        synthetic: bool,
    ) -> None:
        existing = len((await asyncio.to_thread(self._store.events, [stored.id]))[stored.id])
        rows = []
        for i, e in enumerate(events, start=existing + 1):
            rec: dict[str, object] = {
                "type": e.type.value,
                "at": e.at.isoformat(),
                "price": e.price,
                "ambiguous": e.ambiguous,
                "detail": e.detail,
            }
            rows.append(StoredEvent(stored.id, i, rec, event_hash(stored.id, i, rec)))
        progress = state_to_progress(state, quality, note, synthetic)
        ok = await asyncio.to_thread(
            self._store.advance, stored.id, stored.status, state.status.value, progress, rows
        )
        if not ok:
            logger.warning("paper sim %s changed concurrently; advance skipped", stored.id)

    async def advance_all(self) -> int:
        ids = await asyncio.to_thread(self._store.active_ids, self._cfg.max_open_sims)
        for sim_id in ids:
            try:
                await self.advance(sim_id)
            except Exception:
                logger.exception("paper advance failed for %s", sim_id)
        return len(ids)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.advance_all()
            except Exception:
                logger.exception("paper monitor cycle failed")
            await asyncio.sleep(self._cfg.advance_seconds)

    # --- user actions ------------------------------------------------------------

    async def close_now(self, sim_id: str) -> PaperSim:
        async with self._lock:
            stored = await self._stored(sim_id)
            state = (
                await self._advance(stored)
                if PaperStatus(stored.status) in ACTIVE
                else progress_to_state(stored.progress)
            )
            if state.status is not PaperStatus.OPEN:
                raise PaperRequestError("only OPEN paper sims can be closed")
            stored = await self._stored(sim_id)
            now = self._clock()
            series = await self._candles.load_series(stored.symbol, self._cfg.timeframe, 5, now)
            closed = [c for c in series.candles if c.is_closed]
            if series.quality in _WITHHELD or not closed or closed[-1].open_time != state.processed_through:
                raise PaperRequestError("no current closed bar to close against")
            params = params_of(stored.record, _utc(stored.created_at), self._cfg.pending_expiry_bars)
            bar = closed[-1]
            price = manual_close_price(params, bar)
            closed_state = SimState(
                **{
                    **state.__dict__,
                    "status": PaperStatus.CLOSED,
                    "exit_price": price,
                    "exited_at": bar.close_time,
                    "exit_reason": ExitReason.MANUAL,
                }
            )
            event = SimEvent(
                PaperEventType.CLOSED_MANUALLY,
                now,
                price,
                False,
                f"closed at the {bar.timeframe.value} close of {bar.open_time.isoformat()} (costs applied)",
            )
            await self._persist(
                stored, closed_state, [event], series.quality, None, bool(stored.progress.get("synthetic"))
            )
        return await self.get(sim_id, advance=False)

    async def cancel(self, sim_id: str) -> PaperSim:
        async with self._lock:
            stored = await self._stored(sim_id)
            state = (
                await self._advance(stored)
                if PaperStatus(stored.status) in ACTIVE
                else progress_to_state(stored.progress)
            )
            if state.status is not PaperStatus.PENDING:
                raise PaperRequestError("only PENDING paper sims can be cancelled")
            stored = await self._stored(sim_id)
            now = self._clock()
            event = SimEvent(
                PaperEventType.CANCELLED, now, None, False, "cancelled by the user before a fill"
            )
            cancelled = SimState(**{**state.__dict__, "status": PaperStatus.CANCELLED})
            await self._persist(
                stored, cancelled, [event], None, None, bool(stored.progress.get("synthetic"))
            )
        return await self.get(sim_id, advance=False)

    async def delete(self, sim_id: str) -> None:
        await self._require()
        if not await asyncio.to_thread(self._store.delete_sim, sim_id):
            raise PaperSimNotFoundError(sim_id)

    # --- read ------------------------------------------------------------

    async def _stored(self, sim_id: str) -> StoredSim:
        await self._require()
        stored = await asyncio.to_thread(self._store.get_sim, sim_id)
        if stored is None:
            raise PaperSimNotFoundError(sim_id)
        return stored

    async def get(self, sim_id: str, *, advance: bool = True) -> PaperSim:
        if advance:
            await self._stored(sim_id)
            await self.advance(sim_id)
        stored = await self._stored(sim_id)
        events = (await asyncio.to_thread(self._store.events, [sim_id]))[sim_id]
        return self._build(stored, events)

    async def list_sims(
        self,
        *,
        symbol: str | None = None,
        status: PaperStatus | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PaperListResponse:
        now = self._clock()
        info = await self.info()
        if not info.available:
            return PaperListResponse(sims=[], next_cursor=None, store=info, generated_at=now)
        await self.advance_all()
        rows, next_cursor = await asyncio.to_thread(
            lambda: self._store.list_sims(
                symbol=symbol.upper() if symbol else None,
                status=status.value if status else None,
                limit=min(limit, 200),
                cursor=cursor,
            )
        )
        events = await asyncio.to_thread(self._store.events, [r.id for r in rows])
        sims = [self._row(self._build(r, events[r.id])) for r in rows]
        return PaperListResponse(sims=sims, next_cursor=next_cursor, store=info, generated_at=now)

    async def export_sims(self) -> list[PaperSim]:
        """Every stored sim (no advancing), for analytics."""
        await self._require()
        out: list[PaperSim] = []
        cursor: str | None = None
        for _ in range(10_000):
            rows, cursor = await asyncio.to_thread(self._page, cursor)
            events = await asyncio.to_thread(self._store.events, [r.id for r in rows])
            out += [self._build(r, events[r.id]) for r in rows]
            if cursor is None:
                break
        return out

    def _page(self, cursor: str | None) -> tuple[list[StoredSim], str | None]:
        return self._store.list_sims(symbol=None, status=None, limit=200, cursor=cursor)

    def _build(self, stored: StoredSim, events: list[StoredEvent]) -> PaperSim:
        rec: dict[str, Any] = stored.record
        created = _utc(stored.created_at)
        verified = stored.record_hash == sim_hash(
            stored.id, created, stored.symbol, rec, stored.strategy_version
        )
        ev_views = [
            PaperEvent(
                seq=e.seq,
                type=PaperEventType(str(e.record["type"])),
                at=datetime.fromisoformat(str(e.record["at"])),
                price=_float(e.record.get("price")),
                ambiguous=bool(e.record.get("ambiguous")),
                detail=str(e.record.get("detail") or ""),
                integrity=SnapshotIntegrity.VERIFIED
                if e.record_hash == event_hash(stored.id, e.seq, e.record)
                else SnapshotIntegrity.TAMPERED,
            )
            for e in events
        ]
        integrity = (
            SnapshotIntegrity.VERIFIED
            if verified and all(e.integrity is SnapshotIntegrity.VERIFIED for e in ev_views)
            else SnapshotIntegrity.TAMPERED
        )
        params = params_of(rec, created, self._cfg.pending_expiry_bars)
        state = progress_to_state(stored.progress)
        detected = [RuleViolation(v) for v in rec.get("detectedViolations") or []]
        snap = rec.get("snapshot") or {}
        summary = summarize(
            snap.get("decision") or {},
            snap.get("evaluation"),
            snap.get("data"),
            _dt(snap.get("capturedAt")) or created,
        )
        return PaperSim(
            id=stored.id,
            symbol=stored.symbol,
            source=PaperSource(rec["source"]),
            direction=params.direction,
            entry_type=params.entry_type,
            reference_price=float(rec["referencePrice"]),
            limit_price=params.limit_price,
            stop=params.stop,
            target=params.target,
            costs=params.costs,
            status=state.status,
            created_at=created,
            fill_price=state.fill_price,
            filled_at=state.filled_at,
            exit_price=state.exit_price,
            exited_at=state.exited_at,
            processed_through=state.processed_through,
            data_quality=DataQuality(str(stored.progress["dataQuality"]))
            if stored.progress.get("dataQuality")
            else None,
            data_note=_str(stored.progress.get("dataNote")),
            is_synthetic=bool(stored.progress.get("synthetic")) or summary.is_synthetic,
            detected_violations=detected,
            result=self._result(params, float(rec["referencePrice"]), state, detected),
            events=ev_views,
            summary=summary,
            integrity=integrity,
            notes=str(rec.get("notes") or ""),
            authority="SIMULATION_ONLY",
            strategy_version=stored.strategy_version,
        )

    def _result(
        self, p: SimParams, reference: float, state: SimState, detected: list[RuleViolation]
    ) -> PaperResult | None:
        if state.status is not PaperStatus.CLOSED or state.fill_price is None or state.exit_price is None:
            return None
        assert state.filled_at is not None and state.exited_at is not None and state.exit_reason is not None
        s = p.s
        entry = (
            state.fill_price if s * (state.fill_price - p.stop) > 0 else reference
        )  # filled beyond the stop: planned risk
        fill = JournalTradeFill(
            direction=p.direction, entry=entry, stop=p.stop, targets=[p.target], opened_at=state.filled_at
        )
        exited = (
            state.exited_at if state.exited_at > state.filled_at else state.filled_at + timedelta(seconds=1)
        )
        outcome = RecordOutcomeRequest(
            exit_price=state.exit_price, exited_at=exited, exit_reason=state.exit_reason
        )
        bars = (
            [(max(state.best, state.worst), min(state.best, state.worst))]
            if state.best is not None and state.worst is not None
            else []
        )
        extremes = candle_extremes(fill, state.exit_price, bars, False, f"{self._cfg.timeframe.value} bars")
        m = compute_outcome(fill, outcome, extremes, detected, self._jcfg)
        risk = abs(entry - p.stop)
        net = (
            round(m.r_multiple - 2 * p.costs.commission / risk, 3)
            if m.r_multiple is not None and risk
            else None
        )
        return PaperResult(
            result=m.result,
            exit_reason=state.exit_reason,
            r_multiple=m.r_multiple,
            net_r_multiple=net,
            planned_r=m.planned_r,
            mfe_r=m.mfe_r,
            mae_r=m.mae_r,
            entry_efficiency=m.entry_efficiency,
            exit_efficiency=m.exit_efficiency,
            duration_minutes=m.duration_minutes,
            violations=m.violations,
            classification=m.classification,
        )

    @staticmethod
    def _row(sim: PaperSim) -> PaperSimRow:
        return PaperSimRow(
            id=sim.id,
            symbol=sim.symbol,
            source=sim.source,
            direction=sim.direction,
            entry_type=sim.entry_type,
            status=sim.status,
            created_at=sim.created_at,
            fill_price=sim.fill_price,
            exit_price=sim.exit_price,
            result=sim.result.result if sim.result else None,
            net_r_multiple=sim.result.net_r_multiple if sim.result else None,
            classification=sim.result.classification if sim.result else None,
            is_synthetic=sim.is_synthetic,
            integrity=sim.integrity,
        )
