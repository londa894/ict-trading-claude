"""Master Decision Object (spec STEP 18). All surfaces must read this same object."""

from __future__ import annotations

from datetime import datetime

from pydantic import field_validator

from app.domain.base import ApiModel, require_utc
from app.domain.enums import Blocker, DataQuality, DecisionConfidence, Verdict

NOT_EVALUATED = "NOT_EVALUATED"
UNKNOWN = "UNKNOWN"


class PriceZone(ApiModel):
    low: float
    high: float


class MasterDecision(ApiModel):
    symbol: str
    verdict: Verdict
    direction: str | None = None
    setup_type: str | None = None
    setup_state: str
    setup_grade: str | None = None
    setup_score: float | None = None
    decision_confidence: DecisionConfidence
    htf_bias: str
    primary_dol: str | None = None
    secondary_dol: str | None = None
    liquidity_event: str | None = None
    structure_event: str | None = None
    displacement: str | None = None
    pd_array: dict[str, object] | None = None
    no_wick_state: dict[str, object] | None = None
    session_state: dict[str, object] | None = None
    macro_state: dict[str, object] | None = None
    news_state: dict[str, object] | None = None
    entry_zone: PriceZone | None = None
    preferred_entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    rr: float | None = None
    risk_status: str
    blockers: list[Blocker]
    next_required_event: str | None = None
    invalidation: str | None = None
    data_quality: DataQuality
    strategy_version: str
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "updated_at")
