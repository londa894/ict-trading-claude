"""Scoring, confidence, conflict and verdict evaluation (pure).

Authority order (spec STEP 8) holds by construction: data/system problems -> UNAVAILABLE before any score;
hard blockers are listed and never outweighed by a score; the outcome never contains LONG/SHORT while
verdict authority is FAIL_SAFE_ONLY. Risk and a strict news blackout have final veto (a confirmed plan becomes
NO_TRADE).

Components for the open setup in direction D (weights in scoring.json, spec initial weights):
  HTF          decision HTF bias == D: full; HTF bias not D but not opposite: setup (H4+H1) bias only
  LIQUIDITY    DOL target + liquidity event
  STRUCTURE    confirmation break: MSS full, CHoCH partial
  DISPLACEMENT displacement PRESENT on the break
  PD_ARRAY     leg zone + zone touched
  NO_WICK      a same-direction MEANINGFUL+ no-wick candle since the liquidity event
  SESSION      time quality now (IDEAL / ACCEPTABLE / LOW_QUALITY / AVOID)
  MACRO        macro state versus D (services/macro); NOT_EVALUATED when unwired, unavailable or synthetic
  ENTRY        a confirmed entry plan (research-only models count half)
  RISK_RR      plan R:R1 >= mode minimum and no risk lock (sizing and locks: services/risk)
Adjustments: correlated evidence (displacement credited only through the break's own qualifier), counter-trend
(decision HTF bias opposite D). Grade from the adjusted score; confidence from score, downgraded by conflict,
capped while required gates are missing. Macro adds conflict on CONFLICT / STRONG_CONFLICT and never blocks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.contracts import strategy_version
from app.domain.enums import (
    Blocker,
    DataQuality,
    DecisionConfidence,
    Direction,
    EntryWarning,
    EvaluationOutcome,
    ExpansionState,
    HtfBias,
    LiquiditySide,
    MacroState,
    NewsState,
    NoWickStrength,
    QualifierStatus,
    RiskStatus,
    ScoreComponentStatus,
    ScoreFactor,
    SessionQuality,
    SetupGrade,
    SetupState,
    StructureEventType,
)
from app.services.macro.models import MacroAssessment
from app.services.news.models import NewsAssessment
from app.services.no_wick.models import NoWickEvent
from app.services.pd_arrays.models import PdArrayZone
from app.services.risk.models import RiskAssessment
from app.services.scoring.models import DecisionEvaluation, ScoreAdjustment, ScoreItem, ScoringConfig
from app.services.setup_state.models import Setup, SetupAnalysis

S = SetupState
F = ScoreFactor
EVALUATED, NOT_EVALUATED = ScoreComponentStatus.EVALUATED, ScoreComponentStatus.NOT_EVALUATED
PRE_BREAK = frozenset({S.DISCOVERED, S.WATCH, S.SETUP_FORMING, S.LIQUIDITY_EVENT, S.WAITING_FOR_MSS})
PRE_TOUCH = frozenset({S.SETUP_ARMED, S.WAITING_FOR_RETRACEMENT, S.ENTRY_ZONE_APPROACHING})
MISSING_GATES = [Blocker.RISK_GATE_MISSING, Blocker.NEWS_GATE_MISSING]
CONFIDENCE_ORDER = list(DecisionConfidence)
DATA_QUALITY_SCORE = {
    DataQuality.LIVE: 100.0,
    DataQuality.CURRENT: 100.0,
    DataQuality.DELAYED: 70.0,
    DataQuality.STALE: 30.0,
}


@dataclass(frozen=True)
class EvaluationInputs:
    setups: SetupAnalysis
    no_wick_events: Sequence[NoWickEvent]
    pd_zones: Sequence[PdArrayZone]
    time_quality: SessionQuality | None
    expansion: ExpansionState | None
    htf_bias: HtfBias | None
    primary_dol_side: LiquiditySide | None
    instrument_spec_missing: bool
    weak_fvg_size_atr: float
    now: datetime
    risk: RiskAssessment | None = None  # None = risk gate not wired
    news: NewsAssessment | None = None  # None = news gate not wired
    macro: MacroAssessment | None = None  # None = macro not wired (context, not a gate)


def _opposite(d: Direction) -> Direction:
    return Direction.BEARISH if d is Direction.BULLISH else Direction.BULLISH


def grade_for(score: float, cfg: ScoringConfig) -> SetupGrade:
    for grade, floor in cfg.grades:
        if score >= floor:
            return grade
    return SetupGrade.D


def confidence_for(score: float | None, conflict: float, cfg: ScoringConfig) -> DecisionConfidence:
    if score is None:
        return DecisionConfidence.LOW
    if score >= cfg.very_high_from:
        level = DecisionConfidence.VERY_HIGH
    elif score >= cfg.high_from:
        level = DecisionConfidence.HIGH
    elif score >= cfg.moderate_from:
        level = DecisionConfidence.MODERATE
    else:
        level = DecisionConfidence.LOW
    if conflict >= cfg.conflict_downgrade_from:
        level = CONFIDENCE_ORDER[max(0, CONFIDENCE_ORDER.index(level) - 1)]
    cap = CONFIDENCE_ORDER.index(cfg.cap_while_gates_missing)
    return CONFIDENCE_ORDER[min(CONFIDENCE_ORDER.index(level), cap)]


def evaluate(inputs: EvaluationInputs, cfg: ScoringConfig) -> DecisionEvaluation:
    a = inputs.setups
    base = {
        "symbol": a.symbol,
        "as_of": a.as_of,
        "eligible_for_decision": a.eligible_for_decision,
        "ineligibility": a.ineligibility,
        "data_quality_score": 0.0 if a.is_synthetic else DATA_QUALITY_SCORE.get(a.quality, 0.0),
        "missing_gates": [
            *([Blocker.RISK_GATE_MISSING] if inputs.risk is None else []),
            *([Blocker.NEWS_GATE_MISSING] if inputs.news is None else []),
        ],
        "risk": inputs.risk,
        "news": inputs.news,
        "macro": inputs.macro,
        "authority": "NOT_AUTHORIZED",
        "strategy_version": strategy_version(),
        "generated_at": inputs.now,
    }
    spec_blockers = [Blocker.INSTRUMENT_SPEC_MISSING] if inputs.instrument_spec_missing else []
    risk_blockers = list(inputs.risk.blockers) if inputs.risk is not None else []
    risk_locked = inputs.risk is not None and inputs.risk.status is RiskStatus.LOCKED
    news_blockers = list(inputs.news.blockers) if inputs.news is not None else []
    news_blackout = inputs.news is not None and inputs.news.state is NewsState.BLACKOUT
    if not a.eligible_for_decision:
        return _empty(base, EvaluationOutcome.UNAVAILABLE, [], ["Data is not eligible for a decision."], None)

    s = a.current
    if s is None:
        last = a.setups[-1] if a.setups else None
        missed = last is not None and last.state is S.ENTRY_MISSED
        blockers = [*spec_blockers, *risk_blockers, *news_blockers]
        if missed and last is not None:
            blockers.append(Blocker.ENTRY_MISSED)
            if last.reason and "R:R" in last.reason:
                blockers.append(Blocker.INSUFFICIENT_RR)
        advocate = ["No open setup: nothing qualifies right now.", *_risk_advocate(inputs.risk)]
        if missed and last is not None:
            advocate.append(f"The last setup's entry was missed: {last.reason}")
        outcome = EvaluationOutcome.NO_TRADE if missed else EvaluationOutcome.WAIT
        return _empty(base, outcome, list(dict.fromkeys(blockers)), advocate, last)

    d = s.direction
    since = s.liquidity_event.time if s.liquidity_event else s.discovered_at
    meaningful = NoWickStrength.MEANINGFUL.rank
    recent_nw = [e for e in inputs.no_wick_events if e.time >= since and e.strength.rank >= meaningful]
    same_nw = [e for e in recent_nw if e.direction is d]
    opposite_nw = [e for e in recent_nw if e.direction is _opposite(d)]
    htf_opposite = inputs.htf_bias is not None and inputs.htf_bias.value == _opposite(d).value
    htf_aligned = inputs.htf_bias is not None and inputs.htf_bias.value == d.value
    target_side = LiquiditySide.BSL if d is Direction.BULLISH else LiquiditySide.SSL

    items: list[ScoreItem] = []

    def item(
        factor: ScoreFactor, points: float, detail: str, status: ScoreComponentStatus = EVALUATED
    ) -> None:
        items.append(
            ScoreItem(
                factor=factor,
                status=status,
                points=min(points, cfg.weights[factor]) if status is EVALUATED else 0.0,
                max_points=cfg.weights[factor],
                detail=detail,
            )
        )

    if inputs.htf_bias is None:
        item(F.HTF, cfg.pts("htfSetupBiasOnly"), "setup bias only (decision HTF bias not provided)")
    elif htf_aligned:
        item(F.HTF, cfg.pts("htfAligned"), f"setup bias and decision HTF bias both {d.value}")
    elif htf_opposite:
        item(F.HTF, 0.0, f"decision HTF bias {inputs.htf_bias.value} opposes the setup")
    else:
        detail = f"setup bias {d.value}; decision HTF bias {inputs.htf_bias.value}"
        item(F.HTF, cfg.pts("htfSetupBiasOnly"), detail)
    liq = (cfg.pts("dolTarget") if s.target else 0.0) + (
        cfg.pts("liquidityEvent") if s.liquidity_event else 0.0
    )
    target_text = "yes" if s.target else "no"
    event_text = "yes" if s.liquidity_event else "no"
    item(F.LIQUIDITY, liq, f"target {target_text}; liquidity event {event_text}")
    if s.mss is None:
        item(F.STRUCTURE, 0.0, "no confirmation break yet")
    else:
        key = "mss" if s.mss.type is StructureEventType.MSS else "choch"
        item(F.STRUCTURE, cfg.pts(key), f"{s.mss.level.value} {s.mss.type.value}")
    displaced = s.mss is not None and s.mss.displacement_qualifier is QualifierStatus.PRESENT
    displacement_text = "displacement on the break" if displaced else "none"
    item(F.DISPLACEMENT, cfg.pts("displacement") if displaced else 0.0, displacement_text)
    pd_points = (cfg.pts("legZone") if s.zone_ids else 0.0) + (
        cfg.pts("zoneTouched") if s.touched_zone_id else 0.0
    )
    touched_text = "yes" if s.touched_zone_id else "no"
    item(F.PD_ARRAY, pd_points, f"{len(s.zone_ids)} leg zone(s); touched {touched_text}")
    item(F.NO_WICK, cfg.pts("noWick") if same_nw else 0.0, f"{len(same_nw)} same-direction no-wick candle(s)")
    if inputs.time_quality is None:
        item(F.SESSION, 0.0, "session clock unavailable", NOT_EVALUATED)
    else:
        session_points = cfg.points["session"]
        assert isinstance(session_points, dict)
        quality = inputs.time_quality.value
        item(F.SESSION, float(session_points[quality]), f"time quality {quality}")
    macro_state = _macro_state(inputs.macro, d)
    if macro_state is None:
        item(F.MACRO, 0.0, _macro_unscored(inputs.macro), NOT_EVALUATED)
    else:
        macro_points = cfg.points["macro"]
        assert isinstance(macro_points, dict)
        score_text = inputs.macro.score if inputs.macro is not None else None
        item(
            F.MACRO, float(macro_points[macro_state.value]), f"macro {macro_state.value} (score {score_text})"
        )
    plan = s.entry_plan
    if plan is None:
        item(F.ENTRY, 0.0, "no entry confirmation yet")
        item(F.RISK_RR, 0.0, "no plan")
    else:
        entry_points = cfg.pts("entryResearchOnly") if plan.research_only else cfg.pts("entryConfirmed")
        item(F.ENTRY, entry_points, plan.model.value)
        risk_text = inputs.risk.status.value if inputs.risk is not None else "not evaluated"
        item(
            F.RISK_RR,
            cfg.pts("rrMet") if plan.rr1 >= plan.min_rr and not risk_locked else 0.0,
            f"R:R {plan.rr1}; risk {risk_text}",
        )

    adjustments: list[ScoreAdjustment] = []
    if displaced:
        adjustments.append(
            ScoreAdjustment(
                name="CORRELATED_EVIDENCE",
                points=cfg.correlated_evidence,
                detail="displacement is only the break's own qualifier (same candle)",
            )
        )
    if htf_opposite:
        adjustments.append(
            ScoreAdjustment(
                name="COUNTER_TREND", points=cfg.counter_trend, detail="against the decision HTF bias"
            )
        )
    raw = sum(i.points for i in items) + sum(x.points for x in adjustments)
    score = round(max(0.0, min(100.0, raw)), 1)
    evaluated_max = sum(i.max_points for i in items if i.status is EVALUATED)

    against: list[str] = []
    conflict = 0.0

    def conflict_item(key: str, text: str) -> None:
        nonlocal conflict
        conflict += cfg.conflict[key]
        against.append(text)

    if htf_opposite and inputs.htf_bias is not None:
        conflict_item("htfOpposite", f"Decision HTF bias is {inputs.htf_bias.value}")
    if inputs.primary_dol_side is not None and inputs.primary_dol_side is not target_side:
        side = inputs.primary_dol_side.value
        conflict_item("dolOpposite", f"Primary DOL is {side}, opposite the setup target")
    if inputs.time_quality is SessionQuality.AVOID:
        conflict_item("timeAvoid", "Time quality is AVOID")
    elif inputs.time_quality is SessionQuality.LOW_QUALITY:
        conflict_item("timeLowQuality", "Time quality is LOW_QUALITY")
    if opposite_nw:
        count = len(opposite_nw)
        conflict_item("oppositeNoWick", f"{count} opposite-direction no-wick candle(s) since the sweep")
    if inputs.expansion is ExpansionState.EXHAUSTED:
        conflict_item("adrExhausted", "The day's range already exhausted its ADR")
    if macro_state is MacroState.STRONG_CONFLICT:
        conflict_item("macroStrongConflict", f"Macro strongly conflicts with the {d.value} setup")
    elif macro_state is MacroState.CONFLICT:
        conflict_item("macroConflict", f"Macro conflicts with the {d.value} setup")
    conflict = min(100.0, conflict)

    warnings = _warnings(s, inputs, htf_opposite)
    if inputs.news is not None and inputs.news.state in (NewsState.CAUTION, NewsState.BLACKOUT):
        warnings.append(EntryWarning.BEFORE_MAJOR_NEWS)
    advocate = [*against]
    advocate += [f"Warning: {w.value}" for w in warnings]
    if plan is not None and plan.research_only:
        advocate.append("The entry model is research-only (limit at the zone edge)")
    advocate += _macro_advocate(inputs.macro, macro_state)
    advocate += _news_advocate(inputs.news)
    advocate += _risk_advocate(inputs.risk)
    if inputs.instrument_spec_missing:
        advocate.append("Position size unverified: no instrument contract spec")

    confirmed = s.state is S.BLOCKED and plan is not None
    hard = list(dict.fromkeys([*spec_blockers, *risk_blockers, *news_blockers]))
    if confirmed and (risk_locked or news_blackout):
        outcome = EvaluationOutcome.NO_TRADE  # risk and a strict news blackout have final veto
    elif confirmed and not hard and not base["missing_gates"]:
        outcome = (
            EvaluationOutcome.CONFIRMED_AWAITING_AUTHORITY
        )  # every gate clear; authority is FAIL_SAFE_ONLY
    elif confirmed:
        outcome = EvaluationOutcome.CONFIRMED_PENDING_GATES
    else:
        outcome = EvaluationOutcome.WAIT
    return DecisionEvaluation(
        **base,
        outcome=outcome,
        direction=d,
        setup_id=s.id,
        setup_type=s.setup_type,
        setup_state=s.state,
        score=score,
        evaluated_max=evaluated_max,
        grade=grade_for(score, cfg),
        confidence=confidence_for(score, conflict, cfg),
        conflict_score=conflict,
        components=items,
        adjustments=adjustments,
        hard_blockers=hard,
        warnings=warnings,
        evidence_for=[f"{i.factor.value}: {i.detail}" for i in items if i.points > 0],
        evidence_against=against,
        devils_advocate=advocate,
        plan=plan,
    )


def _macro_state(macro: MacroAssessment | None, d: Direction) -> MacroState | None:
    """The scored macro state versus D, or None when macro cannot be scored."""
    if macro is None or not macro.available or macro.is_synthetic or macro.direction is not d:
        return None
    return macro.state if macro.state is not MacroState.UNAVAILABLE else None


def _macro_unscored(macro: MacroAssessment | None) -> str:
    if macro is None:
        return "macro not wired"
    if not macro.available:
        return f"macro unavailable: {macro.reason}"
    if macro.is_synthetic:
        return "synthetic macro data: not scored"
    return "macro was assessed for a different direction"


def _macro_advocate(macro: MacroAssessment | None, state: MacroState | None) -> list[str]:
    if state is None:
        return [f"Macro is not scored: {_macro_unscored(macro)}"]
    out = []
    if macro is not None and macro.correlation is not None and macro.correlation.regime.value == "INVERTED":
        out.append(f"Macro correlation inverted: {macro.correlation.detail}")
    return out


def _news_advocate(news: NewsAssessment | None) -> list[str]:
    if news is None:
        return ["News is not evaluated: a blackout could apply"]
    if news.state is NewsState.UNAVAILABLE:
        return [f"News cannot be proven clear: {news.calendar.reason}"]
    out = []
    if news.state is not NewsState.CLEAR and news.active_event is not None:
        e = news.active_event
        out.append(f"News {news.state.value}: {e.importance.value} {e.currency} {e.name}")
    if news.calendar.is_synthetic:
        out.append("The economic calendar is synthetic")
    return out


def _risk_advocate(risk: RiskAssessment | None) -> list[str]:
    if risk is None:
        return ["Risk is not evaluated: no sizing, no locks"]
    if risk.status is RiskStatus.NOT_CONFIGURED:
        return ["Risk is not configured: no account profile, nothing can be sized"]
    if risk.status is RiskStatus.INVALID_PROFILE:
        return ["The risk profile is invalid: nothing can be sized"]
    if risk.status is RiskStatus.UNAVAILABLE:
        return ["The risk assessment failed"]
    out = [f"Risk lock {x.lock.value}: {x.detail}" for x in risk.locks]
    if risk.status is RiskStatus.SIZE_UNVERIFIED:
        out.append("Position size unverified: " + (risk.position.detail if risk.position else "no spec"))
    return out


def _warnings(s: Setup, inputs: EvaluationInputs, htf_opposite: bool) -> list[EntryWarning]:
    out: list[EntryWarning] = []
    as_of = inputs.setups.as_of
    last_open = as_of - inputs.setups.timeframe.duration if as_of else None
    if s.state in PRE_BREAK:
        out.append(EntryWarning.EARLY_BEFORE_MSS)
    if s.state is S.LIQUIDITY_EVENT and s.liquidity_event is not None and s.liquidity_event.time == last_open:
        out.append(EntryWarning.DURING_SWEEP)
    if s.state is S.SETUP_ARMED and s.mss is not None and s.mss.time == last_open:
        out.append(EntryWarning.INSIDE_DISPLACEMENT)
    if s.state in PRE_TOUCH:
        out.append(EntryWarning.BEFORE_RETRACEMENT)
    sizes = [z.size_atr for z in inputs.pd_zones if z.id in s.zone_ids]
    if sizes and max(sizes) < inputs.weak_fvg_size_atr:
        out.append(EntryWarning.WEAK_FVG)
    if htf_opposite:
        out.append(EntryWarning.AGAINST_HTF)
    return out


def _empty(
    base: Mapping[str, object],
    outcome: EvaluationOutcome,
    blockers: list[Blocker],
    advocate: list[str],
    last: Setup | None,
) -> DecisionEvaluation:
    return DecisionEvaluation(
        **base,
        outcome=outcome,
        direction=None,
        setup_id=None,
        setup_type=None,
        setup_state=None,
        score=None,
        evaluated_max=None,
        grade=None,
        confidence=DecisionConfidence.LOW,
        conflict_score=0.0,
        components=[],
        adjustments=[],
        hard_blockers=blockers,
        warnings=[],
        evidence_for=[],
        evidence_against=[],
        devils_advocate=advocate,
        plan=None,
    )
