"""News gate API and wiring into the evaluation, decision, risk, alerts watch and assistant (Phase 13)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.decision import MasterDecision
from app.domain.enums import Blocker, EvaluationOutcome, Timeframe, Verdict
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.news.models import (
    CalendarFile,
    CalendarInfo,
    CalendarResponse,
    EconomicEvent,
    EventView,
    NewsAssessment,
)
from app.services.scoring.service import EvaluationService
from app.services.setup_state.service import SetupService
from tests.integration.test_evaluation_api import _confirmed_evaluation

MID = SERIES_END - timedelta(hours=30, minutes=-1)  # Thursday 2024-04-18 15:01 UTC
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def calendar(tmp_path: Path, events: list[dict]) -> Path:
    body = {
        "source": "test calendar",
        "fetchedAt": (MID - timedelta(hours=2)).isoformat(),
        "coverageStart": (MID - timedelta(days=2)).isoformat(),
        "coverageEnd": (MID + timedelta(days=3)).isoformat(),
        "events": events,
    }
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def ev(minutes: float, importance="HIGH", currency="USD", name="US CPI"):
    return {
        "id": f"{currency}-{importance}-{minutes}",
        "country": "United States" if currency == "USD" else "Euro Area",
        "currency": currency,
        "name": name,
        "scheduledTime": (MID + timedelta(minutes=minutes)).isoformat(),
        "importance": importance,
        "forecast": "0.3%",
        "previous": "0.4%",
    }


def client(**settings) -> TestClient:
    s = Settings(_env_file=None, alert_monitor_enabled=False, **settings)  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=NonSyntheticStub(), clock=lambda: MID))


def test_unconfigured_calendar_blocks_the_decision():
    c = client()
    news = c.get("/api/v1/news/XAUUSD").json()
    assert news["state"] == "UNAVAILABLE" and news["blockers"] == ["NEWS_DATA_UNAVAILABLE"]
    assert "CALENDAR_PROVIDER" in news["calendar"]["reason"]
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "WAIT" and "NEWS_DATA_UNAVAILABLE" in d["blockers"]
    assert d["newsState"]["state"] == "UNAVAILABLE" and d["newsState"]["calendarAvailable"] is False
    evaluation = c.get("/api/v1/evaluation/XAUUSD").json()
    assert evaluation["missingGates"] == [] and evaluation["news"]["state"] == "UNAVAILABLE"
    assert (
        evaluation["risk"]["news"] == "UNAVAILABLE" and "NEWS_NOT_EVALUATED" in evaluation["risk"]["warnings"]
    )
    assert c.get("/api/v1/news/BTCUSD").status_code == 404


def test_blackout_reaches_decision_evaluation_risk_and_assistant(tmp_path):
    c = client(
        calendar_provider="file",
        calendar_file_path=str(calendar(tmp_path, [ev(5, "HIGH"), ev(30, "EXTREME", "EUR", "ECB")])),
    )
    news = c.get("/api/v1/news/XAUUSD").json()
    assert (
        news["state"] == "BLACKOUT"
        and news["blockers"] == ["NEWS_BLACKOUT"]
        and news["relevantCurrencies"] == ["USD"]
    )
    assert news["activeEvent"]["name"] == "US CPI" and news["activeEvent"]["status"] == "IMMINENT"
    assert [e["currency"] for e in news["events"]] == ["USD"]  # EUR event is not relevant to gold
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "WAIT" and "NEWS_BLACKOUT" in d["blockers"] and d["stop"] is None
    assert d["newsState"]["state"] == "BLACKOUT" and d["newsState"]["activeEvent"]["importance"] == "HIGH"
    evaluation = c.get("/api/v1/evaluation/XAUUSD").json()
    assert "NEWS_BLACKOUT" in evaluation["hardBlockers"] and evaluation["risk"]["news"] == "BLACKOUT"
    if evaluation["setupState"] not in (None, "BLOCKED"):
        assert "BEFORE_MAJOR_NEWS" in evaluation["warnings"]
    eur = c.get("/api/v1/news/EURUSD").json()
    assert eur["state"] == "BLACKOUT" and eur["relevantCurrencies"] == ["EUR", "USD"]
    answer = c.post(
        "/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "Is there news soon?"}
    ).json()
    assert answer["intent"] == "NEWS" and "BLACKOUT" in answer["answer"]
    assert ("get_news_state", "OK") in [(t["name"], t["status"]) for t in answer["tools"]]


def test_clear_calendar_clears_the_news_gate(tmp_path):
    c = client(calendar_provider="file", calendar_file_path=str(calendar(tmp_path, [ev(600, "HIGH")])))
    news = c.get("/api/v1/news/XAUUSD").json()
    assert (
        news["state"] == "CLEAR" and news["blockers"] == [] and news["nextEvent"]["minutesToEvent"] == 600.0
    )
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert not any(b.startswith("NEWS_") for b in d["blockers"]) and d["newsState"]["state"] == "CLEAR"
    assert d["verdict"] == "WAIT"  # all gates exist, authority is still FAIL_SAFE_ONLY
    watch = c.post("/api/v1/alerts/ready-watches", json={"symbol": "XAUUSD"}).json()
    assert watch["state"] == "WAITING"


def test_fixture_calendar_is_synthetic_and_never_clears():
    c = client(calendar_provider="fixture")
    news = c.get("/api/v1/news/XAUUSD").json()
    assert news["calendar"]["isSynthetic"] is True and "NEWS_DATA_SYNTHETIC" in news["blockers"]
    body = c.get(
        "/api/v1/calendar", params={"currency": "usd", "minImportance": "HIGH", "hoursAfter": 72}
    ).json()
    assert body["events"] and {e["currency"] for e in body["events"]} == {"USD"}
    assert {e["importance"] for e in body["events"]} <= {"HIGH", "EXTREME"}
    assert c.get("/api/v1/calendar", params={"minImportance": "HUGE"}).status_code == 422


def test_invalid_calendar_file_is_unavailable_without_echoing_values(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps({"source": "x", "fetchedAt": "not-a-date-123456", "events": []}), encoding="utf-8"
    )
    news = client(calendar_provider="file", calendar_file_path=str(path)).get("/api/v1/news/XAUUSD").json()
    assert news["state"] == "UNAVAILABLE" and "fetchedAt" in news["calendar"]["reason"]
    assert "123456" not in news["calendar"]["reason"]


async def test_awaiting_authority_never_changes_the_verdict():
    awaiting = _confirmed_evaluation().model_copy(
        update={
            "outcome": EvaluationOutcome.CONFIRMED_AWAITING_AUTHORITY,
            "hard_blockers": [],
            "missing_gates": [],
        }
    )

    async def fake(*_a, **_k):
        return awaiting

    provider = NonSyntheticStub()
    candles = CandleService(provider, clock=lambda: MID)
    evaluation = EvaluationService(SetupService(candles))
    evaluation.evaluate = fake  # type: ignore[method-assign]
    svc = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, lambda: MID, evaluation=evaluation)
    d = (await svc.evaluate("XAUUSD")).decision
    assert (
        d.verdict is Verdict.WAIT
        and d.direction is None
        and (d.stop, d.tp1, d.preferred_entry) == (None, None, None)
    )
    assert Blocker.ANALYSIS_GATES_NOT_IMPLEMENTED in d.blockers  # the authority blocker remains


@pytest.mark.parametrize(
    "model",
    [EconomicEvent, CalendarFile, EventView, CalendarInfo, NewsAssessment, CalendarResponse, MasterDecision],
)
def test_news_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_unusable_market_data_still_carries_the_news_state(tmp_path):
    path = calendar(tmp_path, [ev(5, "HIGH")])
    s = Settings(
        _env_file=None, alert_monitor_enabled=False, calendar_provider="file", calendar_file_path=str(path)
    )  # type: ignore[call-arg]
    c = TestClient(create_app(settings=s, provider=SyntheticFixtureProvider(), clock=lambda: MID))
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["newsState"]["state"] == "BLACKOUT"
    assert "NEWS_BLACKOUT" in d["blockers"] and "DATA_SYNTHETIC" in d["blockers"]
    assert c.get("/api/v1/news/XAUUSD").json()["state"] == d["newsState"]["state"]  # one state everywhere
