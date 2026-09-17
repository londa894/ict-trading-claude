"""Backtest service: background research runs of the live setup engine over closed history.

Research only (authority RESEARCH_ONLY). One run at a time, in process; a run interrupted by a restart
is reported FAILED.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.contracts import strategy_version
from app.domain.candle import Candle
from app.domain.enums import BacktestStatus, DataQuality, Timeframe
from app.domain.instrument import get_instrument
from app.services.analytics.models import AnalyticsConfig
from app.services.backtest.engine import run_variant, summarize_variant
from app.services.backtest.models import (
    BacktestConfig,
    BacktestData,
    BacktestListResponse,
    BacktestProgress,
    BacktestRequest,
    BacktestResult,
    BacktestRun,
    BacktestRunRow,
    BacktestStoreInfo,
    VariantResult,
)
from app.services.backtest.store import BacktestStore, BacktestUnavailableError, StoredRun
from app.services.candles.service import MAX_LIMIT, CandleService, UnknownSymbolError
from app.services.entry.models import EntryConfig
from app.services.journal.engine import canonical_hash
from app.services.journal.models import JournalConfig
from app.services.paper.models import PaperConfig
from app.services.setup_state.models import SetupAnalysis, SetupConfig
from app.services.setup_state.service import SetupService

logger = logging.getLogger("fmcc.backtest")
_WITHHELD = frozenset({DataQuality.INVALID, DataQuality.DISCONNECTED})
ACTIVE = frozenset({BacktestStatus.QUEUED.value, BacktestStatus.RUNNING.value})
DISCLOSURES = [
    "Sequential replay: at every closed setup-timeframe candle the live setup engine is evaluated as of that "
    "close; nothing after it is visible (same code path as live decisions).",
    "Fills and exits are simulated on closed execution bars (bar-level, mid-price candles): same-bar stop "
    "and target resolve to the stop; limit fills on the fill bar only allow the stop; gaps fill at the open.",
    "Spread, slippage and commission are assumptions from paper.json (times the variant cost multiplier), "
    "not broker specifications.",
    "Risk locks, the news gate and verdict authority are NOT applied (no historical account state or "
    "calendar): results describe confirmed plans, not authorized trades.",
    "Session and DST timing come from the shared New York timeframe code; only closed candles are used.",
    "No parameters are fitted: segments and in/out-of-sample splits show stability, not optimisation.",
    "One simulated position at a time; plans during an open or pending position are skipped and counted.",
    "Past simulated results are not a forecast, a probability or a guarantee.",
]


class BacktestNotFoundError(LookupError):
    pass


class BacktestRequestError(ValueError):
    pass


def _ineligibility_notes(results: list[VariantResult]) -> list[str]:
    notes = []
    for v in results:
        f = v.funnel
        if f.steps and f.ineligible_steps * 2 >= f.steps:
            why = ", ".join(f"{k} {n}" for k, n in f.ineligible_reasons.items()) or "unknown"
            notes.append(
                f"Variant {v.variant.name}: {f.ineligible_steps} of {f.steps} steps were not eligible for a "
                f"decision ({why}); no plans can be taken on those steps (synthetic data is never "
                "decision-eligible)."
            )
    return notes


def result_hash(run_id: str, request: dict[str, Any], result: dict[str, Any], version: str) -> str:
    return canonical_hash({"id": run_id, "request": request, "result": result, "strategyVersion": version})


class BacktestService:
    def __init__(
        self,
        store: BacktestStore,
        candles: CandleService,
        clock: Callable[[], datetime] | None = None,
        cfg: BacktestConfig | None = None,
    ) -> None:
        self._store = store
        self._candles = candles
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or BacktestConfig.from_spec()
        self._paper = PaperConfig.from_spec()
        self._setup_cfg = SetupConfig.from_spec()
        self._running: str | None = None
        self._cancel: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def info(self) -> BacktestStoreInfo:
        reason = await asyncio.to_thread(self._store.check)
        return BacktestStoreInfo(
            available=reason is None, backend=self._store.backend, reason=reason, running_id=self._running
        )

    async def _require(self) -> None:
        info = await self.info()
        if not info.available:
            raise BacktestUnavailableError(info.reason or "backtesting unavailable")

    # --- lifecycle ------------------------------------------------------------

    async def start(self, req: BacktestRequest) -> BacktestRun:
        instrument = get_instrument(req.symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(req.symbol)
        await self._require()
        if req.end > self._clock():
            raise BacktestRequestError("end cannot be in the future (only closed history is replayed)")
        if instrument.symbol not in self._paper.costs:
            raise BacktestRequestError(
                f"no assumed costs are configured for {instrument.symbol} (paper.json)"
            )
        if self._running is not None:
            raise BacktestRequestError("a backtest is already running; wait for it or cancel it")
        now = self._clock()
        request = req.model_copy(update={"symbol": instrument.symbol})
        run_id = str(uuid.uuid4())
        stored = StoredRun(
            id=run_id,
            created_at=now,
            symbol=instrument.symbol,
            status=BacktestStatus.QUEUED.value,
            request=request.model_dump(mode="json", by_alias=True),
            progress={"variant": None, "stepsDone": 0, "stepsTotal": 0, "pct": 0.0},
            result=None,
            result_hash=None,
            error=None,
            started_at=None,
            finished_at=None,
            strategy_version=strategy_version(),
        )
        await asyncio.to_thread(self._store.insert, stored)
        self._running = run_id
        self._task = asyncio.create_task(self._execute(run_id, request))
        return await self.get(run_id)

    async def cancel(self, run_id: str) -> BacktestRun:
        run = await self.get(run_id)
        if run.status.value not in ACTIVE:
            raise BacktestRequestError("only queued or running backtests can be cancelled")
        self._cancel.add(run_id)
        return run

    async def wait(self) -> None:
        """Test/ops helper: wait for the background run (if any) to finish."""
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _update(self, run_id: str, **values: object) -> None:
        await asyncio.to_thread(lambda: self._store.update(run_id, **values))

    async def _load(
        self, symbol: str, tf: Timeframe, start: datetime, end: datetime
    ) -> tuple[list[Candle], bool]:
        """Closed candles with start <= open_time and close_time <= end, loaded backwards in chunks."""
        by_open: dict[datetime, Candle] = {}
        chunk_end = end
        synthetic = False
        for _ in range(200):
            series = await self._candles.load_series(symbol, tf, MAX_LIMIT, chunk_end)
            synthetic = synthetic or series.is_synthetic
            if series.quality in _WITHHELD:
                raise BacktestRequestError(
                    f"{tf.value} history {series.quality.value}: the run cannot be trusted"
                )
            fresh = [
                c for c in series.candles if c.is_closed and c.close_time <= end and c.open_time >= start
            ]
            new = [c for c in fresh if c.open_time not in by_open]
            for c in new:
                by_open[c.open_time] = c
            if not series.candles or not new:
                break
            earliest = min(c.open_time for c in series.candles)
            if earliest <= start:
                break
            chunk_end = earliest
        return sorted(by_open.values(), key=lambda c: c.open_time), synthetic

    async def _execute(self, run_id: str, req: BacktestRequest) -> None:
        now = self._clock()
        try:
            await self._update(run_id, status=BacktestStatus.RUNNING.value, started_at=now)
            step_tf = self._setup_cfg.setup_timeframe
            step_bars, syn_a = await self._load(req.symbol, step_tf, req.start, req.end)
            exec_bars, syn_b = await self._load(req.symbol, self._paper.timeframe, req.start, req.end)
            synthetic = syn_a or syn_b
            if not step_bars or not exec_bars:
                raise BacktestRequestError("no closed history in the requested range")
            acfg = AnalyticsConfig.from_spec()
            tol = JournalConfig.from_spec().break_even_tolerance_r
            results = []
            total_steps = len(step_bars) * len(req.variants)
            for index, variant in enumerate(req.variants):
                setups = SetupService(self._candles, entry_cfg=EntryConfig.from_spec(variant.entry_mode))

                async def analyze(t: datetime, svc: SetupService = setups) -> SetupAnalysis:
                    return await svc.analyze(req.symbol, t)

                async def progress(
                    done: int, total: int, index: int = index, name: str = variant.name
                ) -> None:
                    overall = (index * len(step_bars) + done) / max(total_steps, 1)
                    await self._update(
                        run_id,
                        progress={
                            "variant": name,
                            "stepsDone": done,
                            "stepsTotal": total,
                            "pct": round(overall * 100, 1),
                        },
                    )

                funnel, trades = await run_variant(
                    variant,
                    analyze,
                    step_bars,
                    exec_bars,
                    step_tf,
                    self._paper.costs[req.symbol],
                    self._paper.pending_expiry_bars,
                    req.end,
                    progress,
                    self._cfg.progress_every_steps,
                    lambda: run_id in self._cancel,
                )
                results.append(
                    summarize_variant(
                        variant,
                        funnel,
                        trades,
                        symbol=req.symbol,
                        start=req.start,
                        end=req.end,
                        segments=req.segments,
                        out_of_sample_from=req.out_of_sample_from,
                        synthetic=synthetic,
                        version=strategy_version(),
                        cfg=self._cfg,
                        acfg=acfg,
                        tol=tol,
                    )
                )
            config_hash = canonical_hash(
                {
                    "backtest": asdict(self._cfg),
                    "paperCosts": self._paper.costs[req.symbol].model_dump(),
                    "version": strategy_version(),
                }
            )
            result = BacktestResult(
                variants=results,
                data=BacktestData(
                    provider=self._candles.provider.name,
                    is_synthetic=synthetic,
                    execution_bars=len(exec_bars),
                    step_bars=len(step_bars),
                    first_bar=exec_bars[0].open_time,
                    last_bar=exec_bars[-1].open_time,
                ),
                disclosures=DISCLOSURES
                + (
                    ["The market data is SYNTHETIC: results say nothing about real markets."]
                    if synthetic
                    else []
                )
                + _ineligibility_notes(results),
                config_hash=config_hash,
                completed_at=self._clock(),
            )
            dump = result.model_dump(mode="json", by_alias=True)
            request_dump = req.model_dump(mode="json", by_alias=True)
            await self._update(
                run_id,
                status=BacktestStatus.COMPLETED.value,
                progress={"variant": None, "stepsDone": total_steps, "stepsTotal": total_steps, "pct": 100.0},
                result=dump,
                result_hash=result_hash(run_id, request_dump, dump, strategy_version()),
                finished_at=self._clock(),
            )
        except Exception as exc:
            cancelled = run_id in self._cancel
            if not cancelled:
                logger.exception("backtest %s failed", run_id)
            message = (
                str(exc)
                if isinstance(exc, BacktestRequestError)
                else f"backtest failed ({type(exc).__name__})"
            )
            await self._update(
                run_id,
                status=(BacktestStatus.CANCELLED if cancelled else BacktestStatus.FAILED).value,
                error="cancelled by the user" if cancelled else message[:500],
                finished_at=self._clock(),
            )
        finally:
            self._running = None
            self._cancel.discard(run_id)

    # --- read ------------------------------------------------------------

    def _build(self, s: StoredRun) -> BacktestRun:
        status = BacktestStatus(s.status)
        error = s.error
        if s.status in ACTIVE and s.id != self._running:
            status, error = BacktestStatus.FAILED, "interrupted: the API restarted before the run finished"
        integrity = "NOT_APPLICABLE"
        result = None
        if s.result is not None:
            ok = s.result_hash == result_hash(s.id, s.request, s.result, s.strategy_version)
            integrity = "VERIFIED" if ok else "TAMPERED"
            if ok:
                try:
                    result = BacktestResult.model_validate(s.result)
                except ValidationError:
                    integrity, error = (
                        "UNREADABLE",
                        "the stored result uses an older format and cannot be shown",
                    )
        progress = BacktestProgress.model_validate(s.progress)
        return BacktestRun(
            id=s.id,
            status=status,
            request=BacktestRequest.model_validate(s.request),
            progress=progress,
            error="the stored result failed its integrity check" if integrity == "TAMPERED" else error,
            result=result,
            created_at=s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=UTC),
            started_at=s.started_at,
            finished_at=s.finished_at,
            integrity=integrity,
            authority="RESEARCH_ONLY",
            strategy_version=s.strategy_version,
        )

    async def get(self, run_id: str) -> BacktestRun:
        await self._require()
        stored = await asyncio.to_thread(self._store.get, run_id)
        if stored is None:
            raise BacktestNotFoundError(run_id)
        return self._build(stored)

    async def list_runs(self, limit: int = 50) -> BacktestListResponse:
        now = self._clock()
        info = await self.info()
        if not info.available:
            return BacktestListResponse(runs=[], store=info, generated_at=now)
        rows = []
        for s in await asyncio.to_thread(self._store.recent, limit):
            run = self._build(s)
            variants = run.result.variants if run.result else []
            rows.append(
                BacktestRunRow(
                    id=run.id,
                    symbol=run.request.symbol,
                    start=run.request.start,
                    end=run.request.end,
                    status=run.status,
                    pct=run.progress.pct,
                    variants=[v.name for v in run.request.variants],
                    closed_trades={v.variant.name: v.funnel.closed for v in variants},
                    net_total_r={v.variant.name: v.stats.total_r for v in variants},
                    created_at=run.created_at,
                    strategy_version=run.strategy_version,
                )
            )
        return BacktestListResponse(runs=rows, store=info, generated_at=now)

    async def latest_completed(self, symbol: str) -> BacktestRun | None:
        for s in await asyncio.to_thread(self._store.recent, 50):
            if s.symbol == symbol and s.status == BacktestStatus.COMPLETED.value:
                run = self._build(s)
                if run.result is not None:
                    return run
        return None

    async def delete(self, run_id: str) -> None:
        await self._require()
        if run_id == self._running:
            raise BacktestRequestError("cancel the running backtest before deleting it")
        if not await asyncio.to_thread(self._store.delete, run_id):
            raise BacktestNotFoundError(run_id)
