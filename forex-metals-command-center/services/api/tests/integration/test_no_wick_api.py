import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import (
    AnalysisIneligibility,
    NoWickContextFactor,
    ScoreComponentStatus,
    Timeframe,
    Verdict,
)
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.analysis import pipeline
from app.services.analysis.pipeline import run_pipeline
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.liquidity.models import LiquidityConfig
from app.services.market_state.service import MarketStateService
from app.services.no_wick.models import (
    CandleFeatures,
    NoWickAnalysis,
    NoWickEvent,
    NoWickZone,
    NoWickZoneEvent,
    ScoreComponent,
)
from app.services.no_wick.service import NoWickService
from app.services.structure.models import StructureConfig
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)
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


@pytest.mark.parametrize("tf", ["M5", "M15", "H1"])
def test_no_wick_endpoint_consistent_with_chart(fixture_client, tf):
    body = fixture_client.get("/api/v1/no-wick/XAUUSD", params={"timeframe": tf}).json()
    times = {
        c["time"]
        for c in fixture_client.get("/api/v1/candles/XAUUSD", params={"timeframe": tf}).json()["candles"]
    }
    assert body["timeframe"] == tf and body["strategyVersion"] == "0.20.0-phase20"
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["events"] and body["zones"] and len(body["features"]) == body["candleCount"]
    zone_ids = {z["id"] for z in body["zones"]}
    event_ids = {e["id"] for e in body["events"]}
    for e in body["events"]:
        assert e["time"] in times and len(e["contextComponents"]) == len(NoWickContextFactor)
        assert e["classification"] != "NEWS_DRIVEN_NO_WICK"
    for z in body["zones"]:
        assert z["createdAt"] in times and z["eventId"] in event_ids and z["obOverlap"] == "NOT_EVALUATED"
    assert all(e["zoneId"] in zone_ids and e["time"] in times for e in body["zoneEvents"])


def test_invalid_data_has_no_no_wick_analysis():
    raw = consecutive_bars(TUE_10_UTC, 80)
    raw[40] = bar(raw[40].open_time, o=2030, h=2029, low=2028, c=2030)
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw))
        .get("/api/v1/no-wick/XAUUSD", params={"timeframe": "M5"})
        .json()
    )
    assert body["events"] == [] and body["zones"] == [] and body["features"] == []
    assert "DATA_INVALID" in body["ineligibility"]


def test_no_wick_request_validation(fixture_client):
    assert fixture_client.get("/api/v1/no-wick/XAUUSD", params={"timeframe": "MN1"}).status_code == 422
    assert fixture_client.get("/api/v1/no-wick/BTCUSD").status_code == 404


async def _inputs():
    candles = CandleService(NonSyntheticStub(), clock=lambda: AFTER_FIXTURE)
    series = await candles.load_series("XAUUSD", Timeframe.M15, 300, AFTER_FIXTURE)
    d1 = await candles.load_series("XAUUSD", Timeframe.D1, 30, AFTER_FIXTURE)
    return series, d1, await candles.load_series("XAUUSD", Timeframe.M15, 400, AFTER_FIXTURE)


def boom(*_a, **_k):
    raise RuntimeError("exploded")


async def test_no_wick_failure_is_isolated(monkeypatch):
    series, d1, sessions = await _inputs()
    monkeypatch.setattr(pipeline, "analyze_no_wick", boom)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.no_wick.ineligibility == [AnalysisIneligibility.NO_WICK_ANALYSIS_FAILED]
    assert result.no_wick.events == [] and not result.no_wick.eligible_for_decision
    assert result.pd_arrays.eligible_for_decision and result.pd_arrays.zones
    assert result.liquidity.eligible_for_decision and result.structure.events


async def test_pd_failure_marks_no_wick_components_not_evaluated(monkeypatch):
    series, d1, sessions = await _inputs()
    monkeypatch.setattr(pipeline, "detect_fvgs", boom)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.no_wick.eligible_for_decision and result.no_wick.events
    for e in result.no_wick.events:
        status = {c.factor: c.status for c in e.context_components}
        assert status[NoWickContextFactor.FVG] is ScoreComponentStatus.NOT_EVALUATED
        assert status[NoWickContextFactor.DISPLACEMENT] is ScoreComponentStatus.NOT_EVALUATED
        assert status[NoWickContextFactor.STRUCTURE] is ScoreComponentStatus.EVALUATED
        assert status[NoWickContextFactor.LIQUIDITY] is ScoreComponentStatus.EVALUATED
    assert all(z.fvg_overlap_ids == [] for z in result.no_wick.zones)


def _services(raise_in_no_wick=False):
    provider = NonSyntheticStub()
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    nw = NoWickService(candles)
    if raise_in_no_wick:

        async def fail(*_a, **_k):
            raise RuntimeError("no-wick exploded")

        nw.decision_context = fail  # type: ignore[method-assign]
    clock = lambda: AFTER_FIXTURE  # noqa: E731
    enriched = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, no_wick=nw)
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain


async def test_no_wick_enrichment_is_verdict_neutral():
    enriched_svc, plain_svc = _services()
    enriched = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert enriched.verdict == plain.verdict == Verdict.WAIT
    assert enriched.blockers == plain.blockers and enriched.data_quality == plain.data_quality
    state = enriched.no_wick_state
    assert state is not None and state["timeframe"] == "M15" and state["authority"] == "CONTEXT_ONLY"
    assert state["strength"] in ("MEANINGFUL", "STRONG", "EXCEPTIONAL")
    assert plain.no_wick_state is None


async def test_no_wick_failure_fails_safe():
    broken_svc, plain_svc = _services(raise_in_no_wick=True)
    broken = (await broken_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert broken.verdict == plain.verdict and broken.blockers == plain.blockers
    assert broken.no_wick_state is None


def test_synthetic_decision_gets_no_no_wick_state(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["noWickState"] is None


@pytest.mark.parametrize(
    "model", [CandleFeatures, ScoreComponent, NoWickEvent, NoWickZone, NoWickZoneEvent, NoWickAnalysis]
)
def test_no_wick_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
