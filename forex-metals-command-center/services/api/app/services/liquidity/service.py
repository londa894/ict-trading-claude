"""Liquidity service: API analysis and decision context (primary/secondary DOL, latest liquidity event)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.enums import DolConfidence, LiquidityEventType, Timeframe
from app.services.analysis.pipeline import load_inputs, run_pipeline
from app.services.candles.service import CandleService
from app.services.liquidity.models import DolTarget, LiquidityAnalysis, LiquidityConfig, LiquidityEvent
from app.services.structure.models import StructureConfig

DEFAULT_LIMIT = 300
DECISION_EVENTS = frozenset(
    {
        LiquidityEventType.SWEEP,
        LiquidityEventType.BREAK,
        LiquidityEventType.RUN,
        LiquidityEventType.RECLAIM,
        LiquidityEventType.SWEEP_FAILED,
    }
)


@dataclass(frozen=True)
class LiquidityContext:
    eligible: bool
    primary_dol: str | None
    secondary_dol: str | None
    dol_confidence: DolConfidence | None
    liquidity_event: str | None

    @property
    def dol_unclear(self) -> bool:
        return self.eligible and self.dol_confidence is DolConfidence.UNCLEAR


def describe_target(tf: Timeframe, t: DolTarget) -> str:
    return f"{tf.value} {t.side.value} {t.label} @ {t.price} (magnet {t.magnet_score}, {t.distance_atr} ATR)"


def describe_liquidity_event(tf: Timeframe, e: LiquidityEvent) -> str:
    return f"{tf.value} {e.type.value} {e.side.value} {e.pool_type.value} @ {e.price} ({e.time.isoformat()})"


class LiquidityService:
    def __init__(
        self,
        candles: CandleService,
        structure_cfg: StructureConfig | None = None,
        cfg: LiquidityConfig | None = None,
    ) -> None:
        self._candles = candles
        self._s_cfg = structure_cfg or StructureConfig.from_spec()
        self._cfg = cfg or LiquidityConfig.from_spec()
        ctx = load_spec("liquidity")["decisionContext"]
        self._dol_tf = Timeframe(ctx["dolTimeframe"])
        self._event_tf = Timeframe(ctx["eventTimeframe"])

    async def analyze(self, symbol: str, timeframe: Timeframe, limit: int | None = None) -> LiquidityAnalysis:
        now = self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, timeframe, limit or DEFAULT_LIMIT, now, self._cfg
        )
        return run_pipeline(series, d1, self._s_cfg, self._cfg, sessions=sessions).liquidity

    async def decision_context(self, symbol: str, now: datetime | None = None) -> LiquidityContext:
        now = now or self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, self._dol_tf, DEFAULT_LIMIT, now, self._cfg
        )
        dol_analysis = run_pipeline(series, d1, self._s_cfg, self._cfg, sessions=sessions).liquidity
        event_series = await self._candles.load_series(symbol, self._event_tf, DEFAULT_LIMIT, now)
        event_analysis = run_pipeline(event_series, d1, self._s_cfg, self._cfg, sessions=sessions).liquidity

        eligible = dol_analysis.eligible_for_decision and dol_analysis.dol is not None
        dol = dol_analysis.dol if eligible else None
        event: str | None = None
        if event_analysis.eligible_for_decision:
            relevant = [e for e in event_analysis.events if e.type in DECISION_EVENTS]
            if relevant:
                event = describe_liquidity_event(self._event_tf, relevant[-1])
        return LiquidityContext(
            eligible=eligible,
            primary_dol=describe_target(self._dol_tf, dol.primary) if dol and dol.primary else None,
            secondary_dol=describe_target(self._dol_tf, dol.secondary) if dol and dol.secondary else None,
            dol_confidence=dol.confidence if dol else None,
            liquidity_event=event,
        )
