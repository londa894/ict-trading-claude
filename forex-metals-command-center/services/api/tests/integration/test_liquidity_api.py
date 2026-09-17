import math
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import (
    AnalysisIneligibility,
    Blocker,
    DolConfidence,
    QualifierStatus,
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
from app.services.liquidity.service import LiquidityService
from app.services.market_state.service import MarketStateService
from app.services.structure.models import StructureConfig
from app.services.structure.service import StructureService
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


@pytest.mark.parametrize("tf", ["M5", "M15", "H1", "H4"])
def test_liquidity_endpoint_consistent_with_chart(fixture_client, tf):
    body = fixture_client.get("/api/v1/liquidity/XAUUSD", params={"timeframe": tf}).json()
    times = {
        c["time"]
        for c in fixture_client.get("/api/v1/candles/XAUUSD", params={"timeframe": tf}).json()["candles"]
    }
    assert body["timeframe"] == tf and body["strategyVersion"] == "0.19.0-phase19"
    assert body["keyLevelsAvailable"] is True
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["pools"] and body["dol"] is not None
    assert all(e["time"] in times for e in body["events"])
    assert [e["time"] for e in body["events"]] == sorted(e["time"] for e in body["events"])
    pool_ids = {p["id"] for p in body["pools"]}
    assert all(e["poolId"] in pool_ids for e in body["events"])
    for p in body["pools"]:
        assert math.isfinite(p["price"]) and (p["magnetScore"] is None) == p["taken"]
    types = {p["type"] for p in body["pools"]}
    assert {"PDH", "PDL"} <= types  # key levels from NY-close D1 candles
    dol = body["dol"]
    assert dol["confidence"] in {c.value for c in DolConfidence}
    if dol["primary"]:
        assert dol["primary"]["poolId"] in pool_ids


def test_structure_events_now_carry_liquidity_qualifiers(fixture_client):
    body = fixture_client.get("/api/v1/structure/XAUUSD", params={"timeframe": "M15"}).json()
    quals = {e["liquidityQualifier"] for e in body["events"]}
    assert quals and quals <= {"PRESENT", "ABSENT"}
    assert {e["displacementQualifier"] for e in body["events"]} <= {"PRESENT", "ABSENT"}  # filled in Phase 4


def test_invalid_data_has_no_liquidity_analysis():
    raw = consecutive_bars(TUE_10_UTC, 80)
    raw[40] = bar(raw[40].open_time, o=2030, h=2029, low=2028, c=2030)
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw))
        .get("/api/v1/liquidity/XAUUSD", params={"timeframe": "M5"})
        .json()
    )
    assert body["pools"] == [] and body["events"] == [] and body["dol"] is None
    assert body["eligibleForDecision"] is False and "DATA_INVALID" in body["ineligibility"]


def test_liquidity_request_validation(fixture_client):
    assert fixture_client.get("/api/v1/liquidity/XAUUSD", params={"timeframe": "W1"}).status_code == 422
    assert fixture_client.get("/api/v1/liquidity/XAUUSD", params={"limit": 5000}).status_code == 422
    assert fixture_client.get("/api/v1/liquidity/BTCUSD").status_code == 404


async def _series(provider, tf=Timeframe.H1):
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    return await candles.load_series("XAUUSD", tf, 300, AFTER_FIXTURE), await candles.load_series(
        "XAUUSD", Timeframe.D1, 30, AFTER_FIXTURE
    )


async def test_missing_key_levels_make_liquidity_ineligible():
    series, _ = await _series(NonSyntheticStub())
    result = run_pipeline(series, None, StructureConfig.from_spec(), LiquidityConfig.from_spec())
    assert AnalysisIneligibility.KEY_LEVELS_UNAVAILABLE in result.liquidity.ineligibility
    assert not result.liquidity.eligible_for_decision


async def test_liquidity_failure_keeps_structure_and_fails_safe(monkeypatch):
    series, d1 = await _series(NonSyntheticStub())
    sessions = await CandleService(NonSyntheticStub(), clock=lambda: AFTER_FIXTURE).load_series(
        "XAUUSD", Timeframe.M15, 400, AFTER_FIXTURE
    )

    def boom(*_a, **_k):
        raise RuntimeError("liquidity exploded")

    monkeypatch.setattr(pipeline, "analyze_liquidity", boom)
    result = run_pipeline(
        series, d1, StructureConfig.from_spec(), LiquidityConfig.from_spec(), sessions=sessions
    )
    assert result.structure.external is not None
    assert {e.liquidity_qualifier for e in result.structure.events} == {QualifierStatus.NOT_EVALUATED}
    assert result.liquidity.ineligibility == [AnalysisIneligibility.LIQUIDITY_ANALYSIS_FAILED]
    assert result.liquidity.dol is None and not result.liquidity.eligible_for_decision


def _services(provider, raise_in_liquidity=False):
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    liquidity = LiquidityService(candles)
    if raise_in_liquidity:

        async def boom(*_a, **_k):
            raise RuntimeError("liquidity exploded")

        liquidity.decision_context = boom  # type: ignore[method-assign]
    clock = lambda: AFTER_FIXTURE  # noqa: E731
    enriched = MarketStateService(
        provider, InMemoryEventBus(), Timeframe.M5, clock, StructureService(candles), liquidity
    )
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain, liquidity


async def test_liquidity_enrichment_never_changes_verdict_and_only_adds_dol_unclear():
    provider = NonSyntheticStub()
    enriched_svc, plain_svc, liquidity = _services(provider)
    enriched = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    ctx = await liquidity.decision_context("XAUUSD", AFTER_FIXTURE)

    assert enriched.verdict == plain.verdict == Verdict.WAIT
    assert enriched.data_quality == plain.data_quality
    assert set(enriched.blockers) - set(plain.blockers) <= {Blocker.DOL_UNCLEAR}
    assert set(plain.blockers) <= set(enriched.blockers)
    assert (Blocker.DOL_UNCLEAR in enriched.blockers) == ctx.dol_unclear
    assert ctx.eligible
    assert enriched.primary_dol is not None and enriched.primary_dol.startswith("H1 ")
    assert enriched.direction is None and enriched.stop is None and enriched.tp1 is None


async def test_dol_unclear_blocker_when_two_sided(monkeypatch):
    provider = NonSyntheticStub()
    enriched_svc, _plain, liquidity = _services(provider)
    original = liquidity.decision_context

    async def unclear(symbol, now=None):
        ctx = await original(symbol, now)
        return type(ctx)(
            eligible=True,
            primary_dol=ctx.primary_dol,
            secondary_dol=ctx.secondary_dol,
            dol_confidence=DolConfidence.UNCLEAR,
            liquidity_event=ctx.liquidity_event,
        )

    liquidity.decision_context = unclear  # type: ignore[method-assign]
    d = (await enriched_svc.evaluate("XAUUSD")).decision
    assert Blocker.DOL_UNCLEAR in d.blockers and d.verdict == Verdict.WAIT
    order = list(Blocker)
    assert d.blockers == sorted(d.blockers, key=order.index)


async def test_liquidity_failure_in_decision_fails_safe():
    provider = NonSyntheticStub()
    enriched_svc, plain_svc, _ = _services(provider, raise_in_liquidity=True)
    broken = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert broken.verdict == plain.verdict
    assert broken.primary_dol is None and broken.secondary_dol is None and broken.liquidity_event is None
    assert Blocker.DOL_UNCLEAR not in broken.blockers


def test_synthetic_decision_gets_no_liquidity_context(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE"
    assert d["primaryDol"] is None and d["liquidityEvent"] is None and "DOL_UNCLEAR" not in d["blockers"]
