"""Paper trading API: store, simulation on the independent market data, actions, integrity (Phase 16)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db.models import Base
from app.main import create_app
from app.services.journal.capture import EngineState
from app.services.paper import service as paper_service
from app.services.paper.models import (
    AssumedCosts,
    CreatePaperSimRequest,
    PaperEvent,
    PaperListResponse,
    PaperResult,
    PaperSim,
    PaperSimRow,
    PaperStoreInfo,
)
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub


class Clock:
    def __init__(self) -> None:
        self.now = MID

    def __call__(self):
        return self.now


def db_url(tmp_path: Path, create: bool = True) -> str:
    url = f"sqlite+pysqlite:///{(tmp_path / 'paper.db').as_posix()}"
    if create:
        Base.metadata.create_all(create_engine(url))
    return url


def client(tmp_path: Path | None, clock: Clock | None = None, **settings) -> TestClient:
    extra = {"paper_store": "database", "database_url": db_url(tmp_path)} if tmp_path is not None else {}
    s = Settings(_env_file=None, alert_monitor_enabled=False, **{**extra, **settings})  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=NonSyntheticStub(), clock=clock or Clock()))


def last_close(c: TestClient) -> float:
    return [
        x for x in c.get("/api/v1/candles/XAUUSD?timeframe=M5&limit=5").json()["candles"] if x["isClosed"]
    ][-1]["close"]


def manual(c: TestClient, stop_off=-50.0, target_off=50.0, **over):
    ref = last_close(c)
    body = {
        "symbol": "XAUUSD",
        "direction": "BULLISH",
        "stop": ref + stop_off,
        "target": ref + target_off,
        **over,
    }
    return c.post("/api/v1/paper/sims", json=body)


def test_unconfigured_store_simulates_nothing():
    c = client(None)
    status = c.get("/api/v1/paper/status").json()
    assert (
        status["available"] is False
        and "PAPER_STORE" in status["reason"]
        and status["monitorEnabled"] is False
    )
    assert c.get("/api/v1/paper/sims").json()["sims"] == []
    r = c.post(
        "/api/v1/paper/sims", json={"symbol": "XAUUSD", "direction": "BULLISH", "stop": 1, "target": 5000}
    )
    assert r.status_code == 503 and "nothing is simulated" in r.json()["detail"]


def test_missing_tables_are_reported(tmp_path):
    c = client(None, paper_store="database", database_url=db_url(tmp_path, create=False))
    assert "alembic upgrade head" in c.get("/api/v1/paper/status").json()["reason"]


def test_market_sim_fills_on_the_next_bar_and_closes_manually(tmp_path):
    clock = Clock()
    c = client(tmp_path, clock)
    r = manual(c)
    assert r.status_code == 201
    sim = r.json()
    assert (
        sim["status"] == "PENDING"
        and sim["authority"] == "SIMULATION_ONLY"
        and sim["integrity"] == "VERIFIED"
    )
    assert [e["type"] for e in sim["events"]] == ["CREATED"] and sim["fillPrice"] is None
    assert sim["costs"] == {"spread": 0.3, "slippage": 0.05, "commission": 0.0}
    assert "NO_CONFIRMED_PLAN" in sim["detectedViolations"] and sim["summary"]["verdict"] in (
        "WAIT",
        "UNAVAILABLE",
    )
    clock.now = MID + timedelta(minutes=30)
    got = c.get(f"/api/v1/paper/sims/{sim['id']}").json()
    assert got["status"] == "OPEN" and got["events"][1]["type"] == "FILLED"
    candles = c.get("/api/v1/candles/XAUUSD?timeframe=M5&limit=20").json()["candles"]
    first = next(x for x in candles if x["time"] >= MID.isoformat().replace("+00:00", "Z"))
    assert got["filledAt"].startswith(first["time"][:16]) and first["time"] > MID.isoformat()[:19]
    assert got["fillPrice"] == pytest.approx(first["open"] + 0.15 + 0.05)
    assert c.post(f"/api/v1/paper/sims/{sim['id']}/cancel").status_code == 422  # not pending
    closed = c.post(f"/api/v1/paper/sims/{sim['id']}/close").json()
    assert closed["status"] == "CLOSED" and closed["events"][-1]["type"] == "CLOSED_MANUALLY"
    res = closed["result"]
    assert (
        res["result"] == "MANUAL_EXIT"
        and res["exitReason"] == "MANUAL"
        and res["netRMultiple"] == res["rMultiple"]
    )
    assert res["classification"] in ("BAD_PROCESS_WIN", "PROCESS_ERROR")  # NO_CONFIRMED_PLAN was detected
    assert c.post(f"/api/v1/paper/sims/{sim['id']}/close").status_code == 422
    frozen = c.get(f"/api/v1/paper/sims/{sim['id']}").json()
    clock.now = MID + timedelta(hours=5)
    assert (
        c.get(f"/api/v1/paper/sims/{sim['id']}").json()["events"] == frozen["events"]
    )  # closed sims never change


def test_tight_stop_closes_by_simulation_with_a_result(tmp_path):
    clock = Clock()
    c = client(tmp_path, clock)
    sim = manual(c, stop_off=-0.5, target_off=200.0).json()
    clock.now = MID + timedelta(hours=3)
    got = c.get(f"/api/v1/paper/sims/{sim['id']}").json()
    assert got["status"] == "CLOSED" and got["events"][-1]["type"] == "STOP_HIT"
    assert got["result"]["exitReason"] == "STOP" and got["result"]["rMultiple"] < 0
    assert [e["seq"] for e in got["events"]] == list(range(1, len(got["events"]) + 1))
    assert all(e["integrity"] == "VERIFIED" for e in got["events"])
    rows = c.get("/api/v1/paper/sims?status=CLOSED").json()["sims"]
    assert [r["id"] for r in rows] == [sim["id"]] and rows[0]["result"] == got["result"]["result"]


def test_limit_expires_and_pending_sims_can_be_cancelled(tmp_path):
    clock = Clock()
    c = client(tmp_path, clock)
    far = manual(
        c, stop_off=-400.0, target_off=100.0, entryType="LIMIT", limitPrice=last_close(c) - 300.0
    ).json()
    cancel_me = manual(
        c, stop_off=-400.0, target_off=100.0, entryType="LIMIT", limitPrice=last_close(c) - 300.0
    ).json()
    assert c.post(f"/api/v1/paper/sims/{cancel_me['id']}/close").status_code == 422  # not open
    cancelled = c.post(f"/api/v1/paper/sims/{cancel_me['id']}/cancel").json()
    assert cancelled["status"] == "CANCELLED" and cancelled["events"][-1]["type"] == "CANCELLED"
    clock.now = MID + timedelta(hours=2)
    expired = c.get(f"/api/v1/paper/sims/{far['id']}").json()
    assert (
        expired["status"] == "EXPIRED"
        and expired["result"] is None
        and "12 bars" in expired["events"][-1]["detail"]
    )


def test_validation_and_engine_plan_without_a_plan(tmp_path):
    c = client(tmp_path)
    assert manual(c, stop_off=10.0).status_code == 422  # stop above a long reference
    r = c.post("/api/v1/paper/sims", json={"symbol": "XAUUSD", "source": "ENGINE_PLAN"})
    assert r.status_code == 422 and "no confirmed plan" in r.json()["detail"]
    assert (
        c.post(
            "/api/v1/paper/sims", json={"symbol": "BTCUSD", "direction": "BULLISH", "stop": 1, "target": 9}
        ).status_code
        == 404
    )
    assert c.get("/api/v1/paper/sims/nope").status_code == 422
    assert c.get("/api/v1/paper/sims/00000000-0000-0000-0000-000000000000").status_code == 404


def test_engine_plan_sim_takes_the_confirmed_plan(tmp_path, monkeypatch):
    c = client(tmp_path)
    ref = last_close(c)
    plan = {"direction": "BEARISH", "entry": ref + 5, "stop": ref + 15, "tp1": ref - 25, "risk": 10, "rr1": 3}

    async def fake_capture(market, evaluation, symbol, now):
        return EngineState(
            decision={"verdict": "WAIT", "riskStatus": "NOT_CONFIGURED"},
            evaluation={"plan": plan, "authority": "NOT_AUTHORIZED"},
            data={"isSynthetic": False},
        )

    monkeypatch.setattr(paper_service, "capture_engine_state", fake_capture)
    sim = c.post("/api/v1/paper/sims", json={"symbol": "XAUUSD", "source": "ENGINE_PLAN"}).json()
    assert sim["source"] == "ENGINE_PLAN" and sim["direction"] == "BEARISH" and sim["entryType"] == "LIMIT"
    assert (sim["limitPrice"], sim["stop"], sim["target"]) == pytest.approx(
        (plan["entry"], plan["stop"], plan["tp1"])
    )
    assert sim["detectedViolations"] == []  # following the plan exactly


def test_tampered_creation_record_is_flagged_and_not_advanced(tmp_path):
    clock = Clock()
    c = client(tmp_path, clock)
    sim = manual(c).json()
    with create_engine(db_url(tmp_path, create=False)).begin() as conn:
        record = json.loads(conn.execute(text("SELECT record FROM paper_sims")).scalar_one())
        record["stop"] = record["stop"] - 100
        conn.execute(text("UPDATE paper_sims SET record = :r"), {"r": json.dumps(record)})
    clock.now = MID + timedelta(hours=1)
    got = c.get(f"/api/v1/paper/sims/{sim['id']}").json()
    assert got["integrity"] == "TAMPERED" and got["status"] == "PENDING" and len(got["events"]) == 1


def test_delete_and_open_limit(tmp_path):
    c = client(tmp_path)
    sim = manual(c).json()
    assert c.delete(f"/api/v1/paper/sims/{sim['id']}").status_code == 204
    assert c.delete(f"/api/v1/paper/sims/{sim['id']}").status_code == 404
    app_paper = c.app.state.fmcc.paper
    object.__setattr__(app_paper.cfg, "max_open_sims", 1)
    assert manual(c).status_code == 201
    r = manual(c)
    assert r.status_code == 422 and "at most 1" in r.json()["detail"]


@pytest.mark.parametrize(
    "model",
    [
        AssumedCosts,
        CreatePaperSimRequest,
        PaperEvent,
        PaperResult,
        PaperSim,
        PaperSimRow,
        PaperStoreInfo,
        PaperListResponse,
    ],
)
def test_paper_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
