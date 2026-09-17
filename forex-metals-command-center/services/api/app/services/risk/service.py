"""Risk service: the manual account profile applied to a symbol and its confirmed plan; what-if sizing."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from app.domain.candle import Candle
from app.domain.enums import NewsState, RiskStatus, RiskWarning
from app.domain.instrument import InstrumentSpec, get_instrument
from app.services.candles.service import UnknownSymbolError
from app.services.risk.engine import assess, not_assessed
from app.services.risk.models import (
    ConversionRate,
    RiskAssessment,
    RiskCalculationRequest,
    RiskConfig,
    TradeInput,
)
from app.services.risk.store import ProfileLoadStatus, RiskProfileStore
from app.services.sessions.clock import trading_day_of

logger = logging.getLogger("fmcc.risk")


class RiskService:
    def __init__(self, store: RiskProfileStore, cfg: RiskConfig | None = None) -> None:
        self._store = store
        self._cfg = cfg or RiskConfig.from_spec()

    @property
    def cfg(self) -> RiskConfig:
        return self._cfg

    def assess(
        self,
        symbol: str,
        now: datetime,
        trade: TradeInput | None = None,
        candles: Sequence[Candle] | None = None,
        news_state: NewsState | None = None,
    ) -> RiskAssessment:
        """Never raises for a known symbol: any failure is UNAVAILABLE (fail safe, blocks the decision)."""
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnknownSymbolError(symbol)
        loaded = self._store.load()
        if loaded.status is ProfileLoadStatus.MISSING:
            return self.with_news(
                not_assessed(symbol, now, RiskStatus.NOT_CONFIGURED, loaded.error), news_state
            )
        if loaded.status is ProfileLoadStatus.INVALID or loaded.file is None:
            return self.with_news(
                not_assessed(symbol, now, RiskStatus.INVALID_PROFILE, loaded.error), news_state
            )
        f = loaded.file
        user_spec = f.instrument_specs.get(instrument.symbol)
        try:
            return self.with_news(
                assess(
                    instrument.symbol,
                    now,
                    account=f.account,
                    state=f.state,
                    spec=instrument.spec or user_spec,
                    spec_verified=instrument.spec is not None,
                    rates=f.conversion_rates,
                    trade=trade,
                    candles=candles if candles is not None else [],
                    trading_day=trading_day_of(now),
                    cfg=self._cfg,
                ),
                news_state,
            )
        except Exception:
            logger.exception("risk assessment failed for %s", symbol)
            return self.with_news(
                not_assessed(symbol, now, RiskStatus.UNAVAILABLE, "risk assessment failed"), news_state
            )

    @staticmethod
    def with_news(assessment: RiskAssessment, news_state: NewsState | None) -> RiskAssessment:
        """Report the news gate's state; the news veto itself is applied by the evaluation."""
        if news_state is None:
            return assessment
        warnings: list[RiskWarning] = [
            w for w in assessment.warnings if w is not RiskWarning.NEWS_NOT_EVALUATED
        ]
        if news_state is NewsState.UNAVAILABLE:
            warnings.append(RiskWarning.NEWS_NOT_EVALUATED)
        return assessment.model_copy(update={"news": news_state.value, "warnings": warnings})

    def user_spec(self, symbol: str) -> InstrumentSpec | None:
        loaded = self._store.load()
        return loaded.file.instrument_specs.get(symbol.upper()) if loaded.file is not None else None

    def calculate(self, request: RiskCalculationRequest, now: datetime) -> RiskAssessment:
        """What-if sizing from request values only. Stores and logs nothing; volatility is not checked."""
        instrument = get_instrument(request.symbol)
        if instrument is None:
            raise UnknownSymbolError(request.symbol)
        spec = instrument.spec or request.instrument_spec
        if spec is not None and spec.symbol.upper() != instrument.symbol:
            raise ValueError("the instrument spec is for a different symbol")
        rates: list[ConversionRate] = []
        quote = spec.quote_currency if spec else instrument.quote
        if request.conversion_rate is not None:
            rates.append(
                ConversionRate(
                    base=quote, quote=request.account.currency, rate=request.conversion_rate, as_of=now
                )
            )
        return assess(
            instrument.symbol,
            now,
            account=request.account,
            state=request.state,
            spec=spec,
            spec_verified=instrument.spec is not None,
            rates=rates,
            trade=TradeInput(direction=request.direction, entry=request.entry, stop=request.stop),
            candles=None,
            trading_day=trading_day_of(now),
            cfg=self._cfg,
        )
