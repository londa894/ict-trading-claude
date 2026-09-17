"""Power of Three (PO3) phase of the current trading day from the daily open, ADR and setup bias.

For bias BULLISH (mirror for BEARISH), O = daily open, A = ADR, using the trading day's closed candles:
  manipulation  the day traded against the bias beyond O by >= manipulationAdrPct % of A (low <= O - m)
  distribution  after that, price closed in the bias direction beyond O by >= distributionAdrPct % of A
  ACCUMULATION  no manipulation and the close is within the distribution distance of O
  MANIPULATION  manipulation happened, distribution not (yet)
  DISTRIBUTION  manipulation happened first, then the latest close is beyond O + d
  UNCLEAR       no bias, no daily open, no ADR, or price expanded with the bias without a manipulation first
PO3 describes the day's delivery; it never creates a trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.domain.candle import Candle
from app.domain.enums import Direction, Po3Phase
from app.services.setup_state.models import Po3State, SetupConfig


def po3_state(
    candles: Sequence[Candle],
    trading_day: date | None,
    daily_open: float | None,
    adr: float | None,
    bias: Direction | None,
    cfg: SetupConfig,
) -> Po3State:
    def state(phase: Po3Phase, detail: str) -> Po3State:
        return Po3State(trading_day=trading_day, phase=phase, daily_open=daily_open, adr=adr, detail=detail)

    if bias is None or daily_open is None or adr is None or adr <= 0 or not candles:
        return state(Po3Phase.UNCLEAR, "needs a setup bias, the daily open, an ADR and candles of the day")
    m = cfg.po3_manipulation_adr_pct / 100 * adr
    d = cfg.po3_distribution_adr_pct / 100 * adr
    bullish = bias is Direction.BULLISH

    def against(c: Candle) -> bool:
        return c.low <= daily_open - m if bullish else c.high >= daily_open + m

    manipulation_index = next((k for k, c in enumerate(candles) if against(c)), None)
    close = candles[-1].close
    expanded = (close >= daily_open + d) if bullish else (close <= daily_open - d)
    if manipulation_index is None:
        if expanded:
            return state(Po3Phase.UNCLEAR, "expanded with the bias without a manipulation leg first")
        return state(Po3Phase.ACCUMULATION, "no manipulation yet; price near the daily open")
    if expanded and manipulation_index < len(candles) - 1:
        return state(Po3Phase.DISTRIBUTION, "manipulation against the bias, then expansion beyond the open")
    return state(Po3Phase.MANIPULATION, "traded against the bias beyond the manipulation distance")
