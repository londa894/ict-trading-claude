"""Macro service: provider snapshot + market D1 closes -> assessment. Failures are UNAVAILABLE (unscored)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.enums import Direction, Timeframe
from app.domain.instrument import get_instrument
from app.services.candles.service import CandleService, UnknownSymbolError
from app.services.macro.engine import assess
from app.services.macro.models import MacroAssessment, MacroConfig, MacroSeriesResponse
from app.services.macro.providers import MacroProvider, MacroUnavailableError
from app.services.news.models import NewsAssessment
from app.services.sessions.clock import trading_day_of
from app.services.timeframes.core import NEW_YORK

logger = logging.getLogger("fmcc.macro")
D1_BARS = 80


class MacroService:
    def __init__(
        self,
        provider: MacroProvider,
        candles: CandleService,
        clock: Callable[[], datetime] | None = None,
        cfg: MacroConfig | None = None,
    ) -> None:
        self._provider = provider
        self._candles = candles
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cfg = cfg or MacroConfig.from_spec()

    @property
    def cfg(self) -> MacroConfig:
        return self._cfg

    async def series(self) -> MacroSeriesResponse:
        now = self._clock()
        empty = {"provider": self._provider.name, "is_synthetic": self._provider.is_synthetic, "series": []}
        try:
            snapshot = await self._provider.snapshot(now)
        except MacroUnavailableError as exc:
            return MacroSeriesResponse(
                **empty, available=False, reason=str(exc), source=None, fetched_at=None, generated_at=now
            )
        return MacroSeriesResponse(
            provider=snapshot.provider,
            is_synthetic=snapshot.is_synthetic,
            available=True,
            reason=None,
            source=snapshot.source,
            fetched_at=snapshot.fetched_at,
            series=list(snapshot.series.values()),
            generated_at=now,
        )

    async def assess(
        self,
        symbol: str,
        now: datetime | None = None,
        *,
        direction: Direction | None = None,
        news: NewsAssessment | None = None,
    ) -> MacroAssessment:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnknownSymbolError(symbol)
        now = now or self._clock()
        today = now.astimezone(NEW_YORK).date()
        snapshot, reason = None, None
        try:
            snapshot = await self._provider.snapshot(now)
        except MacroUnavailableError as exc:
            reason = str(exc)
        except Exception as exc:
            logger.exception("macro provider failed")
            reason = f"macro provider failed ({type(exc).__name__})"
        closes: list[tuple[datetime, float]] = []
        try:
            d1 = await self._candles.load_series(instrument.symbol, Timeframe.D1, D1_BARS, now)
            closes = [(c.open_time, c.close) for c in d1.candles if c.is_closed]
        except Exception:
            logger.exception("macro D1 load failed for %s", symbol)
        market = [(trading_day_of(t), close) for t, close in closes]
        try:
            return assess(
                instrument.symbol,
                now,
                today,
                snapshot,
                reason,
                self._cfg,
                self._provider.name,
                direction,
                market,
                news,
            )
        except Exception:
            logger.exception("macro assessment failed for %s", symbol)
            return assess(
                instrument.symbol,
                now,
                today,
                None,
                "macro assessment failed",
                self._cfg,
                self._provider.name,
                direction,
            )


def decision_context(m: MacroAssessment) -> dict[str, object]:
    """Compact MasterDecision.macroState (context: modifies score/confidence, never the verdict)."""
    return {
        "authority": "CONTEXT_ONLY",
        "state": m.state.value if m.state else None,
        "bias": m.bias.value,
        "score": m.score,
        "available": m.available,
        "synthetic": m.is_synthetic,
        "correlationRegime": m.correlation.regime.value if m.correlation else None,
        "drivers": [
            {
                "series": d.series,
                "direction": d.direction.value if d.direction else None,
                "contribution": d.contribution,
            }
            for d in m.drivers
        ],
    }
