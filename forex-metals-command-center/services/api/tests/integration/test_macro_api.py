"""Basic macro API and wiring into the evaluation, decision, alerts and assistant (Phase 14)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import MacroSeriesId, ScoreFactor
from app.main import create_app
from app.providers.fixture import SyntheticFixtureProvider
from app.services.macro.models import (
    CorrelationInfo,
    DriverContribution,
    MacroAssessment,
    MacroFile,
    MacroObservation,
    MacroSeries,
    MacroSeriesResponse,
    SeriesTrend,
)
from app.services.scoring.models import DecisionEvaluation
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub
from tests.unit.test_macro_engine import series

TODAY = MID.date()


def macro_file(tmp_path: Path, dxy=-1.0, real=-1.0, end=TODAY, raw: dict | None = None) -> Path:
    body = raw or {
        "source": "test macro",
        "fetchedAt": MID.isoformat(),
        "series": [
            json.loads(series(MacroSeriesId.DXY, dxy, end=end).model_dump_json(by_alias=True)),
            json.loads(series(MacroSeriesId.US10Y_REAL, real, end=end).model_dump_json(by_alias=True)),
        ],
    }
    path = tmp_path / "macro.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def client(provider=None, **settings) -> TestClient:
    s = Settings(_env_file=None, alert_monitor_enabled=False, **settings)  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=provider or NonSyntheticStub(), clock=lambda: MID))


def macro_item(evaluation: dict) -> dict:
    return next(c for c in evaluation["components"] if c["factor"] == ScoreFactor.MACRO.value)


def test_unconfigured_macro_is_unavailable_and_not_scored():
    c = client()
    m = c.get("/api/v1/macro/XAUUSD").json()
    assert m["available"] is False and m["bias"] == "UNAVAILABLE" and "MACRO_PROVIDER" in m["reason"]
    s = c.get("/api/v1/macro/series").json()
    assert s["available"] is False and s["series"] == [] and s["provider"] == "unconfigured"
    ev = c.get("/api/v1/evaluation/XAUUSD").json()
    assert ev["macro"]["available"] is False
    if ev["components"]:
        assert macro_item(ev)["status"] == "NOT_EVALUATED"
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["macroState"]["available"] is False and d["macroState"]["authority"] == "CONTEXT_ONLY"
    assert d["verdict"] in ("WAIT", "UNAVAILABLE") and not any("MACRO" in b for b in d["blockers"])


def test_file_macro_gives_bias_series_and_direction_state(tmp_path):
    c = client(macro_provider="file", macro_file_path=str(macro_file(tmp_path)))
    m = c.get("/api/v1/macro/XAUUSD").json()
    assert m["available"] and m["bias"] == "BULLISH" and m["isSynthetic"] is False and m["state"] is None
    assert {x["configured"] for x in m["drivers"]} == {"DXY", "US10Y_REAL", "US2Y", "VIX"}
    assert c.get("/api/v1/macro/XAUUSD?direction=BEARISH").json()["state"] == "STRONG_CONFLICT"
    assert c.get("/api/v1/macro/USDJPY").json()["bias"] == "BEARISH"  # dollar down
    s = c.get("/api/v1/macro/series").json()
    assert s["available"] and {x["id"] for x in s["series"]} == {"DXY", "US10Y_REAL"}
    assert c.get("/api/v1/macro/NOPE").status_code == 404
    assert c.get("/api/v1/macro/XAUUSD?direction=LONG").status_code == 422
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["macroState"]["bias"] == "BULLISH" and d["verdict"] != "LONG"


def test_stale_or_invalid_macro_file_is_unavailable_without_echoing_values(tmp_path):
    stale = client(macro_provider="file", macro_file_path=str(macro_file(tmp_path, end=TODAY.replace(day=1))))
    m = stale.get("/api/v1/macro/XAUUSD").json()
    assert not m["available"] and "stale" in m["reason"]
    bad = {
        "source": "x",
        "fetchedAt": MID.isoformat(),
        "series": [
            {
                "id": "DXY",
                "name": "d",
                "unit": "u",
                "observations": [{"date": "2024-04-18", "value": "SECRET-777"}],
            }
        ],
    }
    c = client(macro_provider="file", macro_file_path=str(macro_file(tmp_path, raw=bad)))
    m = c.get("/api/v1/macro/XAUUSD").json()
    assert not m["available"] and "series.0.observations.0.value" in m["reason"]
    assert "SECRET-777" not in json.dumps(m) and "SECRET-777" not in c.get("/api/v1/macro/series").text


def test_fixture_macro_is_synthetic_and_never_scored():
    c = client(macro_provider="fixture")
    m = c.get("/api/v1/macro/XAUUSD").json()
    assert m["available"] and m["isSynthetic"] and "Synthetic macro data: not scored" in m["warnings"]
    ev = c.get("/api/v1/evaluation/XAUUSD").json()
    if ev["components"]:
        item = macro_item(ev)
        assert item["status"] == "NOT_EVALUATED" and "synthetic" in item["detail"]


def test_unusable_market_data_still_carries_macro_context(tmp_path):
    c = client(SyntheticFixtureProvider(), macro_provider="file", macro_file_path=str(macro_file(tmp_path)))
    d = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert d["verdict"] == "UNAVAILABLE" and d["macroState"]["bias"] == "BULLISH"
    assert c.get("/api/v1/macro/XAUUSD").json()["bias"] == d["macroState"]["bias"]  # one bias everywhere
    assert d["macroState"]["state"] is None  # no setup direction from untrusted data
    ev = c.get("/api/v1/evaluation/XAUUSD").json()
    assert ev["macro"]["direction"] is None and ev["macro"]["state"] is None
    answer = c.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "macro?"}).json()
    assert "no open setup direction" in answer["answer"] and "Versus the open" not in answer["answer"]


def test_assistant_explains_macro_from_the_tool(tmp_path):
    c = client(macro_provider="file", macro_file_path=str(macro_file(tmp_path)))
    caps = c.get("/api/v1/assistant/capabilities").json()
    tool = next(t for t in caps["tools"] if t["name"] == "get_macro_state")
    assert tool["available"] is True
    answer = c.post(
        "/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "What does macro say?"}
    ).json()
    assert answer["intent"] == "MACRO" and "get_macro_state" in [t["name"] for t in answer["tools"]]
    facts = {f["label"]: f["value"] for f in answer["facts"]}
    assert facts["Macro bias"] == "BULLISH" and "never blocks" in answer["answer"]
    assert answer["provider"] == "DETERMINISTIC" and answer["guard"]["status"] == "NOT_APPLICABLE"
    unconfigured = (
        client().post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "dxy?"}).json()
    )
    assert any("Macro context: UNKNOWN" in u for u in unconfigured["unknowns"])


@pytest.mark.parametrize(
    "model",
    [
        MacroObservation,
        MacroSeries,
        MacroFile,
        SeriesTrend,
        DriverContribution,
        CorrelationInfo,
        MacroAssessment,
        MacroSeriesResponse,
        DecisionEvaluation,
    ],
)
def test_macro_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
