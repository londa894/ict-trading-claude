"""Scoring & verdict evaluation models (spec STEP 8, Phase 8). Scores rank; never win probabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import (
    AnalysisIneligibility,
    Blocker,
    DecisionConfidence,
    Direction,
    EntryWarning,
    EvaluationOutcome,
    ScoreComponentStatus,
    ScoreFactor,
    SetupGrade,
    SetupState,
    SetupType,
)
from app.services.entry.models import EntryPlan
from app.services.macro.models import MacroAssessment
from app.services.news.models import NewsAssessment
from app.services.risk.models import RiskAssessment


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[ScoreFactor, float]
    points: dict[str, object]
    correlated_evidence: float
    counter_trend: float
    conflict: dict[str, float]
    grades: list[tuple[SetupGrade, float]]
    moderate_from: float
    high_from: float
    very_high_from: float
    conflict_downgrade_from: float
    cap_while_gates_missing: DecisionConfidence

    @classmethod
    def from_spec(cls) -> ScoringConfig:
        s = load_spec("scoring")
        weights = {ScoreFactor(k): float(v) for k, v in s["weights"].items()}
        if abs(sum(weights.values()) - 100.0) > 1e-9 or set(weights) != set(ScoreFactor):
            raise ValueError("scoring weights must cover every factor and sum to 100")
        c = s["confidence"]
        return cls(
            weights=weights,
            points=dict(s["points"]),
            correlated_evidence=float(s["adjustments"]["correlatedEvidence"]),
            counter_trend=float(s["adjustments"]["counterTrend"]),
            conflict={k: float(v) for k, v in s["conflict"].items()},
            grades=sorted(((SetupGrade(k), float(v)) for k, v in s["grades"].items()), key=lambda g: -g[1]),
            moderate_from=float(c["moderateFrom"]),
            high_from=float(c["highFrom"]),
            very_high_from=float(c["veryHighFrom"]),
            conflict_downgrade_from=float(c["conflictDowngradeFrom"]),
            cap_while_gates_missing=DecisionConfidence(c["capWhileGatesMissing"]),
        )

    def pts(self, key: str) -> float:
        return float(self.points[key])  # type: ignore[arg-type]


class ScoreItem(ApiModel):
    factor: ScoreFactor
    status: ScoreComponentStatus
    points: float
    max_points: float
    detail: str


class ScoreAdjustment(ApiModel):
    name: str
    points: float
    detail: str


class DecisionEvaluation(ApiModel):
    """Deterministic verdict evaluation. `authority` stays NOT_AUTHORIZED until every required gate exists."""

    symbol: str
    as_of: datetime | None
    eligible_for_decision: bool
    ineligibility: list[AnalysisIneligibility]
    outcome: EvaluationOutcome
    direction: Direction | None
    setup_id: str | None
    setup_type: SetupType | None
    setup_state: SetupState | None
    score: float | None
    evaluated_max: float | None
    grade: SetupGrade | None
    confidence: DecisionConfidence
    conflict_score: float
    data_quality_score: float
    components: list[ScoreItem]
    adjustments: list[ScoreAdjustment]
    hard_blockers: list[Blocker]
    missing_gates: list[Blocker]
    warnings: list[EntryWarning]
    evidence_for: list[str]
    evidence_against: list[str]
    devils_advocate: list[str]
    plan: EntryPlan | None
    risk: RiskAssessment | None
    news: NewsAssessment | None
    macro: MacroAssessment | None
    authority: str
    strategy_version: str
    generated_at: datetime
