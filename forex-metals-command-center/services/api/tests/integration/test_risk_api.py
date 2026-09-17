"""Risk API, profile store and decision integration (Phase 9)."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Blocker, EvaluationOutcome, RiskStatus, Timeframe, Verdict
from app.domain.instrument import InstrumentSpec
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.risk import service as risk_service
from app.services.risk.engine import not_assessed
from app.services.risk.models import (
    AccountProfile,
    AccountState,
    OpenPosition,
    PositionSize,
    PropRules,
    RiskAssessment,
    RiskBudget,
    RiskCalculationRequest,
    RiskLimits,
    RiskLockItem,
    VolatilityState,
)
from app.services.risk.service import RiskService
from app.services.risk.store import ProfileLoadStatus, RiskProfileStore
from app.services.scoring.service import EvaluationService
from app.services.sessions.clock import trading_day_of
from app.services.setup_state.service import SetupService
from tests.integration.test_evaluation_api import _confirmed_evaluation
from tests.risk_helpers import spec

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def profile(**over):
    body = {
        "account": {"balance": 25_000, "currency": "USD", "profile": "STANDARD", "leverage": 100},
        "state": {
            "tradingDay": trading_day_of(AFTER_FIXTURE).isoformat(),
            "realizedPnlToday": 0,
            "realizedPnlWeek": 0,
            "tradesToday": 0,
            "consecutiveLosses": 0,
            "openPositions": [],
        },
        "instrumentSpecs": {"XAUUSD": spec().model_dump(by_alias=True)},
    }
    body.update(over)
    return body


def write(tmp_path: Path, body) -> Path:
    path = tmp_path / "risk_profile.local.json"
    path.write_text(json.dumps(body) if not isinstance(body, str) else body, encoding="utf-8")
    return path


def client(path: Path | None = None, provider=None):
    settings = Settings(_env_file=None, risk_profile_path=str(path) if path else "")  # type: ignore[call-arg]
    app = create_app(settings=settings, provider=provider or NonSyntheticStub(), clock=lambda: AFTER_FIXTURE)
    return TestClient(app)


def test_store_states_and_reload(tmp_path):
    assert RiskProfileStore(None).load().status is ProfileLoadStatus.MISSING
    assert RiskProfileStore(tmp_path / "nope.json").load().status is ProfileLoadStatus.MISSING
    path = write(tmp_path, "{not json")
    store = RiskProfileStore(path)
    assert store.load().status is ProfileLoadStatus.INVALID
    write(tmp_path, profile(account={"balance": -123456.78, "currency": "USD", "profile": "STANDARD"}))
    os.utime(path, ns=(1, 1))  # force a new mtime on coarse filesystems
    bad = store.load()
    assert bad.status is ProfileLoadStatus.INVALID and "account.balance" in (bad.error or "")
    assert "123456" not in (bad.error or "")  # values are never echoed
    write(tmp_path, profile())
    os.utime(path, ns=(2, 2))
    assert store.load().status is ProfileLoadStatus.OK and store.load().file is not None


def test_no_profile_blocks_the_decision_with_risk_profile_missing():
    c = client()
    risk = c.get("/api/v1/risk/XAUUSD").json()
    assert risk["status"] == "NOT_CONFIGURED" and risk["blockers"] == ["RISK_PROFILE_MISSING"]
    assert risk["authority"] == "NOT_AUTHORIZED" and risk["position"] is None
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert (
        d["verdict"] == "WAIT"
        and d["riskStatus"] == "NOT_CONFIGURED"
        and "RISK_PROFILE_MISSING" in d["blockers"]
    )
    assert c.get("/api/v1/risk/BTCUSD").status_code == 404


def test_a_valid_profile_clears_risk_and_supplies_the_spec(tmp_path):
    c = client(write(tmp_path, profile()))
    risk = c.get("/api/v1/risk/XAUUSD").json()
    assert risk["status"] == "CLEAR" and risk["locks"] == [] and risk["position"] is None  # no confirmed plan
    assert risk["volatility"]["timeframe"] == "M15"
    assert risk["budget"]["riskPerTradeAmount"] == 250.0 and risk["limits"]["riskPerTradePct"] == 1.0
    ev = c.get("/api/v1/evaluation/XAUUSD").json()
    assert ev["missingGates"] == [] and ev["risk"]["status"] == risk["status"]
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "WAIT" and d["riskStatus"] == risk["status"] and d["stop"] is None
    assert "RISK_PROFILE_MISSING" not in d["blockers"]
    assert ev["eligibleForDecision"] and "INSTRUMENT_SPEC_MISSING" not in d["blockers"]  # user-supplied spec


def test_locked_and_invalid_profiles(tmp_path):
    locked = profile(state={**profile()["state"], "consecutiveLosses": 3})
    c = client(write(tmp_path, locked))
    risk = c.get("/api/v1/risk/XAUUSD").json()
    assert risk["status"] == "LOCKED" and "CONSECUTIVE_LOSSES" in [x["lock"] for x in risk["locks"]]
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "WAIT" and d["riskStatus"] == "LOCKED" and "RISK_LOCKED" in d["blockers"]

    stale = profile(state={**profile()["state"], "tradingDay": "2020-01-01"})
    sub = tmp_path / "stale"
    sub.mkdir()
    assert "ACCOUNT_STATE_STALE" in [
        x["lock"] for x in client(write(sub, stale)).get("/api/v1/risk/XAUUSD").json()["locks"]
    ]

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    bad = client(write(invalid, profile(account={"balance": 1000, "currency": "USD", "profile": "CUSTOM"})))
    risk = bad.get("/api/v1/risk/XAUUSD").json()
    assert risk["status"] == "INVALID_PROFILE" and risk["blockers"] == ["RISK_PROFILE_INVALID"]
    assert "RISK_PROFILE_INVALID" in bad.get("/api/v1/market-state/XAUUSD").json()["decision"]["blockers"]


def calc_body(**over):
    body = {
        "symbol": "XAUUSD",
        "direction": "BULLISH",
        "entry": 2032.0,
        "stop": 2028.8,
        "account": {"balance": 10_000, "currency": "USD", "profile": "STANDARD", "leverage": 100},
        "instrumentSpec": spec().model_dump(by_alias=True),
    }
    body.update(over)
    return body


def test_what_if_calculator_sizes_and_stores_nothing():
    c = client()
    r = c.post("/api/v1/risk/calculate", json=calc_body())
    assert r.status_code == 200
    a = r.json()
    assert a["status"] == "WITHIN_LIMITS" and a["sizeStatus"] == "SIZED_FROM_USER_SPEC"
    assert a["position"]["volume"] == 0.28 and a["position"]["riskAmount"] == 98.0
    assert {"VOLATILITY_NOT_CHECKED", "ACCOUNT_STATE_NOT_PROVIDED", "USER_SUPPLIED_SPEC"} <= set(
        a["warnings"]
    )
    assert c.get("/api/v1/risk/XAUUSD").json()["status"] == "NOT_CONFIGURED"  # nothing was stored

    eur = c.post(
        "/api/v1/risk/calculate",
        json=calc_body(account={"balance": 10_000, "currency": "EUR", "profile": "STANDARD"}),
    )
    assert eur.json()["status"] == "SIZE_UNVERIFIED"
    converted = c.post(
        "/api/v1/risk/calculate",
        json=calc_body(
            account={"balance": 10_000, "currency": "EUR", "profile": "STANDARD"}, conversionRate=0.9
        ),
    )
    assert converted.json()["position"]["riskPerVolume"] == 315.0
    no_spec = c.post("/api/v1/risk/calculate", json=calc_body(instrumentSpec=None)).json()
    assert no_spec["status"] == "SIZE_UNVERIFIED" and no_spec["blockers"] == ["POSITION_SIZE_UNVERIFIED"]


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"symbol": "BTCUSD"}, 404),
        ({"instrumentSpec": {**spec().model_dump(by_alias=True), "symbol": "XAGUSD"}}, 422),
        ({"entry": -1}, 422),
        (
            {"account": {"balance": 10_000, "currency": "USD", "profile": "STANDARD", "riskPerTradePct": 5}},
            422,
        ),
        ({"direction": "LONG"}, 422),
    ],
)
def test_calculator_rejects_bad_requests(over, code):
    assert client().post("/api/v1/risk/calculate", json=calc_body(**over)).status_code == code


def test_cors_allows_the_calculator_post():
    r = client().options(
        "/api/v1/risk/calculate",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200 and "POST" in r.headers["access-control-allow-methods"]


def test_risk_failure_fails_safe(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("risk exploded")

    monkeypatch.setattr(risk_service, "assess", boom)
    svc = RiskService(RiskProfileStore(write(tmp_path, profile())))
    a = svc.assess("XAUUSD", AFTER_FIXTURE)
    assert a.status is RiskStatus.UNAVAILABLE and a.blockers == [Blocker.RISK_GATE_MISSING]


def _locked_risk() -> RiskAssessment:
    base = not_assessed("XAUUSD", AFTER_FIXTURE, RiskStatus.NOT_CONFIGURED, None)
    return base.model_copy(
        update={
            "status": RiskStatus.LOCKED,
            "locks": [RiskLockItem(lock="DAILY_LOSS_LIMIT", detail="x")],
            "blockers": [Blocker.RISK_LOCKED],
        }
    )


async def test_a_risk_veto_on_a_confirmed_plan_never_changes_the_verdict():
    vetoed = _confirmed_evaluation().model_copy(
        update={
            "outcome": EvaluationOutcome.NO_TRADE,
            "risk": _locked_risk(),
            "hard_blockers": [Blocker.INSTRUMENT_SPEC_MISSING, Blocker.RISK_LOCKED],
            "missing_gates": [Blocker.NEWS_GATE_MISSING],
        }
    )

    async def fake(*_a, **_k):
        return vetoed

    provider = NonSyntheticStub()
    candles = CandleService(provider, clock=lambda: AFTER_FIXTURE)
    evaluation = EvaluationService(SetupService(candles))
    evaluation.evaluate = fake  # type: ignore[method-assign]
    svc = MarketStateService(
        provider, InMemoryEventBus(), Timeframe.M5, lambda: AFTER_FIXTURE, evaluation=evaluation
    )
    d = (await svc.evaluate("XAUUSD")).decision
    assert d.verdict is Verdict.WAIT and d.direction is None and d.stop is None and d.preferred_entry is None
    assert d.risk_status == "LOCKED" and Blocker.RISK_LOCKED in d.blockers
    assert Blocker.NEWS_GATE_MISSING not in d.blockers  # only a CONFIRMED_PENDING_GATES outcome lists gates


@pytest.mark.parametrize(
    "model",
    [
        RiskLimits,
        RiskBudget,
        RiskLockItem,
        VolatilityState,
        PositionSize,
        RiskAssessment,
        PropRules,
        AccountProfile,
        OpenPosition,
        AccountState,
        InstrumentSpec,
        RiskCalculationRequest,
    ],
)
def test_risk_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_validation_errors_never_echo_submitted_values():
    body = calc_body(account={"balance": -123456.78, "currency": "USD", "profile": "STANDARD"})
    r = client().post("/api/v1/risk/calculate", json=body)
    assert r.status_code == 422 and "123456" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", "account", "balance"]
