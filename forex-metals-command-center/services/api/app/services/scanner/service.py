"""Scanner: the Master Decision of every requested symbol, isolated per symbol, cached briefly and ranked.

Isolation: each symbol is evaluated on its own; a failure or a decision for another symbol becomes that row's
UNAVAILABLE + SYSTEM_INTEGRITY_FAILURE and never touches other rows. Rows are never merged across symbols.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.contracts import strategy_version, verdict_authority
from app.domain.enums import Blocker, DataQuality, DecisionConfidence, MarketStatus, Verdict
from app.domain.instrument import Instrument, get_instrument, instrument_registry
from app.services.candles.service import UnknownSymbolError
from app.services.data_quality.market_hours import market_status_at
from app.services.market_state.service import MarketStateService
from app.services.scanner.models import RANKING_RULES, MarketRow, ScannerConfig, ScanResponse, ScanRow

logger = logging.getLogger("fmcc.scanner")
NOT_AUTHORIZED = "NOT_AUTHORIZED"


def default_symbols() -> list[str]:
    return [i.symbol for i in sorted(instrument_registry().values(), key=lambda i: (i.priority, i.symbol))]


def markets(now: datetime) -> list[MarketRow]:
    return [
        MarketRow(
            symbol=i.symbol,
            asset_class=i.asset_class,
            base=i.base,
            quote=i.quote,
            priority=i.priority,
            deeply_validated=i.deeply_validated,
            market_status=market_status_at(i.asset_class, now),
            position_size_status=i.position_size_status,
        )
        for i in (get_instrument(s) for s in default_symbols())
        if i is not None
    ]


def rank_key(row: ScanRow) -> tuple[object, ...]:
    return (
        row.verdict is Verdict.UNAVAILABLE,
        not row.deeply_validated,
        -row.setup_progress,
        row.setup_score is None,
        -(row.setup_score or 0.0),
        len(row.blockers),
        row.priority,
        row.symbol,
    )


class ScannerService:
    def __init__(
        self,
        market_state: MarketStateService,
        clock: Callable[[], datetime] | None = None,
        cfg: ScannerConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._market_state = market_state
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or ScannerConfig.from_spec()
        self._monotonic = monotonic
        self._cache: dict[str, tuple[float, ScanRow]] = {}
        self._lock = asyncio.Lock()

    @property
    def cfg(self) -> ScannerConfig:
        return self._cfg

    def resolve_symbols(self, symbols: Sequence[str] | None) -> list[str]:
        if not symbols:
            return default_symbols()[: self._cfg.max_symbols]
        out: list[str] = []
        for raw in symbols:
            s = raw.strip().upper()
            if get_instrument(s) is None:
                raise UnknownSymbolError(raw)
            if s not in out:
                out.append(s)
        if len(out) > self._cfg.max_symbols:
            raise ValueError(f"at most {self._cfg.max_symbols} symbols per scan")
        return out

    async def scan(
        self,
        symbols: Sequence[str] | None = None,
        *,
        min_score: float | None = None,
        only_setups: bool = False,
    ) -> ScanResponse:
        requested = self.resolve_symbols(symbols)
        started = self._monotonic()
        async with self._lock:  # one scan at a time: concurrent requests reuse the fresh cache
            rows = [await self._row(s) for s in requested]
        ranked = sorted(rows, key=rank_key)
        if min_score is not None:
            ranked = [r for r in ranked if r.setup_score is not None and r.setup_score >= min_score]
        if only_setups:
            ranked = [r for r in ranked if r.setup_progress > 0]
        numbered = [r.model_copy(update={"rank": i}) for i, r in enumerate(ranked, start=1)]
        return ScanResponse(
            rows=numbered,
            requested_symbols=requested,
            min_score=min_score,
            only_setups=only_setups,
            ranking=list(RANKING_RULES),
            cache_seconds=self._cfg.cache_seconds,
            duration_ms=int((self._monotonic() - started) * 1000),
            verdict_authority=verdict_authority(),
            authority=NOT_AUTHORIZED,
            strategy_version=strategy_version(),
            scanned_at=self._clock(),
        )

    async def _row(self, symbol: str) -> ScanRow:
        now = self._monotonic()
        cached = self._cache.get(symbol)
        if cached is not None and now - cached[0] < self._cfg.cache_seconds:
            return cached[1].model_copy(update={"cache_age_seconds": round(now - cached[0], 1)})
        instrument = get_instrument(symbol)
        assert instrument is not None  # resolved before
        row = await self._evaluate(instrument)
        self._cache[symbol] = (now, row)
        return row

    async def _evaluate(self, instrument: Instrument) -> ScanRow:
        try:
            response = await self._market_state.evaluate(instrument.symbol)
            d = response.decision
            if d.symbol != instrument.symbol:
                raise RuntimeError("decision symbol mismatch")  # isolation guard
        except Exception:
            logger.exception("scan failed for %s", instrument.symbol)
            return self._failed(instrument)
        return ScanRow(
            rank=0,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            priority=instrument.priority,
            deeply_validated=instrument.deeply_validated,
            market_status=response.data.market_status,
            verdict=d.verdict,
            data_quality=d.data_quality,
            htf_bias=d.htf_bias,
            setup_state=d.setup_state,
            setup_type=d.setup_type,
            setup_progress=self._cfg.progress(d.setup_state) if d.verdict is not Verdict.UNAVAILABLE else 0,
            setup_score=d.setup_score,
            setup_grade=d.setup_grade,
            decision_confidence=d.decision_confidence,
            risk_status=d.risk_status,
            primary_dol=d.primary_dol,
            blockers=d.blockers,
            next_required_event=d.next_required_event,
            latest_closed_open_time=response.data.latest_closed_open_time,
            evaluated_at=d.updated_at,
            cache_age_seconds=0.0,
            error=None,
        )

    def _failed(self, instrument: Instrument) -> ScanRow:
        return ScanRow(
            rank=0,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            priority=instrument.priority,
            deeply_validated=instrument.deeply_validated,
            market_status=MarketStatus.UNKNOWN,
            verdict=Verdict.UNAVAILABLE,
            data_quality=DataQuality.DISCONNECTED,
            htf_bias="UNKNOWN",
            setup_state="NOT_EVALUATED",
            setup_type=None,
            setup_progress=0,
            setup_score=None,
            setup_grade=None,
            decision_confidence=DecisionConfidence.LOW,
            risk_status="NOT_EVALUATED",
            primary_dol=None,
            blockers=[Blocker.SYSTEM_INTEGRITY_FAILURE],
            next_required_event="Investigate system integrity failure",
            latest_closed_open_time=None,
            evaluated_at=self._clock(),
            cache_age_seconds=0.0,
            error="scan failed",
        )
