"""No Wick service: API analysis and decision context. No Wick never authorizes LONG/SHORT on its own."""

from __future__ import annotations

from datetime import datetime

from app.contracts import load_spec
from app.domain.enums import NoWickStrength, Timeframe
from app.services.analysis.pipeline import load_inputs, run_pipeline
from app.services.candles.service import CandleService
from app.services.liquidity.models import LiquidityConfig
from app.services.no_wick.models import NoWickAnalysis, NoWickConfig
from app.services.pd_arrays.models import PdArrayConfig
from app.services.structure.models import StructureConfig

DEFAULT_LIMIT = 300


class NoWickService:
    def __init__(
        self,
        candles: CandleService,
        structure_cfg: StructureConfig | None = None,
        liquidity_cfg: LiquidityConfig | None = None,
        pd_cfg: PdArrayConfig | None = None,
        cfg: NoWickConfig | None = None,
    ) -> None:
        self._candles = candles
        self._s_cfg = structure_cfg or StructureConfig.from_spec()
        self._l_cfg = liquidity_cfg or LiquidityConfig.from_spec()
        self._pd_cfg = pd_cfg or PdArrayConfig.from_spec()
        self._cfg = cfg or NoWickConfig.from_spec()
        self._tf = Timeframe(load_spec("no_wick")["decisionContext"]["timeframe"])

    async def analyze(self, symbol: str, timeframe: Timeframe, limit: int | None = None) -> NoWickAnalysis:
        now = self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, timeframe, limit or DEFAULT_LIMIT, now, self._l_cfg
        )
        return run_pipeline(
            series, d1, self._s_cfg, self._l_cfg, self._pd_cfg, self._cfg, sessions=sessions
        ).no_wick

    async def decision_context(self, symbol: str, now: datetime | None = None) -> dict[str, object] | None:
        """Latest MEANINGFUL+ no-wick event on the confirmation timeframe, from eligible data only."""
        now = now or self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, self._tf, DEFAULT_LIMIT, now, self._l_cfg
        )
        analysis = run_pipeline(
            series, d1, self._s_cfg, self._l_cfg, self._pd_cfg, self._cfg, sessions=sessions
        ).no_wick
        if not analysis.eligible_for_decision:
            return None
        meaningful = [e for e in analysis.events if e.strength.rank >= NoWickStrength.MEANINGFUL.rank]
        if not meaningful:
            return None
        e = meaningful[-1]
        zone = next((z for z in analysis.zones if z.id == e.zone_id), None)
        return {
            "timeframe": self._tf.value,
            "time": e.time.isoformat(),
            "direction": e.direction.value,
            "classification": e.classification.value,
            "strength": e.strength.value,
            "candleQualityScore": e.candle_quality_score,
            "contextScore": e.context_score,
            "relevanceScore": e.relevance_score,
            "zoneState": zone.state.value if zone else None,
            "authority": "CONTEXT_ONLY",
        }
