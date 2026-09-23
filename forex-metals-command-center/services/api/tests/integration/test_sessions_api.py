import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import (
    AnalysisIneligibility,
    LiquidityPoolType,
    NoWickContextFactor,
    ScoreComponentStatus,
    Timeframe,
    Verdict,
)
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.analysis.pipeline import run_pipeline
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.liquidity.models import LiquidityConfig
from app.services.market_state.service import MarketStateService
from app.services.sessions import analysis as session_analysis
from app.services.sessions.models import (
    AdrState,
    JudasSwing,
    PreviousSession,
    SessionAnalysis,
    SessionClock,
    SessionInstance,
    SessionOpens,
)
from app.services.sessions.service import SessionService
from app.services.structure.models import StructureConfig
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"
SESSION_POOLS = {
    LiquidityPoolType.ASIA_HIGH,
    LiquidityPoolType.ASIA_LOW,
    LiquidityPoolType.LONDON_HIGH,
    LiquidityPoolType.LONDON_LOW,
    LiquidityPoolType.NY_AM_HIGH,
    LiquidityPoolType.NY_AM_LOW,
    LiquidityPoolType.NY_PM_HIGH,
    LiquidityPoolType.NY_PM_LOW,
}


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


def test_sessions_endpoint(fixture_client):
    body = fixture_client.get("/api/v1/sessions/XAUUSD").json()
    assert body["strategyVersion"] == "0.20.0-phase20" and body["sourceTimeframe"] == "M15"
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["clock"]["marketStatus"] == "CLOSED" and body["clock"]["timeQuality"] == "AVOID"
    assert body["clock"]["nextSession"] == "ASIA"
    states = {i["state"] for i in body["instances"]}
    assert "COMPLETE" in states and {i["session"] for i in body["instances"]} == {
        "ASIA",
        "LONDON",
        "NY_AM",
        "NY_PM",
        "LONDON_CLOSE",
    }
    for i in body["instances"]:
        if i["state"] == "COMPLETE":
            assert i["knownAt"] == i["end"] and i["candleCount"] == i["expectedCount"]
    assert body["opens"]["dailyOpen"] is not None and body["adr"] is not None
    assert body["previousSession"]["session"] == "NY_PM"
    assert {j["status"] for j in body["judas"]} <= {"CANDIDATE", "CONFIRMED", "FAILED"}


def test_invalid_data_keeps_the_clock_but_no_levels():
    raw = [
        b.model_copy(update={"timeframe": Timeframe.M15})
        for b in consecutive_bars(TUE_10_UTC, 80, tf=Timeframe.M15)
    ]
    raw[40] = bar(raw[40].open_time, o=2030, h=2029, low=2028, c=2030, tf=Timeframe.M15)
    body = client(StaticProvider(raw), clock=lambda: after_last(raw)).get("/api/v1/sessions/XAUUSD").json()
    assert body["instances"] == [] and body["judas"] == [] and body["adr"] is None
    assert body["opens"]["dailyOpen"] is None and body["sessionQuality"] is None
    assert "DATA_INVALID" in body["ineligibility"] and body["clock"]["tradingDay"] == "2024-01-10"


def test_sessions_request_validation(fixture_client):
    assert fixture_client.get("/api/v1/sessions/BTCUSD").status_code == 404


async def test_session_analysis_failure_is_reported_not_raised(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("sessions exploded")

    monkeypatch.setattr(session_analysis, "build_instances", boom)
    a = await SessionService(CandleService(NonSyntheticStub(), clock=lambda: AFTER_FIXTURE)).analyze("XAUUSD")
    assert a.ineligibility == [AnalysisIneligibility.SESSION_ANALYSIS_FAILED] and not a.eligible_for_decision
    assert a.instances == [] and a.clock.trading_day is not None


async def _inputs(tf=Timeframe.M15):
    candles = CandleService(NonSyntheticStub(), clock=lambda: AFTER_FIXTURE)
    series = await candles.load_series("XAUUSD", tf, 300, AFTER_FIXTURE)
    d1 = await candles.load_series("XAUUSD", Timeframe.D1, 30, AFTER_FIXTURE)
    sessions = await candles.load_series("XAUUSD", Timeframe.M15, 400, AFTER_FIXTURE)
    return series, d1, sessions


@pytest.mark.parametrize("tf", [Timeframe.M5, Timeframe.H1])
async def test_liquidity_gets_session_pools_and_no_wick_gets_session_context(tf):
    series, d1, sessions = await _inputs(tf)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.liquidity.eligible_for_decision
    pools = [p for p in result.liquidity.pools if p.type in SESSION_POOLS]
    assert {p.type for p in pools} == SESSION_POOLS
    assert all(p.known_at <= series.candles[-1].close_time for p in pools)
    for e in result.no_wick.events:
        [session] = [c for c in e.context_components if c.factor is NoWickContextFactor.SESSION]
        assert session.status is ScoreComponentStatus.EVALUATED


async def test_no_wick_session_context_is_not_evaluated_above_h1():
    series, d1, sessions = await _inputs(Timeframe.H4)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.no_wick.events
    for e in result.no_wick.events:
        [session] = [c for c in e.context_components if c.factor is NoWickContextFactor.SESSION]
        assert session.status is ScoreComponentStatus.NOT_EVALUATED


async def test_missing_session_source_makes_liquidity_ineligible_only():
    series, d1, _ = await _inputs()
    result = run_pipeline(series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec())
    assert result.liquidity.ineligibility == [AnalysisIneligibility.SESSION_LEVELS_UNAVAILABLE]
    assert not [p for p in result.liquidity.pools if p.type in SESSION_POOLS] and result.liquidity.pools
    assert result.pd_arrays.eligible_for_decision and result.no_wick.eligible_for_decision


def _services(raise_in_sessions=False):
    provider = NonSyntheticStub()
    svc = SessionService(CandleService(provider, clock=lambda: AFTER_FIXTURE))
    if raise_in_sessions:

        async def fail(*_a, **_k):
            raise RuntimeError("session context exploded")

        svc.decision_context = fail  # type: ignore[method-assign]
    clock = lambda: AFTER_FIXTURE  # noqa: E731
    enriched = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, sessions=svc)
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain


async def test_session_enrichment_is_verdict_neutral():
    enriched_svc, plain_svc = _services()
    enriched = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert enriched.verdict == plain.verdict == Verdict.WAIT
    assert enriched.blockers == plain.blockers and enriched.data_quality == plain.data_quality
    state = enriched.session_state
    assert state is not None and state["authority"] == "CONTEXT_ONLY" and state["timeQuality"] == "AVOID"
    assert state["marketStatus"] == "CLOSED" and plain.session_state is None


async def test_session_failure_fails_safe():
    broken_svc, plain_svc = _services(raise_in_sessions=True)
    broken = (await broken_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert (
        broken.verdict == plain.verdict and broken.blockers == plain.blockers and broken.session_state is None
    )


def test_synthetic_decision_gets_no_session_state(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["sessionState"] is None


@pytest.mark.parametrize(
    "model",
    [SessionClock, SessionInstance, SessionOpens, PreviousSession, AdrState, JudasSwing, SessionAnalysis],
)
def test_session_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
