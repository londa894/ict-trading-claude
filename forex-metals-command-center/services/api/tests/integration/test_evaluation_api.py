import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import (
    Blocker,
    DecisionConfidence,
    EvaluationOutcome,
    SetupGrade,
    SetupState,
    SetupType,
    Timeframe,
    Verdict,
)
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.candles.service import CandleService
from app.services.entry.models import EntryPlan
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.scoring import service as scoring_service
from app.services.scoring.models import DecisionEvaluation, ScoreAdjustment, ScoreItem
from app.services.scoring.service import EvaluationService
from app.services.setup_state.models import Setup
from app.services.setup_state.service import SetupService

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)
MID_FIXTURE = SERIES_END - timedelta(hours=30, minutes=-1)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


def test_evaluation_endpoint_on_synthetic_data_is_unavailable(fixture_client):
    body = fixture_client.get("/api/v1/evaluation/XAUUSD").json()
    assert body["outcome"] == "UNAVAILABLE" and body["authority"] == "NOT_AUTHORIZED"
    assert body["score"] is None and body["confidence"] == "LOW" and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["missingGates"] == []  # the risk (Phase 9) and news (Phase 13) gates exist
    assert body["news"]["state"] == "UNAVAILABLE" and body["news"]["blockers"] == ["NEWS_DATA_UNAVAILABLE"]
    assert body["risk"]["status"] == "NOT_CONFIGURED" and body["risk"]["blockers"] == ["RISK_PROFILE_MISSING"]
    assert fixture_client.get("/api/v1/evaluation/BTCUSD").status_code == 404


async def test_eligible_evaluation_scores_the_open_setup():
    candles = CandleService(NonSyntheticStub(), clock=lambda: MID_FIXTURE)
    ev = await EvaluationService(SetupService(candles)).evaluate("XAUUSD", MID_FIXTURE)
    assert ev.eligible_for_decision and ev.outcome in (
        EvaluationOutcome.WAIT,
        EvaluationOutcome.CONFIRMED_PENDING_GATES,
    )
    assert ev.setup_state is not None and ev.score is not None and ev.grade is not None
    assert ev.confidence in (DecisionConfidence.LOW, DecisionConfidence.MODERATE)
    assert len(ev.components) == 10 and ev.evaluated_max == 95.0
    assert "LONG" not in ev.model_dump_json() and "SHORT" not in ev.model_dump_json()


def _confirmed_evaluation() -> DecisionEvaluation:
    """A hand-made CONFIRMED_PENDING_GATES evaluation: the strongest case a FAIL_SAFE_ONLY decision sees."""
    t = MID_FIXTURE
    plan = EntryPlan(
        model="M15_CLOSE",
        mode="STANDARD",
        direction="BULLISH",
        confirmed_at=t,
        zone_id="z",
        entry=2032.0,
        stop=2028.8,
        risk=3.2,
        tp1=2045.0,
        tp2=None,
        tp3=None,
        rr1=4.06,
        rr2=None,
        rr3=None,
        min_rr=2.0,
        research_only=False,
        detail="",
    )
    return DecisionEvaluation(
        symbol="XAUUSD",
        as_of=t,
        eligible_for_decision=True,
        ineligibility=[],
        outcome=EvaluationOutcome.CONFIRMED_PENDING_GATES,
        direction="BULLISH",
        setup_id="s",
        setup_type=SetupType.LIQUIDITY_SWEEP_MSS,
        setup_state=SetupState.BLOCKED,
        score=95.0,
        evaluated_max=95.0,
        grade=SetupGrade.A_PLUS,
        confidence=DecisionConfidence.MODERATE,
        conflict_score=0.0,
        data_quality_score=100.0,
        components=[ScoreItem(factor="HTF", status="EVALUATED", points=15, max_points=15, detail="")],
        adjustments=[ScoreAdjustment(name="x", points=0, detail="")],
        hard_blockers=[Blocker.INSTRUMENT_SPEC_MISSING],
        missing_gates=[Blocker.RISK_GATE_MISSING, Blocker.NEWS_GATE_MISSING],
        warnings=[],
        evidence_for=[],
        evidence_against=[],
        devils_advocate=[],
        plan=plan,
        risk=None,
        news=None,
        macro=None,
        authority="NOT_AUTHORIZED",
        strategy_version="0.19.0-phase19",
        generated_at=t,
    )


def _services(evaluate_fn=None):
    provider = NonSyntheticStub()
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    evaluation = EvaluationService(SetupService(candles))
    if evaluate_fn is not None:
        evaluation.evaluate = evaluate_fn  # type: ignore[method-assign]
    clock = lambda: AFTER_FIXTURE  # noqa: E731
    enriched = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, evaluation=evaluation)
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain


async def test_a_confirmed_plan_never_changes_the_verdict_or_publishes_prices():
    async def confirmed(*_a, **_k):
        return _confirmed_evaluation()

    enriched_svc, plain_svc = _services(confirmed)
    d = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert d.verdict == plain.verdict == Verdict.WAIT and d.direction is None
    assert (d.entry_zone, d.preferred_entry, d.stop, d.tp1, d.tp2, d.tp3, d.rr) == (None,) * 7
    assert d.setup_state == "BLOCKED" and d.setup_type == "LIQUIDITY_SWEEP_MSS"
    assert (
        d.setup_score == 95.0
        and d.setup_grade == "A+"
        and d.decision_confidence is DecisionConfidence.MODERATE
    )
    assert set(d.blockers) == set(plain.blockers) | {Blocker.RISK_GATE_MISSING, Blocker.NEWS_GATE_MISSING}


async def test_real_evaluation_enrichment_is_verdict_neutral():
    enriched_svc, plain_svc = _services()
    d = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert d.verdict == plain.verdict == Verdict.WAIT and d.direction is None and d.stop is None
    assert set(plain.blockers) <= set(d.blockers)
    assert d.decision_confidence in (DecisionConfidence.LOW, DecisionConfidence.MODERATE)


async def test_evaluation_failure_fails_safe(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("evaluation exploded")

    enriched_svc, plain_svc = _services(boom)
    d = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert d == plain.model_copy(update={"updated_at": d.updated_at})


async def test_scoring_engine_failure_is_reported(monkeypatch):
    calls = {"n": 0}
    real = scoring_service.evaluate

    def flaky(inputs, cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scoring exploded")
        return real(inputs, cfg)

    monkeypatch.setattr(scoring_service, "evaluate", flaky)
    candles = CandleService(NonSyntheticStub(), clock=lambda: MID_FIXTURE)
    ev = await EvaluationService(SetupService(candles)).evaluate("XAUUSD", MID_FIXTURE)
    assert ev.outcome is EvaluationOutcome.UNAVAILABLE and "EVALUATION_FAILED" in ev.ineligibility


def test_synthetic_decision_keeps_no_score(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["setupScore"] is None and d["setupGrade"] is None
    assert d["stop"] is None and d["tp1"] is None and d["preferredEntry"] is None


@pytest.mark.parametrize("model", [Setup, EntryPlan, ScoreItem, ScoreAdjustment, DecisionEvaluation])
def test_evaluation_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
