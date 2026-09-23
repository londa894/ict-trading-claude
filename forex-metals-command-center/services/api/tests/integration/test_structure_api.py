from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import HtfBias, Timeframe, Verdict
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.structure.service import StructureService
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars
from tests.structure_helpers import cfg

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)


class NonSyntheticStub(SyntheticFixtureProvider):
    """Same deterministic prices, but presented as a real feed so eligibility rules can be exercised."""

    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


@pytest.mark.parametrize("tf", ["M5", "M15", "H1", "H4", "D1"])
def test_structure_endpoint_matches_chart_candles(fixture_client, tf):
    body = fixture_client.get("/api/v1/structure/XAUUSD", params={"timeframe": tf}).json()
    candles = fixture_client.get("/api/v1/candles/XAUUSD", params={"timeframe": tf}).json()["candles"]
    times = {c["time"] for c in candles}

    assert body["timeframe"] == tf and body["strategyVersion"] == "0.20.0-phase20"
    assert body["isSynthetic"] is True
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["internal"]["level"] == "INTERNAL" and body["external"]["level"] == "EXTERNAL"
    assert body["internal"]["pivotLength"] == 3 and body["external"]["pivotLength"] == 10
    # Every overlay anchor exists on the chart (same data, same window).
    for lvl in ("internal", "external"):
        for s in body[lvl]["swings"]:
            assert s["time"] in times
        for e in body[lvl]["events"]:
            assert e["time"] in times and e["brokenSwingTime"] in times
    events = body["events"]
    assert len(events) == len(body["internal"]["events"]) + len(body["external"]["events"])
    assert [e["time"] for e in events] == sorted(e["time"] for e in events)


def test_structure_withheld_for_invalid_data():
    raw = consecutive_bars(TUE_10_UTC, 80)
    raw[40] = bar(raw[40].open_time, o=2030, h=2029, low=2028, c=2030)
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw))
        .get("/api/v1/structure/XAUUSD", params={"timeframe": "M5"})
        .json()
    )
    assert body["quality"] == "INVALID"
    assert body["internal"] is None and body["external"] is None and body["events"] == []
    assert "DATA_INVALID" in body["ineligibility"] and body["eligibleForDecision"] is False


def test_stale_data_analysed_but_not_eligible():
    raw = consecutive_bars(TUE_10_UTC, 80)
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw) + timedelta(hours=3))
        .get("/api/v1/structure/XAUUSD", params={"timeframe": "M5"})
        .json()
    )
    assert body["quality"] == "STALE" and body["external"] is not None
    assert body["ineligibility"] == ["DATA_STALE"]


def test_structure_request_validation(fixture_client):
    assert fixture_client.get("/api/v1/structure/XAUUSD", params={"timeframe": "MN1"}).status_code == 422
    assert fixture_client.get("/api/v1/structure/XAUUSD", params={"limit": 0}).status_code == 422
    assert fixture_client.get("/api/v1/structure/BTCUSD").status_code == 404
    assert fixture_client.get("/api/v1/structure/BTCUSD/alignment").status_code == 404


def test_alignment_endpoint(fixture_client):
    body = fixture_client.get("/api/v1/structure/XAUUSD/alignment").json()
    assert [t["timeframe"] for t in body["timeframes"]] == ["D1", "H4", "H1", "M15", "M5"]
    assert body["alignment"] in {"ALIGNED_BULLISH", "ALIGNED_BEARISH", "MIXED", "UNCLEAR"}
    assert body["htfBias"] == "UNKNOWN"  # synthetic data is never eligible
    assert body["eligibleForDecision"] is False


def test_decision_on_synthetic_data_has_no_structure_context(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE"
    assert d["htfBias"] == "UNKNOWN" and d["structureEvent"] is None


def _services(provider, structure_cfg=None, raise_in_structure=False):
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    structure = StructureService(candles, cfg=structure_cfg)
    if raise_in_structure:

        async def boom(*_a, **_k):
            raise RuntimeError("structure exploded")

        structure.decision_context = boom  # type: ignore[method-assign]
    with_structure = MarketStateService(
        provider, InMemoryEventBus(), Timeframe.M5, lambda: AFTER_FIXTURE, structure
    )
    without = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, lambda: AFTER_FIXTURE)
    return with_structure, without


async def test_eligible_structure_enriches_decision_but_never_changes_verdict():
    provider = NonSyntheticStub()
    with_structure, without = _services(
        provider, structure_cfg=cfg(pivot=3, min_candles=20, ranging_bars=200)
    )
    enriched = (await with_structure.evaluate("XAUUSD")).decision
    plain = (await without.evaluate("XAUUSD")).decision

    assert (
        enriched.verdict == plain.verdict == Verdict.WAIT
    )  # Friday after close -> MARKET_CLOSED, still WAIT
    assert enriched.blockers == plain.blockers
    assert enriched.data_quality == plain.data_quality
    assert enriched.htf_bias != HtfBias.UNKNOWN
    assert enriched.htf_bias in {b.value for b in HtfBias}
    assert enriched.structure_event is not None and enriched.structure_event.startswith("M15 ")
    assert enriched.direction is None and enriched.stop is None


async def test_structure_with_insufficient_htf_candles_is_unknown():
    provider = NonSyntheticStub()  # fixture has ~40 D1 candles < spec minCandles 50
    with_structure, _ = _services(provider)
    decision = (await with_structure.evaluate("XAUUSD")).decision
    assert decision.htf_bias == HtfBias.UNKNOWN


async def test_structure_failure_fails_safe_without_touching_verdict():
    provider = NonSyntheticStub()
    with_structure, without = _services(provider, raise_in_structure=True)
    broken = (await with_structure.evaluate("XAUUSD")).decision
    plain = (await without.evaluate("XAUUSD")).decision
    assert broken.verdict == plain.verdict and broken.blockers == plain.blockers
    assert broken.htf_bias == HtfBias.UNKNOWN and broken.structure_event is None


async def test_unavailable_decision_skips_structure_entirely():
    calls = []

    class Spy(StructureService):
        async def decision_context(self, symbol, now=None):
            calls.append(symbol)
            return await super().decision_context(symbol, now)

    provider = SyntheticFixtureProvider()  # synthetic -> UNAVAILABLE
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    svc = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, lambda: AFTER_FIXTURE, Spy(candles))
    d = (await svc.evaluate("XAUUSD")).decision
    assert d.verdict == Verdict.UNAVAILABLE and calls == []
