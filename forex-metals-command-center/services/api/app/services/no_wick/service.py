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
# HTF bar counts are sized per timeframe: a weekly candle is derived H1 -> D1 -> W1, so a large
# W1 limit would demand tens of thousands of H1 bars. 64 weeks (the candle service's W1 cap) yields
# >50 closed weekly candles, enough to clear the structure minCandles gate for no-wick context.
_HTF_LIMITS: dict[Timeframe, int] = {Timeframe.W1: 64}


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

    async def htf_context(self, symbol: str, now: datetime | None = None) -> dict[str, object]:
        """No-wick across the tracked timeframes (D1/H4/H1/M15): latest MEANINGFUL+ event per timeframe.

        Read-only HTF context for the reversal playbook (REVERSAL_SETUP_SPEC.md). Never authorizes LONG/SHORT.
        """
        names = load_spec("no_wick")["decisionContext"].get("htfTimeframes", [self._tf.value])
        rows: list[dict[str, object]] = []
        for tf in (Timeframe(t) for t in names):
            analysis = await self.analyze(symbol, tf, _HTF_LIMITS.get(tf))
            events = list(analysis.events) if analysis.eligible_for_decision else []
            meaningful = [e for e in events if e.strength.rank >= NoWickStrength.MEANINGFUL.rank]
            latest = meaningful[-1] if meaningful else (events[-1] if events else None)
            zone = next((z for z in analysis.zones if latest and z.id == latest.zone_id), None)
            rows.append(
                {
                    "timeframe": tf.value,
                    "eligible": analysis.eligible_for_decision,
                    "meaningfulCount": len(meaningful),
                    "forming": (
                        analysis.forming.model_dump(by_alias=True, mode="json")
                        if analysis.forming is not None
                        else None
                    ),
                    "latest": (
                        {
                            "time": latest.time.isoformat(),
                            "direction": latest.direction.value,
                            "classification": latest.classification.value,
                            "strength": latest.strength.value,
                            "relevanceScore": latest.relevance_score,
                            "zoneState": zone.state.value if zone else None,
                        }
                        if latest is not None
                        else None
                    ),
                }
            )
        return {"symbol": symbol.upper(), "timeframes": rows, "authority": "CONTEXT_ONLY"}
