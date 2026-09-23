"""Reversal setup service: assembles HTF origins + decision-timeframe inputs and runs the engine.

Read-only. The `enabled` flag is surfaced but the service always computes the setups (so they can be
observed); it is the verdict layer that must ignore reversals while the flag is off.
"""

from __future__ import annotations

from datetime import datetime

from app.contracts import load_spec, strategy_version
from app.domain.enums import PdArrayType, Timeframe
from app.services.analysis.pipeline import load_inputs, run_pipeline
from app.services.candles.service import CandleService
from app.services.liquidity.models import LiquidityConfig
from app.services.no_wick.models import NoWickConfig
from app.services.pd_arrays.models import PdArrayConfig
from app.services.reversal.engine import analyze_reversals
from app.services.reversal.models import ReversalAnalysis, ReversalConfig, ReversalSetup
from app.services.reversal.origins import build_origins
from app.services.structure.analysis import classify_htf_bias
from app.services.structure.models import StructureConfig
from app.services.structure.swings import atr_at, true_ranges

DEFAULT_LIMIT = 300
# The furthest-advanced non-terminal state wins as `current`.
_STATE_RANK = {
    "BLOCKED": 6,
    "ENTRY_ZONE_TOUCHED": 5,
    "SETUP_ARMED": 4,
    "CONFIRMATION": 3,
    "REACTION": 2,
    "REBALANCE_TOUCH": 1,
    "REBALANCE_WATCH": 0,
    "DISCOVERED": 0,
}


class ReversalService:
    def __init__(
        self,
        candles: CandleService,
        structure_cfg: StructureConfig | None = None,
        liquidity_cfg: LiquidityConfig | None = None,
        pd_cfg: PdArrayConfig | None = None,
        no_wick_cfg: NoWickConfig | None = None,
        cfg: ReversalConfig | None = None,
    ) -> None:
        self._candles = candles
        self._s_cfg = structure_cfg or StructureConfig.from_spec()
        self._l_cfg = liquidity_cfg or LiquidityConfig.from_spec()
        self._pd_cfg = pd_cfg or PdArrayConfig.from_spec()
        self._nw_cfg = no_wick_cfg or NoWickConfig.from_spec()
        self._cfg = cfg or ReversalConfig.from_spec()
        self._decision_tf = Timeframe(load_spec("no_wick")["decisionContext"]["timeframe"])

    @property
    def enabled(self) -> bool:
        """The A/B master switch. False = the reversal never reaches the live verdict."""
        return self._cfg.enabled

    async def _pipeline(self, symbol: str, tf: Timeframe, now: datetime, limit: int):  # type: ignore[no-untyped-def]
        series, d1, sessions = await load_inputs(self._candles, symbol, tf, limit, now, self._l_cfg)
        result = run_pipeline(
            series, d1, self._s_cfg, self._l_cfg, self._pd_cfg, self._nw_cfg, sessions=sessions
        )
        return series, result

    async def analyze(self, symbol: str, now: datetime | None = None) -> ReversalAnalysis:
        now = now or self._candles.now()
        # Origins + bias from the HTF timeframes (D1/H4/H1).
        nw_by_tf: dict[Timeframe, list] = {}
        imr_by_tf: dict[Timeframe, list] = {}
        structures = []
        for tf in self._cfg.origin_timeframes:
            _, r = await self._pipeline(symbol, tf, now, DEFAULT_LIMIT)
            structures.append(r.structure)
            nw_by_tf[tf] = [z for z in r.no_wick.zones if z.active] if r.no_wick.eligible_for_decision else []
            imr_by_tf[tf] = (
                [z for z in r.pd_arrays.zones if z.type is PdArrayType.IMR and z.active]
                if r.pd_arrays.eligible_for_decision
                else []
            )
        bias = classify_htf_bias(structures)
        origins = build_origins(nw_by_tf, imr_by_tf, self._cfg)

        # Decision-timeframe inputs: confirmation (pd), target (liquidity), reaction (displacement), atr.
        dseries, dr = await self._pipeline(symbol, self._decision_tf, now, DEFAULT_LIMIT)
        closed = [c for c in dseries.candles if c.is_closed]
        as_of = max((c.close_time for c in closed), default=None)
        reasons = list(dr.structure.ineligibility)
        atr = atr_at(true_ranges(closed), len(closed) - 1, self._pd_cfg.atr_period) if closed else 0.0

        result = analyze_reversals(
            origins,
            closed,
            bias,
            dr.pd_arrays.displacements if dr.pd_arrays.eligible_for_decision else [],
            self._cfg,
            pd_zones=dr.pd_arrays.zones,
            pd_events=dr.pd_arrays.events,
            liquidity_pools=dr.liquidity.pools,
            atr=atr,
        )
        current = _pick_current(result.setups)
        return ReversalAnalysis(
            symbol=symbol.upper(),
            timeframe=self._decision_tf,
            as_of=as_of,
            candle_count=len(closed),
            quality=dseries.quality,
            is_synthetic=dseries.is_synthetic,
            enabled=self._cfg.enabled,
            eligible_for_decision=not reasons and as_of is not None,
            ineligibility=reasons,
            bias=bias,
            origin_count=len(origins),
            current=current,
            setups=result.setups,
            events=result.events,
            provider_error=dseries.provider_error,
            strategy_version=strategy_version(),
            generated_at=now,
        )


def _pick_current(setups: list[ReversalSetup]) -> ReversalSetup | None:
    live = [s for s in setups if not s.terminal]
    if not live:
        return None
    return max(live, key=lambda s: (_STATE_RANK.get(s.state.value, -1), s.state_changed_at))
