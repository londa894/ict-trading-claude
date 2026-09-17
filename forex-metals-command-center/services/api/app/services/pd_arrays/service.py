"""PD array service (Phase 4: displacement + FVG/IFVG): API analysis and decision context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.enums import Timeframe
from app.services.analysis.pipeline import load_inputs, run_pipeline
from app.services.candles.service import CandleService
from app.services.liquidity.models import LiquidityConfig
from app.services.pd_arrays.models import DisplacementEvent, PdArrayAnalysis, PdArrayConfig
from app.services.structure.models import StructureConfig

DEFAULT_LIMIT = 300


@dataclass(frozen=True)
class DisplacementContext:
    eligible: bool
    displacement: str | None


def describe_displacement(tf: Timeframe, d: DisplacementEvent) -> str:
    return (
        f"{tf.value} {d.direction.value} {d.grade.value} displacement {d.magnitude_atr} ATR "
        f"over {d.candle_count} candle(s) ({d.time.isoformat()})"
    )


class PdArrayService:
    def __init__(
        self,
        candles: CandleService,
        structure_cfg: StructureConfig | None = None,
        liquidity_cfg: LiquidityConfig | None = None,
        cfg: PdArrayConfig | None = None,
    ) -> None:
        self._candles = candles
        self._s_cfg = structure_cfg or StructureConfig.from_spec()
        self._l_cfg = liquidity_cfg or LiquidityConfig.from_spec()
        self._cfg = cfg or PdArrayConfig.from_spec()
        self._tf = Timeframe(load_spec("pd_arrays")["decisionContext"]["displacementTimeframe"])

    async def analyze(self, symbol: str, timeframe: Timeframe, limit: int | None = None) -> PdArrayAnalysis:
        now = self._candles.now()
        series, d1, sessions = await load_inputs(
            self._candles, symbol, timeframe, limit or DEFAULT_LIMIT, now, self._l_cfg
        )
        return run_pipeline(series, d1, self._s_cfg, self._l_cfg, self._cfg, sessions=sessions).pd_arrays

    async def decision_context(self, symbol: str, now: datetime | None = None) -> DisplacementContext:
        now = now or self._candles.now()
        series = await self._candles.load_series(symbol, self._tf, DEFAULT_LIMIT, now)
        analysis = run_pipeline(series, None, self._s_cfg, self._l_cfg, self._cfg).pd_arrays
        if not analysis.eligible_for_decision:
            return DisplacementContext(eligible=False, displacement=None)
        strong = [d for d in analysis.displacements if d.grade.rank >= self._cfg.qualifier_min_grade.rank]
        return DisplacementContext(
            eligible=True, displacement=describe_displacement(self._tf, strong[-1]) if strong else None
        )
