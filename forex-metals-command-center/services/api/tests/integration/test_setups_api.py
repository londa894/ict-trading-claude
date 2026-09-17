import json
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import AnalysisIneligibility, SetupState, Timeframe, Verdict
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.setup_state import service as setup_service
from app.services.setup_state.engine import ALLOWED
from app.services.setup_state.models import (
    BiasPoint,
    BiasState,
    BreakRef,
    LiquidityRef,
    Po3State,
    Setup,
    SetupAnalysis,
    SetupEvent,
    SetupStepState,
    TargetRef,
)
from app.services.setup_state.service import SetupService
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)
MID_FIXTURE = SERIES_END - timedelta(hours=30, minutes=-1)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"
PHASE7_STATES = {s.value for s in ALLOWED} | {"INVALIDATED", "EXPIRED"}


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


def test_setups_endpoint_on_synthetic_data_is_blocked(fixture_client):
    body = fixture_client.get("/api/v1/setups/XAUUSD").json()
    assert body["strategyVersion"] == "0.19.0-phase19" and body["timeframe"] == "M15"
    assert body["eligibleForDecision"] is False and "DATA_SYNTHETIC" in body["ineligibility"]
    assert body["currentState"] == "BLOCKED"
    assert body["bias"]["timeframes"] == ["H4", "H1"]
    candles = fixture_client.get("/api/v1/candles/XAUUSD", params={"timeframe": "M15"}).json()["candles"]
    times = {c["time"] for c in candles}
    ids = {s["id"] for s in body["setups"]}
    assert body["setups"] and all(e["setupId"] in ids and e["time"] in times for e in body["events"])
    assert {e["state"] for e in body["events"]} <= PHASE7_STATES
    assert fixture_client.get("/api/v1/setups/BTCUSD").status_code == 404


def test_invalid_data_has_no_setups():
    raw = [
        b.model_copy(update={"timeframe": Timeframe.M15})
        for b in consecutive_bars(TUE_10_UTC, 120, tf=Timeframe.M15)
    ]
    raw[60] = bar(raw[60].open_time, o=2030, h=2029, low=2028, c=2030, tf=Timeframe.M15)
    body = client(StaticProvider(raw), clock=lambda: after_last(raw)).get("/api/v1/setups/XAUUSD").json()
    assert body["setups"] == [] and body["events"] == [] and body["currentState"] == "BLOCKED"
    assert "DATA_INVALID" in body["ineligibility"]


async def test_eligible_analysis_on_real_like_data():
    svc = SetupService(CandleService(NonSyntheticStub(), clock=lambda: MID_FIXTURE))
    a = await svc.analyze("XAUUSD", MID_FIXTURE)
    assert a.eligible_for_decision and a.ineligibility == []
    assert a.bias.direction is not None and a.setups
    assert a.current is not None and a.current_state is a.current.state and not a.current.terminal
    assert a.current.next_required_event
    by_setup: dict[str, list[SetupState]] = {}
    for e in a.events:
        by_setup.setdefault(e.setup_id, []).append(e.state)
    assert all(b in ALLOWED[x] for seq in by_setup.values() for x, b in pairwise(seq))


async def test_setup_failure_is_reported_not_raised(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("setup engine exploded")

    monkeypatch.setattr(setup_service, "analyze_setups", boom)
    svc = SetupService(CandleService(NonSyntheticStub(), clock=lambda: MID_FIXTURE))
    a = await svc.analyze("XAUUSD", MID_FIXTURE)
    assert a.ineligibility == [AnalysisIneligibility.SETUP_ANALYSIS_FAILED]
    assert a.current_state is SetupState.BLOCKED and a.setups == []


def _services(clock_at, raise_in_setups=False):
    provider = NonSyntheticStub()
    svc = SetupService(CandleService(provider, clock=lambda: clock_at))
    if raise_in_setups:

        async def fail(*_a, **_k):
            raise RuntimeError("setup context exploded")

        svc.decision_context = fail  # type: ignore[method-assign]
    clock = lambda: clock_at  # noqa: E731
    enriched = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, setups=svc)
    plain = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock)
    return enriched, plain


async def test_setup_enrichment_is_verdict_neutral():
    enriched_svc, plain_svc = _services(AFTER_FIXTURE)
    enriched = (await enriched_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert enriched.verdict == plain.verdict == Verdict.WAIT and enriched.direction is None
    assert enriched.blockers == plain.blockers and enriched.data_quality == plain.data_quality
    assert enriched.setup_state in PHASE7_STATES | {"NO_SETUP"}
    assert (enriched.setup_type is None) == (enriched.setup_state == "NO_SETUP")
    assert plain.setup_state == "NOT_EVALUATED"


async def test_setup_failure_fails_safe():
    broken_svc, plain_svc = _services(AFTER_FIXTURE, raise_in_setups=True)
    broken = (await broken_svc.evaluate("XAUUSD")).decision
    plain = (await plain_svc.evaluate("XAUUSD")).decision
    assert broken.verdict == plain.verdict and broken.blockers == plain.blockers
    assert broken.setup_state == "NOT_EVALUATED" and broken.setup_type is None


def test_synthetic_decision_setup_state_not_evaluated(fixture_client):
    d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["setupState"] == "NOT_EVALUATED" and d["setupType"] is None


@pytest.mark.parametrize(
    "model",
    [
        BiasPoint,
        TargetRef,
        LiquidityRef,
        BreakRef,
        SetupStepState,
        Setup,
        SetupEvent,
        BiasState,
        Po3State,
        SetupAnalysis,
    ],
)
def test_setup_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
