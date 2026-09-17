"""Engine-state capture shared by the journal and paper trading (the same engine code for LIVE / PAPER)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.enums import HtfBias
from app.services.market_state.service import MarketStateService
from app.services.scoring.service import EvaluationService

logger = logging.getLogger("fmcc.capture")


@dataclass(frozen=True)
class EngineState:
    decision: dict[str, Any]
    evaluation: dict[str, Any] | None  # None when the evaluation failed (treated as "no confirmed plan")
    data: dict[str, Any]


async def capture_engine_state(
    market_state: MarketStateService, evaluation: EvaluationService, symbol: str, now: datetime
) -> EngineState:
    market = await market_state.evaluate(symbol)
    ev_dump: dict[str, Any] | None = None
    try:
        bias = market.decision.htf_bias
        ev = await evaluation.evaluate(
            symbol,
            now,
            htf_bias=HtfBias(bias) if bias in HtfBias.__members__ else None,
            primary_dol=market.decision.primary_dol,
            resolve_context=False,
        )
        ev_dump = ev.model_dump(mode="json", by_alias=True)
    except Exception:
        logger.exception("engine snapshot evaluation failed for %s", symbol)
    return EngineState(
        decision=market.decision.model_dump(mode="json", by_alias=True),
        evaluation=ev_dump,
        data=market.data.model_dump(mode="json", by_alias=True),
    )
