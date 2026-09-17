"""ALERT_ME_WHEN_READY checklist (pure): the mandatory conditions of spec STEP 5, the gates and authority.

The watch fires only when the Master Decision itself is LONG/SHORT in the watched direction AND the verdict
authority is FULL. Under FAIL_SAFE_ONLY the decision is never directional (enforce_verdict_authority), so
it cannot fire.
"""

from __future__ import annotations

from app.domain.decision import MasterDecision
from app.domain.enums import (
    ConditionStatus,
    Direction,
    NewsState,
    ReadyWatchState,
    RiskStatus,
    SetupStep,
    SetupStepStatus,
    Verdict,
)
from app.services.alerts.models import ReadyCondition
from app.services.scoring.models import DecisionEvaluation
from app.services.setup_state.service import SetupRun

MET, MISSING, NOT_EVALUATED = ConditionStatus.MET, ConditionStatus.MISSING, ConditionStatus.NOT_EVALUATED
STEP_STATUS = {
    SetupStepStatus.DONE: MET,
    SetupStepStatus.PENDING: MISSING,
    SetupStepStatus.NOT_EVALUATED: NOT_EVALUATED,
}
SEQUENCE = [
    SetupStep.HTF_BIAS,
    SetupStep.DOL_TARGET,
    SetupStep.LIQUIDITY_EVENT,
    SetupStep.DISPLACEMENT,
    SetupStep.MSS,
    SetupStep.PD_ARRAY,
    SetupStep.RETRACEMENT,
    SetupStep.LTF_CONFIRMATION,
]
VERDICT_FOR = {Direction.BULLISH: Verdict.LONG, Direction.BEARISH: Verdict.SHORT}


def checklist(
    decision: MasterDecision,
    evaluation: DecisionEvaluation | None,
    run: SetupRun | None,
    direction: Direction | None,
    authority: str,
) -> tuple[ReadyWatchState, list[ReadyCondition], bool]:
    """Returns (state, conditions, fire)."""
    c: list[ReadyCondition] = []
    usable = decision.verdict is not Verdict.UNAVAILABLE
    c.append(
        ReadyCondition(
            name="DATA_USABLE", status=MET if usable else MISSING, detail=decision.data_quality.value
        )
    )
    current = run.analysis.current if run is not None else None
    matches = current is not None and (direction is None or current.direction is direction)
    setup_text = (
        f"{current.direction.value} {current.state.value}" if current is not None else "no open setup"
    )
    if not usable:
        # Setup progress computed from untrusted data is not evidence: nothing counts as met.
        for name in ["OPEN_SETUP", *(step.value for step in SEQUENCE)]:
            c.append(ReadyCondition(name=name, status=NOT_EVALUATED, detail="data not usable"))
    else:
        c.append(ReadyCondition(name="OPEN_SETUP", status=MET if matches else MISSING, detail=setup_text))
        steps = {s.step: s for s in current.steps} if matches and current is not None else {}
        for step in SEQUENCE:
            s = steps.get(step)
            c.append(
                ReadyCondition(
                    name=step.value,
                    status=STEP_STATUS[s.status] if s else MISSING,
                    detail=s.detail if s else "no matching setup",
                )
            )
    risk = evaluation.risk if evaluation is not None else None
    risk_ok = risk is not None and risk.status is RiskStatus.WITHIN_LIMITS
    c.append(
        ReadyCondition(
            name="RISK",
            status=MET if risk_ok else MISSING,
            detail=risk.status.value if risk else "not evaluated",
        )
    )
    news = evaluation.news if evaluation is not None else None
    news_ok = (
        news is not None
        and news.state in (NewsState.CLEAR, NewsState.NORMALIZED, NewsState.CAUTION)
        and not news.blockers
    )
    news_detail = "news gate not wired" if news is None else news.state.value
    if news is not None and news.blockers:
        news_detail = f"{news.state.value} ({', '.join(b.value for b in news.blockers)})"
    c.append(ReadyCondition(name="NEWS_GATE", status=MET if news_ok else MISSING, detail=news_detail))
    full = authority == "FULL"
    c.append(ReadyCondition(name="VERDICT_AUTHORITY", status=MET if full else MISSING, detail=authority))
    wanted = {VERDICT_FOR[direction]} if direction is not None else set(VERDICT_FOR.values())
    directional = decision.verdict in wanted
    c.append(
        ReadyCondition(name="VERDICT", status=MET if directional else MISSING, detail=decision.verdict.value)
    )

    fire = usable and full and directional
    if fire:
        return ReadyWatchState.FIRED, c, True
    if not usable:
        return ReadyWatchState.UNAVAILABLE, c, False
    setup_done = all(
        x.status is MET
        for x in c
        if x.name in {s.value for s in SEQUENCE} | {"OPEN_SETUP", "RISK", "NEWS_GATE"}
    )
    return (ReadyWatchState.GATES_PENDING if setup_done else ReadyWatchState.WAITING), c, False


def next_required(conditions: list[ReadyCondition]) -> str | None:
    missing = next((x for x in conditions if x.status is not MET), None)
    return f"{missing.name}: {missing.detail}" if missing is not None else None
