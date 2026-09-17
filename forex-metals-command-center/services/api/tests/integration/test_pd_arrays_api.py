import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import AnalysisIneligibility, QualifierStatus, Timeframe, Verdict
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.analysis import pipeline
from app.services.analysis.pipeline import run_pipeline
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.liquidity.models import LiquidityConfig
from app.services.market_state.service import MarketStateService
from app.services.pd_arrays.models import (
    DisplacementEvent,
    PdArrayAnalysis,
    PdArrayConfig,
    PdArrayEvent,
    PdArrayZone,
)
from app.services.pd_arrays.service import PdArrayService
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
def test_pd_arrays_endpoint_consistent_with_chart(fixture_client, tf):
    body = fixture_client.get("/api/v1/pd-arrays/XAUUSD", params={"timeframe": tf}).json()
    times = {
        c["time"]
        for c in fixture_client.get("/api/v1/candles/XAUUSD", params={"timeframe": tf}).json()["candles"]
    }
    assert body["timeframe"] == tf and body["strategyVersion"] == "0.19.0-phase19"
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["zones"] and body["displacements"]
    zone_ids = {z["id"] for z in body["zones"]}
    for z in body["zones"]:
        assert all(t in times for t in z["sourceTimes"]) and z["createdAt"] in times
        assert z["bottom"] < z["top"] and (z["qualityScore"] is None) == (not z["active"])
    assert all(e["zoneId"] in zone_ids and e["time"] in times for e in body["events"])
    assert all(d["time"] in times and d["legStart"] in times for d in body["displacements"])


def test_structure_events_carry_both_qualifiers(fixture_client):
    events = fixture_client.get("/api/v1/structure/XAUUSD", params={"timeframe": "M15"}).json()["events"]
    assert {e["displacementQualifier"] for e in events} <= {"PRESENT", "ABSENT"}
    assert {e["liquidityQualifier"] for e in events} <= {"PRESENT", "ABSENT"}
    assert "PRESENT" in {e["displacementQualifier"] for e in events}


def test_invalid_data_has_no_pd_analysis():
    raw = consecutive_bars(TUE_10_UTC, 80)
    raw[40] = bar(raw[40].open_time, o=2030, h=2029, low=2028, c=2030)
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw))
        .get("/api/v1/pd-arrays/XAUUSD", params={"timeframe": "M5"})
        .json()
    )
    assert body["zones"] == [] and body["displacements"] == [] and body["events"] == []
    assert "DATA_INVALID" in body["ineligibility"]


def test_pd_request_validation(fixture_client):
    assert fixture_client.get("/api/v1/pd-arrays/XAUUSD", params={"timeframe": "W1"}).status_code == 422
    assert fixture_client.get("/api/v1/pd-arrays/BTCUSD").status_code == 404


async def test_pd_failure_is_isolated_from_structure_and_liquidity(monkeypatch):
    candles = CandleService(NonSyntheticStub(), clock=lambda: AFTER_FIXTURE)
    series = await candles.load_series("XAUUSD", Timeframe.M15, 300, AFTER_FIXTURE)
    d1 = await candles.load_series("XAUUSD", Timeframe.D1, 30, AFTER_FIXTURE)
    sessions = await candles.load_series("XAUUSD", Timeframe.M15, 400, AFTER_FIXTURE)

    def boom(*_a, **_k):
        raise RuntimeError("fvg exploded")

    monkeypatch.setattr(pipeline, "detect_fvgs", boom)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.pd_arrays.ineligibility == [AnalysisIneligibility.PD_ARRAY_ANALYSIS_FAILED]
    assert result.pd_arrays.zones == [] and not result.pd_arrays.eligible_for_decision
    assert {e.displacement_qualifier for e in result.structure.events} == {QualifierStatus.NOT_EVALUATED}
    assert {e.liquidity_qualifier for e in result.structure.events} <= {
        QualifierStatus.PRESENT,
        QualifierStatus.ABSENT,
    }
    assert result.liquidity.eligible_for_decision and result.liquidity.pools


def _decisions(raise_in_pd=False):
    provider = NonSyntheticStub()
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    pd = PdArrayService(candles)
    if raise_in_pd:

        async def boom(*_a, **_k):
            raise RuntimeError("pd exploded")

        pd.decision_context = boom  # type: ignore[method-assign]
    clock = lambda: AFTER_FIXTURE  # noqa: E731
    enriched = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, pd_arrays=pd)
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain


async def test_displacement_enrichment_is_verdict_neutral():
    enriched_svc, plain_svc = _decisions()
    enriched = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert enriched.verdict == plain.verdict == Verdict.WAIT
    assert enriched.blockers == plain.blockers and enriched.data_quality == plain.data_quality
    assert enriched.displacement is not None and enriched.displacement.startswith("M15 ")
    assert any(g in enriched.displacement for g in ("MODERATE", "STRONG", "EXCEPTIONAL"))
    assert enriched.pd_array is None  # the entry PD array is chosen in Phase 8


async def test_displacement_failure_fails_safe():
    broken_svc, plain_svc = _decisions(raise_in_pd=True)
    broken = (await broken_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert (
        broken.verdict == plain.verdict and broken.blockers == plain.blockers and broken.displacement is None
    )


def test_synthetic_decision_gets_no_displacement(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["displacement"] is None


@pytest.mark.parametrize("model", [DisplacementEvent, PdArrayZone, PdArrayEvent, PdArrayAnalysis])
def test_pd_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_config_loads_and_rejects_unknown_invalidation_rule(monkeypatch):
    from app.contracts import load_spec as real_load_spec
    from app.services.pd_arrays import models

    assert PdArrayConfig.from_spec().grades_atr

    def patched(name):
        spec = real_load_spec(name)
        if name == "pd_arrays":
            spec = {**spec, "fvg": {**spec["fvg"], "invalidationRule": "WICK_THROUGH"}}
        return spec

    monkeypatch.setattr(models, "load_spec", patched)
    with pytest.raises(ValueError, match="CLOSE_THROUGH"):
        PdArrayConfig.from_spec()
