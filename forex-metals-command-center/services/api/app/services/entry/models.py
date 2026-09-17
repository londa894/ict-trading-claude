"""Entry confirmation configuration and plan model (spec STEP 5, Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import Direction, EntryMode, EntryModel, StructureEventType, Timeframe

UNAVAILABLE_MODELS = frozenset({EntryModel.BREAKER})  # breaker blocks are Phase 20+ advanced PD arrays


@dataclass(frozen=True)
class EntryConfig:
    mode: EntryMode
    models: tuple[EntryModel, ...]
    min_rr: float
    execution_timeframe: Timeframe
    execution_bars: int
    ltf_break_types: frozenset[StructureEventType]
    confirmation_window_bars: int
    stop_buffer_atr: float
    min_tp1_atr: float
    target_distinct_atr: float
    weak_fvg_size_atr: float
    include_confirmed_ifvg: bool

    @classmethod
    def from_spec(cls, mode: EntryMode | None = None) -> EntryConfig:
        s = load_spec("entry")
        chosen = mode or EntryMode(s["mode"])
        m = s["modes"][chosen.value]
        models = tuple(EntryModel(x) for x in m["models"])
        if any(x in UNAVAILABLE_MODELS or x is EntryModel.IFVG for x in models):
            raise ValueError(
                "BREAKER is not available; IFVG is derived from M15_CLOSE on a confirmed IFVG zone"
            )
        breaks = frozenset(StructureEventType(x) for x in s["ltfBreakTypes"])
        if StructureEventType.BOS in breaks:
            raise ValueError("an LTF refinement must be a reversal break (CHOCH/MSS)")
        return cls(
            mode=chosen,
            models=models,
            min_rr=float(m["minRr"]),
            execution_timeframe=Timeframe(s["executionTimeframe"]),
            execution_bars=int(s["executionBars"]),
            ltf_break_types=breaks,
            confirmation_window_bars=int(s["confirmationWindowBars"]),
            stop_buffer_atr=float(s["stopBufferAtr"]),
            min_tp1_atr=float(s["minTp1Atr"]),
            target_distinct_atr=float(s["targetDistinctAtr"]),
            weak_fvg_size_atr=float(s["weakFvgSizeAtr"]),
            include_confirmed_ifvg=bool(s["includeConfirmedIfvg"]),
        )


class EntryPlan(ApiModel):
    """A confirmed deterministic plan. Never a trade authorization while authority is FAIL_SAFE_ONLY."""

    model: EntryModel
    mode: EntryMode
    direction: Direction
    confirmed_at: datetime  # open time of the setup-timeframe candle whose close confirmed
    zone_id: str
    entry: float
    stop: float
    risk: float  # |entry - stop| in price
    tp1: float
    tp2: float | None
    tp3: float | None
    rr1: float
    rr2: float | None
    rr3: float | None
    min_rr: float
    research_only: bool
    detail: str
