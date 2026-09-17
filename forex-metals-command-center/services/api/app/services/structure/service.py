"""Structure service: per-timeframe analysis, MTF alignment and decision context.

Structure is computed only from validated series:
- INVALID / DISCONNECTED data -> no analysis at all.
- STALE or SYNTHETIC data -> analysis is returned (for display, clearly labelled) but is NOT eligible
  to feed the Master Decision.
Per-timeframe analysis goes through the shared structure -> liquidity pipeline so structure events carry
their liquidity qualifier. Alignment only needs states and skips liquidity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec, strategy_version
from app.domain.enums import HtfBias, StructureEventStatus, Timeframe
from app.services.analysis.pipeline import load_inputs, run_pipeline
from app.services.candles.service import CandleService
from app.services.liquidity.models import LiquidityConfig
from app.services.structure.analysis import (
    analyze_series,
    classify_alignment,
    classify_htf_bias,
    describe_event,
)
from app.services.structure.models import (
    MtfStructureResponse,
    StructureAnalysis,
    StructureConfig,
    TimeframeStructureState,
)


@dataclass(frozen=True)
class DecisionContext:
    htf_bias: HtfBias
    structure_event: str | None


class StructureService:
    def __init__(
        self,
        candles: CandleService,
        cfg: StructureConfig | None = None,
        liquidity_cfg: LiquidityConfig | None = None,
    ) -> None:
        self._candles = candles
        self._cfg = cfg or StructureConfig.from_spec()
        self._l_cfg = liquidity_cfg or LiquidityConfig.from_spec()
        ctx = load_spec("structure")["decisionContext"]
        self._htf = [Timeframe(t) for t in ctx["htfTimeframes"]]
        self._confirmation = Timeframe(ctx["confirmationTimeframe"])
        self._alignment_tfs = [Timeframe(t) for t in ctx["alignmentTimeframes"]]
        self._limits = {Timeframe(k): int(v) for k, v in ctx["limits"].items() if not k.startswith("$")}

    def _limit_for(self, timeframe: Timeframe) -> int:
        return self._limits.get(timeframe, 300)

    async def analyze(self, symbol: str, timeframe: Timeframe, limit: int | None = None) -> StructureAnalysis:
        now = self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, timeframe, limit or self._limit_for(timeframe), now, self._l_cfg
        )
        return run_pipeline(series, d1, self._cfg, self._l_cfg, sessions=sessions).structure

    async def alignment(self, symbol: str) -> MtfStructureResponse:
        now = self._candles.now()
        analyses = [
            analyze_series(await self._candles.load_series(symbol, tf, self._limit_for(tf), now), self._cfg)
            for tf in self._alignment_tfs
        ]
        by_tf = {a.timeframe: a for a in analyses}
        return MtfStructureResponse(
            symbol=symbol.upper(),
            alignment=classify_alignment([a.external.state if a.external else None for a in analyses]),
            htf_bias=classify_htf_bias([by_tf[tf] for tf in self._htf]),
            eligible_for_decision=all(a.eligible_for_decision for a in analyses),
            timeframes=[
                TimeframeStructureState(
                    timeframe=a.timeframe,
                    state=a.external.state if a.external else None,
                    trend=a.external.trend if a.external else None,
                    quality=a.quality,
                    eligible_for_decision=a.eligible_for_decision,
                    ineligibility=a.ineligibility,
                )
                for a in analyses
            ],
            strategy_version=strategy_version(),
            generated_at=now,
        )

    async def decision_context(self, symbol: str, now: datetime | None = None) -> DecisionContext:
        now = now or self._candles.now()
        htf = [
            analyze_series(await self._candles.load_series(symbol, tf, self._limit_for(tf), now), self._cfg)
            for tf in self._htf
        ]
        bias = classify_htf_bias(htf)
        series, d1, sessions = await load_inputs(
            self._candles, symbol, self._confirmation, self._limit_for(self._confirmation), now, self._l_cfg
        )
        confirmation = run_pipeline(series, d1, self._cfg, self._l_cfg, sessions=sessions).structure
        event: str | None = None
        if confirmation.eligible_for_decision:
            confirmed = [e for e in confirmation.events if e.status is StructureEventStatus.CONFIRMED]
            if confirmed:
                event = describe_event(self._confirmation, confirmed[-1])
        return DecisionContext(htf_bias=bias, structure_event=event)
