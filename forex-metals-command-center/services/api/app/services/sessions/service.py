"""Session service: API analysis and decision context. Time never creates a trade by itself."""

from __future__ import annotations

import logging
from datetime import datetime

from app.domain.enums import SessionInstanceState, SessionName, Timeframe
from app.domain.instrument import get_instrument
from app.services.candles.service import CandleService, UnknownSymbolError
from app.services.sessions.analysis import analyze_sessions
from app.services.sessions.models import SessionAnalysis, SessionConfig

logger = logging.getLogger("fmcc.sessions")


class SessionService:
    def __init__(self, candles: CandleService, cfg: SessionConfig | None = None) -> None:
        self._candles = candles
        self._cfg = cfg or SessionConfig.from_spec()

    async def analyze(self, symbol: str, now: datetime | None = None) -> SessionAnalysis:
        now = now or self._candles.now()
        instrument = get_instrument(symbol.upper())
        if instrument is None:
            raise UnknownSymbolError(symbol)
        source = await self._candles.load_series(
            symbol, self._cfg.source_timeframe, self._cfg.source_bars, now
        )
        d1 = await self._candles.load_series(symbol, Timeframe.D1, self._cfg.adr.period_days + 5, now)
        try:
            return analyze_sessions(source, d1, instrument.asset_class, self._cfg)
        except Exception:
            logger.exception("session analysis failed for %s", symbol)
            return analyze_sessions(source, d1, instrument.asset_class, self._cfg, failed=True)

    async def decision_context(self, symbol: str, now: datetime | None = None) -> dict[str, object] | None:
        """Session context for MasterDecision.sessionState, from eligible data only."""
        a = await self.analyze(symbol, now)
        if not a.eligible_for_decision:
            return None
        asia = [
            i
            for i in a.instances
            if i.session is SessionName.ASIA and i.state is SessionInstanceState.COMPLETE
        ]
        latest_asia = asia[-1] if asia else None
        judas = a.judas[-1] if a.judas else None
        return {
            "tradingDay": a.clock.trading_day.isoformat(),
            "marketStatus": a.clock.market_status.value,
            "activeSessions": [s.value for s in a.clock.active_sessions],
            "activeKillZones": [k.value for k in a.clock.active_kill_zones],
            "timeQuality": a.clock.time_quality.value,
            "sessionQuality": a.session_quality.value if a.session_quality else None,
            "asianRangeState": latest_asia.asian_range_state.value
            if latest_asia and latest_asia.asian_range_state
            else None,
            "adrPctUsed": a.adr.pct_used if a.adr else None,
            "expansion": a.adr.expansion.value if a.adr and a.adr.expansion else None,
            "judas": f"{judas.session.value} {judas.direction.value} {judas.status.value}" if judas else None,
            "authority": "CONTEXT_ONLY",
        }
