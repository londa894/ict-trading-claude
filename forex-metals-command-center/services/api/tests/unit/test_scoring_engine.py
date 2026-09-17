"""Scoring, grade, confidence, conflict, warnings and evaluation outcomes (Phase 8), on real engine setups."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.enums import (
    Blocker,
    DataQuality,
    DecisionConfidence,
    Direction,
    EntryMode,
    EntryWarning,
    EvaluationOutcome,
    ExpansionState,
    HtfBias,
    LiquiditySide,
    NoWickStrength,
    Po3Phase,
    ScoreComponentStatus,
    ScoreFactor,
    SessionQuality,
    SetupGrade,
    SetupState,
    Timeframe,
)
from app.services.entry.models import EntryConfig
from app.services.scoring import models as scoring_models
from app.services.scoring.engine import EvaluationInputs, confidence_for, evaluate, grade_for
from app.services.scoring.models import ScoringConfig
from app.services.scoring.service import dol_side
from app.services.setup_state.models import BiasState, Po3State, SetupAnalysis
from tests.setup_helpers import happy

CFG = ScoringConfig.from_spec()
NOW = datetime(2024, 1, 9, 16, 0, tzinfo=UTC)
BULL_CONFIRM = (2031.8, 2032.3, 2031.3, 2032.0)


def analysis(candles, result, *, eligible=True):
    open_setups = [s for s in result.setups if not s.terminal]
    current = open_setups[0] if open_setups else None
    return SetupAnalysis(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        as_of=candles[-1].close_time,
        candle_count=len(candles),
        quality=DataQuality.CURRENT,
        is_synthetic=False,
        eligible_for_decision=eligible,
        ineligibility=[] if eligible else ["DATA_STALE"],
        bias=BiasState(direction=Direction.BULLISH, timeframes=[Timeframe.H4, Timeframe.H1], latest=[]),
        current_state=(current.state if current else None) if eligible else SetupState.BLOCKED,
        current=current,
        setups=result.setups,
        events=result.events,
        po3=Po3State(trading_day=None, phase=Po3Phase.UNCLEAR, daily_open=None, adr=None, detail=""),
        provider_error=None,
        strategy_version="0.19.0-phase19",
        generated_at=NOW,
    )


def inputs(a, **over):
    base = dict(
        setups=a,
        no_wick_events=[],
        pd_zones=[],
        time_quality=SessionQuality.IDEAL,
        expansion=ExpansionState.ACTIVE,
        htf_bias=HtfBias.BULLISH,
        primary_dol_side=LiquiditySide.BSL,
        instrument_spec_missing=True,
        weak_fvg_size_atr=0.3,
        now=NOW,
    )
    base.update(over)
    return EvaluationInputs(**base)


def confirmed(**kw):
    candles, result = happy(rows={29: BULL_CONFIRM}, **kw).run()
    return analysis(candles, result)


def test_confirmed_plan_scores_but_is_never_authorized():
    ev = evaluate(inputs(confirmed()), CFG)
    points = {i.factor: i.points for i in ev.components}
    assert points == {
        ScoreFactor.HTF: 15,
        ScoreFactor.LIQUIDITY: 15,
        ScoreFactor.STRUCTURE: 15,
        ScoreFactor.DISPLACEMENT: 10,
        ScoreFactor.PD_ARRAY: 10,
        ScoreFactor.NO_WICK: 0,
        ScoreFactor.SESSION: 10,
        ScoreFactor.MACRO: 0,
        ScoreFactor.ENTRY: 10,
        ScoreFactor.RISK_RR: 5,
    }
    macro = next(i for i in ev.components if i.factor is ScoreFactor.MACRO)
    assert macro.status is ScoreComponentStatus.NOT_EVALUATED
    assert [a.name for a in ev.adjustments] == ["CORRELATED_EVIDENCE"]
    assert ev.score == 85.0 and ev.evaluated_max == 95.0 and ev.grade is SetupGrade.A
    assert ev.confidence is DecisionConfidence.MODERATE  # HIGH by score, capped while gates are missing
    assert ev.outcome is EvaluationOutcome.CONFIRMED_PENDING_GATES and ev.authority == "NOT_AUTHORIZED"
    assert ev.missing_gates == [Blocker.RISK_GATE_MISSING, Blocker.NEWS_GATE_MISSING]
    assert ev.hard_blockers == [Blocker.INSTRUMENT_SPEC_MISSING]
    assert ev.plan is not None and ev.setup_state is SetupState.BLOCKED
    body = ev.model_dump_json(by_alias=True)
    assert "LONG" not in body and "SHORT" not in body
    assert any("News is not evaluated" in x for x in ev.devils_advocate)


def test_counter_trend_and_conflicts_lower_score_and_confidence():
    a = confirmed()
    ev = evaluate(inputs(a, htf_bias=HtfBias.BEARISH), CFG)
    assert {x.name for x in ev.adjustments} == {"CORRELATED_EVIDENCE", "COUNTER_TREND"}
    assert ev.score == 60.0 and ev.grade is SetupGrade.C and ev.conflict_score == 30.0
    assert EntryWarning.AGAINST_HTF in ev.warnings and ev.confidence is DecisionConfidence.MODERATE
    worse = evaluate(inputs(a, htf_bias=HtfBias.BEARISH, primary_dol_side=LiquiditySide.SSL), CFG)
    assert worse.conflict_score == 55.0 and worse.confidence is DecisionConfidence.LOW
    assert "Primary DOL is SSL, opposite the setup target" in worse.evidence_against


def test_no_wick_session_and_exhaustion_inputs():
    a = confirmed()
    since = a.current.liquidity_event.time
    same = SimpleNamespace(time=since, direction=Direction.BULLISH, strength=NoWickStrength.STRONG)
    opposite = SimpleNamespace(time=since, direction=Direction.BEARISH, strength=NoWickStrength.MEANINGFUL)
    ev = evaluate(
        inputs(
            a,
            no_wick_events=[same, opposite],
            time_quality=SessionQuality.AVOID,
            expansion=ExpansionState.EXHAUSTED,
        ),
        CFG,
    )
    points = {i.factor: i.points for i in ev.components}
    assert points[ScoreFactor.NO_WICK] == 5 and points[ScoreFactor.SESSION] == 0
    assert ev.conflict_score == 20 + 15 + 10


def test_research_only_limit_entry_counts_half():
    aggressive = EntryConfig.from_spec(EntryMode.AGGRESSIVE)
    candles, result = happy(entry_cfg=aggressive).run()
    ev = evaluate(inputs(analysis(candles, result)), CFG)
    assert next(i for i in ev.components if i.factor is ScoreFactor.ENTRY).points == 5
    assert "The entry model is research-only (limit at the zone edge)" in ev.devils_advocate


def test_no_open_setup_after_a_missed_entry_is_no_trade():
    candles, result = happy(rows={29: BULL_CONFIRM}, target_price=2036.0).run()
    ev = evaluate(inputs(analysis(candles, result)), CFG)
    assert ev.outcome is EvaluationOutcome.NO_TRADE and ev.score is None and ev.grade is None
    assert ev.hard_blockers == [
        Blocker.INSTRUMENT_SPEC_MISSING,
        Blocker.ENTRY_MISSED,
        Blocker.INSUFFICIENT_RR,
    ]
    assert ev.confidence is DecisionConfidence.LOW


def test_ineligible_data_is_unavailable():
    candles, result = happy(rows={29: BULL_CONFIRM}).run()
    ev = evaluate(inputs(analysis(candles, result, eligible=False)), CFG)
    assert ev.outcome is EvaluationOutcome.UNAVAILABLE and ev.score is None and ev.components == []


def test_warnings_follow_the_setup_state():
    candles, result = happy(count=21, breaks={}, zones={}).run()  # the sweep is the last candle
    ev = evaluate(inputs(analysis(candles, result)), CFG)
    assert ev.setup_state is SetupState.LIQUIDITY_EVENT
    assert ev.warnings == [EntryWarning.EARLY_BEFORE_MSS, EntryWarning.DURING_SWEEP]
    candles, result = happy(count=25, zones={}).run()  # armed on the last candle
    ev = evaluate(inputs(analysis(candles, result)), CFG)
    assert ev.warnings == [EntryWarning.INSIDE_DISPLACEMENT, EntryWarning.BEFORE_RETRACEMENT]
    candles, result = happy(count=27).run()
    weak = [SimpleNamespace(id="FVG:25", size_atr=0.2)]
    ev = evaluate(inputs(analysis(candles, result), pd_zones=weak), CFG)
    assert EntryWarning.WEAK_FVG in ev.warnings


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (90, SetupGrade.A_PLUS),
        (89.9, SetupGrade.A),
        (70, SetupGrade.B),
        (60, SetupGrade.C),
        (59.9, SetupGrade.D),
    ],
)
def test_grades(score, grade):
    assert grade_for(score, CFG) is grade


@pytest.mark.parametrize(
    ("score", "conflict", "confidence"),
    [
        (None, 0, DecisionConfidence.LOW),
        (95, 0, DecisionConfidence.MODERATE),  # VERY_HIGH capped
        (70, 0, DecisionConfidence.MODERATE),
        (70, 40, DecisionConfidence.LOW),
        (50, 0, DecisionConfidence.LOW),
    ],
)
def test_confidence(score, conflict, confidence):
    assert confidence_for(score, conflict, CFG) is confidence
    uncapped = replace(CFG, cap_while_gates_missing=DecisionConfidence.VERY_HIGH)
    if score == 95:
        assert confidence_for(score, conflict, uncapped) is DecisionConfidence.VERY_HIGH


def test_config_requires_weights_summing_to_100(monkeypatch):
    real = scoring_models.load_spec

    def patched(name):
        spec = real(name)
        if name == "scoring":
            spec = {**spec, "weights": {**spec["weights"], "HTF": 20}}
        return spec

    monkeypatch.setattr(scoring_models, "load_spec", patched)
    with pytest.raises(ValueError, match="sum to 100"):
        ScoringConfig.from_spec()


def test_dol_side_parser():
    assert dol_side("H1 BSL PDH 2024-01-08 @ 2040 (magnet 60, 2 ATR)") is LiquiditySide.BSL
    assert dol_side("H1 SSL x") is LiquiditySide.SSL
    assert dol_side(None) is None and dol_side("garbage") is None


def _risk(status, locks=(), blockers=()):
    from app.domain.enums import RiskStatus
    from app.services.risk.engine import not_assessed
    from app.services.risk.models import RiskLockItem

    base = not_assessed("XAUUSD", NOW, RiskStatus.NOT_CONFIGURED, None)
    return base.model_copy(
        update={
            "status": RiskStatus(status),
            "locks": [RiskLockItem(lock=x, detail="detail") for x in locks],
            "blockers": list(blockers) if status != "NOT_CONFIGURED" else base.blockers,
        }
    )


def test_risk_gate_replaces_the_missing_risk_gate():
    ev = evaluate(inputs(confirmed(), risk=_risk("WITHIN_LIMITS")), CFG)
    assert ev.outcome is EvaluationOutcome.CONFIRMED_PENDING_GATES and ev.risk is not None
    assert ev.missing_gates == [Blocker.NEWS_GATE_MISSING]
    assert ev.confidence is DecisionConfidence.MODERATE  # still capped: the news gate is missing
    rr = next(i for i in ev.components if i.factor is ScoreFactor.RISK_RR)
    assert rr.points == 5 and "WITHIN_LIMITS" in rr.detail


def test_a_risk_lock_vetoes_a_confirmed_plan():
    locked = _risk("LOCKED", locks=["DAILY_LOSS_LIMIT"], blockers=[Blocker.RISK_LOCKED])
    ev = evaluate(inputs(confirmed(), risk=locked), CFG)
    assert ev.outcome is EvaluationOutcome.NO_TRADE and ev.plan is not None
    assert ev.hard_blockers == [Blocker.INSTRUMENT_SPEC_MISSING, Blocker.RISK_LOCKED]
    rr = next(i for i in ev.components if i.factor is ScoreFactor.RISK_RR)
    assert rr.points == 0 and any("DAILY_LOSS_LIMIT" in x for x in ev.devils_advocate)
    body = ev.model_dump_json(by_alias=True)
    assert "LONG" not in body and "SHORT" not in body


def test_missing_profile_keeps_the_plan_pending_and_blocked():
    ev = evaluate(inputs(confirmed(), risk=_risk("NOT_CONFIGURED")), CFG)
    assert ev.outcome is EvaluationOutcome.CONFIRMED_PENDING_GATES
    assert Blocker.RISK_PROFILE_MISSING in ev.hard_blockers
    assert any("not configured" in x for x in ev.devils_advocate)


def _news(state, blockers=(), synthetic=False):
    from app.domain.enums import NewsState
    from app.domain.instrument import get_instrument
    from app.services.news.engine import assess as news_assess
    from app.services.news.models import CalendarSnapshot, NewsConfig

    snapshot = CalendarSnapshot(
        "file", "t", synthetic, NOW, NOW - timedelta(days=1), NOW + timedelta(days=2), ()
    )
    base = news_assess(get_instrument("XAUUSD"), NOW, snapshot, None, NewsConfig.from_spec(), "file")
    return base.model_copy(update={"state": NewsState(state), "blockers": list(blockers)})


def test_all_gates_clear_awaits_authority_only():
    ev = evaluate(
        inputs(confirmed(), risk=_risk("WITHIN_LIMITS"), news=_news("CLEAR"), instrument_spec_missing=False),
        CFG,
    )
    assert ev.outcome is EvaluationOutcome.CONFIRMED_AWAITING_AUTHORITY
    assert ev.missing_gates == [] and ev.hard_blockers == [] and ev.authority == "NOT_AUTHORIZED"
    assert ev.confidence is DecisionConfidence.MODERATE  # still capped while authority is FAIL_SAFE_ONLY
    body = ev.model_dump_json(by_alias=True)
    assert "LONG" not in body and "SHORT" not in body


def test_a_news_blackout_vetoes_a_confirmed_plan_and_caution_warns():
    blackout = _news("BLACKOUT", [Blocker.NEWS_BLACKOUT])
    ev = evaluate(
        inputs(confirmed(), risk=_risk("WITHIN_LIMITS"), news=blackout, instrument_spec_missing=False), CFG
    )
    assert ev.outcome is EvaluationOutcome.NO_TRADE and ev.hard_blockers == [Blocker.NEWS_BLACKOUT]
    assert EntryWarning.BEFORE_MAJOR_NEWS in ev.warnings
    caution = evaluate(
        inputs(
            confirmed(), risk=_risk("WITHIN_LIMITS"), news=_news("CAUTION"), instrument_spec_missing=False
        ),
        CFG,
    )
    assert (
        caution.outcome is EvaluationOutcome.CONFIRMED_AWAITING_AUTHORITY
        and EntryWarning.BEFORE_MAJOR_NEWS in caution.warnings
    )
    for pending in (
        _news("POST_NEWS_WAIT", [Blocker.NEWS_POST_WAIT]),
        _news("UNAVAILABLE", [Blocker.NEWS_DATA_UNAVAILABLE]),
        _news("CLEAR", [Blocker.NEWS_DATA_SYNTHETIC]),
    ):
        ev = evaluate(
            inputs(confirmed(), risk=_risk("WITHIN_LIMITS"), news=pending, instrument_spec_missing=False), CFG
        )
        assert (
            ev.outcome is EvaluationOutcome.CONFIRMED_PENDING_GATES and ev.hard_blockers == pending.blockers
        )


def _macro(state, synthetic=False, available=True, direction=None, regime=None):
    from app.domain.enums import CorrelationRegime, Direction, MacroBias, MacroSeriesId, MacroState
    from app.services.macro.models import CorrelationInfo, MacroAssessment

    corr = (
        CorrelationInfo(series=MacroSeriesId.DXY, observations=20, coefficient=0.5, regime=regime, detail="d")
        if regime
        else None
    )
    return MacroAssessment(
        symbol="XAUUSD",
        bias=MacroBias.BULLISH if available else MacroBias.UNAVAILABLE,
        score=0.5 if available else None,
        state=MacroState(state),
        direction=direction or Direction.BULLISH,
        drivers=[],
        series=[],
        correlation=corr and corr.model_copy(update={"regime": CorrelationRegime(regime)}),
        provider="file",
        source="t",
        is_synthetic=synthetic,
        available=available,
        fetched_at=NOW,
        reason=None if available else "no macro data provider is configured (MACRO_PROVIDER)",
        warnings=[],
        thresholds={},
        strategy_version="x",
        generated_at=NOW,
    )


@pytest.mark.parametrize(
    ("state", "points", "conflict"),
    [
        ("STRONGLY_SUPPORTIVE", 5, 0),
        ("SUPPORTIVE", 4, 0),
        ("NEUTRAL", 2.5, 0),
        ("CONFLICT", 0, 15),
        ("STRONG_CONFLICT", 0, 30),
    ],
)
def test_macro_state_scores_and_adds_conflict_but_never_blocks(state, points, conflict):
    a = confirmed()
    plain = evaluate(inputs(a), CFG)
    ev = evaluate(inputs(a, macro=_macro(state)), CFG)
    item = next(i for i in ev.components if i.factor is ScoreFactor.MACRO)
    assert item.status is ScoreComponentStatus.EVALUATED and item.points == points
    assert (
        ev.evaluated_max == plain.evaluated_max + 5 and ev.conflict_score == plain.conflict_score + conflict
    )
    assert ev.score == plain.score + points and ev.macro is not None
    assert ev.hard_blockers == plain.hard_blockers and ev.outcome is plain.outcome
    if conflict:
        assert any("Macro" in x for x in ev.evidence_against)


@pytest.mark.parametrize(
    ("macro", "detail"),
    [
        (None, "macro not wired"),
        ("unavailable", "macro unavailable"),
        ("synthetic", "synthetic macro data: not scored"),
        ("other", "different direction"),
    ],
)
def test_macro_is_not_scored_when_it_cannot_be_trusted(macro, detail):
    from app.domain.enums import Direction

    m = {
        None: None,
        "unavailable": _macro("UNAVAILABLE", available=False),
        "synthetic": _macro("STRONG_CONFLICT", synthetic=True),
        "other": _macro("STRONG_CONFLICT", direction=Direction.BEARISH),
    }[macro]
    ev = evaluate(inputs(confirmed(), macro=m), CFG)
    item = next(i for i in ev.components if i.factor is ScoreFactor.MACRO)
    assert item.status is ScoreComponentStatus.NOT_EVALUATED and item.points == 0 and detail in item.detail
    assert ev.conflict_score == evaluate(inputs(confirmed()), CFG).conflict_score
    assert any("Macro is not scored" in x for x in ev.devils_advocate)


def test_inverted_correlation_is_named_in_the_devils_advocate():
    ev = evaluate(inputs(confirmed(), macro=_macro("SUPPORTIVE", regime="INVERTED")), CFG)
    assert any("correlation inverted" in x for x in ev.devils_advocate)
