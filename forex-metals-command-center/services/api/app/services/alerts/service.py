"""Alert monitor: re-evaluates monitored symbols when a new execution candle closes (or after a quiet period),
derives alerts from state changes, and keeps ALERT_ME_WHEN_READY watches up to date.

Each symbol is isolated: a failure raises one MONITOR_FAILURE alert for that symbol (with cooldown) and the
cycle goes on. Ready watches and alerts live in process memory (lost on restart) until a database is used.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.contracts import load_spec
from app.domain.enums import AlertPriority, AlertType, Direction, ReadyWatchState, Timeframe
from app.domain.instrument import get_instrument
from app.services.alerts.models import (
    PRIORITY_ORDER,
    Alert,
    AlertCandidate,
    AlertConfig,
    AlertFeed,
    MonitorStatus,
    ReadyWatch,
    SymbolMemory,
)
from app.services.alerts.ready import checklist, next_required
from app.services.alerts.rules import Snapshot, derive
from app.services.alerts.store import AlertStore
from app.services.candles.service import CandleService, UnknownSymbolError
from app.services.market_state.service import MarketStateService
from app.services.scoring.service import EvaluationService

logger = logging.getLogger("fmcc.alerts")
NOT_AUTHORIZED = "NOT_AUTHORIZED"


class ReadyWatchLimitError(ValueError):
    pass


class AlertService:
    def __init__(
        self,
        candles: CandleService,
        market_state: MarketStateService,
        evaluation: EvaluationService,
        clock: Callable[[], datetime] | None = None,
        cfg: AlertConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        enabled: bool = True,
    ) -> None:
        self._candles = candles
        self._market_state = market_state
        self._evaluation = evaluation
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or AlertConfig.from_spec()
        self._monotonic = monotonic
        self._enabled = enabled
        self.store = AlertStore(self._cfg)
        self._memory: dict[str, SymbolMemory] = {}
        self._last_bar: dict[str, datetime | None] = {}
        self._last_full: dict[str, float] = {}
        self._watches: dict[str, ReadyWatch] = {}
        self._cycles = 0
        self._last_cycle_at: datetime | None = None
        self._last_cycle_ms: int | None = None
        self._last_error: str | None = None
        self._running = False
        self._lock = asyncio.Lock()

    # --- monitored symbols & status -------------------------------------------------------------------------

    def monitored_symbols(self) -> list[str]:
        out = list(self._cfg.monitored_symbols)
        for w in self._watches.values():
            if w.symbol not in out:
                out.append(w.symbol)
        return out[: self._cfg.max_monitored_symbols]

    def status(self) -> MonitorStatus:
        return MonitorStatus(
            enabled=self._enabled,
            running=self._running,
            symbols=self.monitored_symbols(),
            poll_seconds=self._cfg.poll_seconds,
            max_quiet_seconds=self._cfg.max_quiet_seconds,
            cycles=self._cycles,
            last_cycle_at=self._last_cycle_at,
            last_cycle_ms=self._last_cycle_ms,
            last_error=self._last_error,
            baselined=sorted(self._memory),
        )

    def feed(
        self,
        *,
        since: int = 0,
        symbol: str | None = None,
        category: str | None = None,
        min_priority: AlertPriority | None = None,
        limit: int = 100,
    ) -> AlertFeed:
        alerts: list[Alert] = []
        for a in self.store.list():
            if (
                a.seq <= since
                or (symbol and a.symbol != symbol.upper())
                or (category and a.category.value != category)
            ):
                continue
            if min_priority is not None and PRIORITY_ORDER.index(a.priority) < PRIORITY_ORDER.index(
                min_priority
            ):
                continue
            alerts.append(a)
            if len(alerts) >= limit:
                break
        return AlertFeed(
            alerts=alerts,
            next_cursor=max((a.seq for a in alerts), default=since),
            suppressed=dict(self.store.suppressed),
            monitor=self.status(),
            authority=NOT_AUTHORIZED,
            generated_at=self._clock(),
        )

    # --- ready watches --------------------------------------------------------------------------------------

    def watches(self) -> list[ReadyWatch]:
        return sorted(self._watches.values(), key=lambda w: w.created_at)

    def add_watch(self, symbol: str, direction: Direction | None) -> ReadyWatch:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnknownSymbolError(symbol)
        if len(self._watches) >= self._cfg.max_ready_watches:
            raise ReadyWatchLimitError(f"at most {self._cfg.max_ready_watches} ready watches")
        symbols = self.monitored_symbols()
        if instrument.symbol not in symbols and len(symbols) >= self._cfg.max_monitored_symbols:
            raise ReadyWatchLimitError(f"at most {self._cfg.max_monitored_symbols} monitored symbols")
        for w in self._watches.values():
            if (
                w.symbol == instrument.symbol
                and w.direction == direction
                and w.state is not ReadyWatchState.FIRED
            ):
                return w  # idempotent
        watch = ReadyWatch(
            id=f"WATCH:{uuid.uuid4().hex[:12]}",
            symbol=instrument.symbol,
            direction=direction,
            state=ReadyWatchState.WAITING,
            conditions=[],
            next_required_event="waiting for the next monitor cycle",
            created_at=self._clock(),
            last_checked_at=None,
            fired_at=None,
        )
        self._watches[watch.id] = watch
        return watch

    def remove_watch(self, watch_id: str) -> bool:
        return self._watches.pop(watch_id, None) is not None

    # --- monitor --------------------------------------------------------------------------------------------

    async def run_forever(self) -> None:
        self._running = True
        try:
            while True:
                await self.cycle()
                await asyncio.sleep(self._cfg.poll_seconds)
        finally:
            self._running = False

    async def cycle(self, force: bool = False) -> list[Alert]:
        async with self._lock:
            started = self._monotonic()
            emitted: list[Alert] = []
            self._last_error = None
            for symbol in self.monitored_symbols():
                try:
                    if force or self._unchecked_watch(symbol) or await self._due(symbol):
                        emitted += await self._evaluate_symbol(symbol)
                except Exception as exc:
                    logger.exception("alert monitor failed for %s", symbol)
                    self._last_error = f"{symbol}: {type(exc).__name__}"
                    now = self._clock()
                    failure = AlertCandidate(
                        AlertType.MONITOR_FAILURE,
                        symbol,
                        "monitor",
                        f"{symbol} alert monitor failed",
                        "The monitor could not evaluate this market; alerts for it may be missing.",
                        now,
                    )
                    alert = self.store.add(failure, now)
                    if alert is not None:
                        emitted.append(alert)
            self._cycles += 1
            self._last_cycle_at = self._clock()
            self._last_cycle_ms = int((self._monotonic() - started) * 1000)
            return emitted

    def _unchecked_watch(self, symbol: str) -> bool:
        """A new ready watch is evaluated on the next cycle instead of waiting for a new bar."""
        return any(w.symbol == symbol and w.last_checked_at is None for w in self._watches.values())

    async def _due(self, symbol: str) -> bool:
        now = self._clock()
        series = await self._candles.load_series(symbol, Timeframe.M5, 3, now)
        closed = [c for c in series.candles if c.is_closed]
        bar = closed[-1].open_time if closed else None
        quiet = self._monotonic() - self._last_full.get(symbol, float("-inf"))
        changed = symbol not in self._last_bar or bar != self._last_bar[symbol]
        self._last_bar[symbol] = bar
        return changed or quiet >= self._cfg.max_quiet_seconds

    async def _evaluate_symbol(self, symbol: str) -> list[Alert]:
        now = self._clock()
        response = await self._market_state.evaluate(symbol)
        decision = response.decision
        if decision.symbol != symbol:
            raise RuntimeError("decision symbol mismatch")
        evaluation, run = await self._evaluation.evaluate_with_run(symbol, now)
        if evaluation.symbol != symbol or run.analysis.symbol != symbol:
            raise RuntimeError("evaluation symbol mismatch")
        self._last_full[symbol] = self._monotonic()
        candidates, memory = derive(
            Snapshot(symbol=symbol, now=now, decision=decision, evaluation=evaluation, run=run),
            self._memory.get(symbol),
            self._cfg,
        )
        self._memory[symbol] = memory
        emitted = [a for a in (self.store.add(c, now) for c in candidates) if a is not None]

        authority = str(load_spec("strategy_version")["verdictAuthority"])
        for watch in [w for w in self._watches.values() if w.symbol == symbol]:
            if watch.state is ReadyWatchState.FIRED:
                continue
            state, conditions, fire = checklist(decision, evaluation, run, watch.direction, authority)
            update: dict[str, object] = {
                "state": state,
                "conditions": conditions,
                "next_required_event": next_required(conditions),
                "last_checked_at": now,
            }
            if fire:
                update["fired_at"] = now
                side = watch.direction.value if watch.direction else "either direction"
                ready = AlertCandidate(
                    AlertType.READY,
                    symbol,
                    watch.id,
                    f"{symbol} READY: {decision.verdict.value}",
                    f"Every mandatory condition is met ({side}). Verdict {decision.verdict.value}.",
                    now,
                    direction=watch.direction,
                )
                alert = self.store.add(ready, now)
                if alert is not None:
                    emitted.append(alert)
            self._watches[watch.id] = watch.model_copy(update=update)
        return emitted
