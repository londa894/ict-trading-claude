"""Backtest API: background research replays of the live setup engine (Phase 18)."""

import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db.models import Base
from app.main import create_app
from app.services.backtest.models import (
    BacktestData,
    BacktestListResponse,
    BacktestProgress,
    BacktestRequest,
    BacktestResult,
    BacktestRun,
    BacktestRunRow,
    BacktestStoreInfo,
    BacktestTrade,
    BacktestVariant,
    Funnel,
    MonteCarlo,
    SegmentStats,
    VariantResult,
)
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub


def app_client(tmp_path: Path | None):
    extra = {}
    if tmp_path is not None:
        url = f"sqlite+pysqlite:///{(tmp_path / 'bt.db').as_posix()}"
        Base.metadata.create_all(create_engine(url))
        extra = {"backtest_store": "database", "database_url": url}
    s = Settings(_env_file=None, alert_monitor_enabled=False, paper_monitor_enabled=False, **extra)  # type: ignore[call-arg]
    return create_app(settings=s, provider=NonSyntheticStub(), clock=lambda: MID)


def wait_done(c: TestClient, run_id: str, timeout: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = c.get(f"/api/v1/backtests/{run_id}").json()
        if run["status"] not in ("QUEUED", "RUNNING"):
            return run
        time.sleep(0.5)
    raise AssertionError("backtest did not finish")


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_unconfigured_store_runs_nothing():
    with TestClient(app_client(None)) as c:
        assert c.get("/api/v1/backtests/status").json()["available"] is False
        assert c.get("/api/v1/backtests").json()["runs"] == []
        body = {"symbol": "XAUUSD", "start": iso(MID - timedelta(hours=6)), "end": iso(MID)}
        r = c.post("/api/v1/backtests", json=body)
        assert r.status_code == 503 and "nothing is run" in r.json()["detail"]


def test_replay_of_the_live_engine_with_two_variants(tmp_path):
    with TestClient(app_client(tmp_path)) as c:
        body = {
            "symbol": "XAUUSD",
            "start": iso(MID - timedelta(hours=10)),
            "end": iso(MID - timedelta(hours=2)),
            "variants": [{"name": "STD"}, {"name": "CONS", "entryMode": "CONSERVATIVE", "costMultiplier": 2}],
            "segments": 2,
            "outOfSampleFrom": iso(MID - timedelta(hours=6)),
        }
        r = c.post("/api/v1/backtests", json=body)
        assert (
            r.status_code == 202
            and r.json()["status"] in ("QUEUED", "RUNNING")
            and r.json()["authority"] == "RESEARCH_ONLY"
        )
        second = c.post("/api/v1/backtests", json=body)
        assert second.status_code == 409  # one run at a time
        run = wait_done(c, r.json()["id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["integrity"] == "VERIFIED" and run["progress"]["pct"] == 100.0
        res = run["result"]
        assert [v["variant"]["name"] for v in res["variants"]] == ["STD", "CONS"]
        data = res["data"]
        assert data["provider"] == "stub-real" and data["isSynthetic"] is False and data["stepBars"] > 20
        for v in res["variants"]:
            f = v["funnel"]
            assert f["steps"] == data["stepBars"] and f["closed"] == v["stats"]["count"]
            assert (
                f["plansConfirmed"] == f["closed"] + f["expired"] + f["plansSkippedOverlap"] + f["openAtEnd"]
            )
            assert v["stats"]["label"] == "INSUFFICIENT" and len(v["segments"]) == 2
            assert v["inSample"] is not None and v["outOfSample"] is not None
        assert any("NOT applied" in d for d in res["disclosures"]) and any(
            "not a forecast" in d for d in res["disclosures"]
        )
        listing = c.get("/api/v1/backtests").json()["runs"]
        assert (
            listing[0]["id"] == run["id"]
            and listing[0]["status"] == "COMPLETED"
            and listing[0]["variants"] == ["STD", "CONS"]
        )


def test_validation_cancel_integrity_and_delete(tmp_path):
    with TestClient(app_client(tmp_path)) as c:
        future = {
            "symbol": "XAUUSD",
            "start": iso(MID - timedelta(hours=1)),
            "end": iso(MID + timedelta(hours=1)),
        }
        assert c.post("/api/v1/backtests", json=future).status_code == 422
        too_long = {"symbol": "XAUUSD", "start": iso(MID - timedelta(days=30)), "end": iso(MID)}
        assert c.post("/api/v1/backtests", json=too_long).status_code == 422
        assert (
            c.post("/api/v1/backtests", json={**future, "symbol": "BTCUSD", "end": iso(MID)}).status_code
            == 404
        )
        body = {"symbol": "XAUUSD", "start": iso(MID - timedelta(days=5)), "end": iso(MID)}
        run = c.post("/api/v1/backtests", json=body).json()
        cancelled = c.post(f"/api/v1/backtests/{run['id']}/cancel")
        assert cancelled.status_code == 200
        done = wait_done(c, run["id"])
        assert (
            done["status"] == "CANCELLED"
            and done["result"] is None
            and done["error"] == "cancelled by the user"
        )
        assert c.post(f"/api/v1/backtests/{run['id']}/cancel").status_code == 422
        short = c.post(
            "/api/v1/backtests",
            json={
                "symbol": "XAUUSD",
                "start": iso(MID - timedelta(hours=3)),
                "end": iso(MID - timedelta(hours=1)),
            },
        ).json()
        finished = wait_done(c, short["id"])
        assert finished["status"] == "COMPLETED"
        engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'bt.db').as_posix()}")
        with engine.begin() as conn:
            raw = json.loads(
                conn.execute(
                    text("SELECT result FROM backtest_runs WHERE id = :i"), {"i": short["id"]}
                ).scalar_one()
            )
            raw["variants"][0]["funnel"]["steps"] = 999
            conn.execute(
                text("UPDATE backtest_runs SET result = :r WHERE id = :i"),
                {"r": json.dumps(raw), "i": short["id"]},
            )
        tampered = c.get(f"/api/v1/backtests/{short['id']}").json()
        assert tampered["integrity"] == "TAMPERED" and tampered["result"] is None
        assert c.delete(f"/api/v1/backtests/{short['id']}").status_code == 204
        assert c.get(f"/api/v1/backtests/{short['id']}").status_code == 404


def test_interrupted_runs_are_reported_failed(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'bt.db').as_posix()}"
    first = app_client(tmp_path)
    with TestClient(first) as c:
        body = {
            "symbol": "XAUUSD",
            "start": iso(MID - timedelta(hours=2)),
            "end": iso(MID - timedelta(hours=1)),
        }
        run_id = c.post("/api/v1/backtests", json=body).json()["id"]
        wait_done(c, run_id)
    with create_engine(url).begin() as conn:
        conn.execute(text("UPDATE backtest_runs SET status = 'RUNNING', result = NULL, result_hash = NULL"))
    s = Settings(
        _env_file=None,
        alert_monitor_enabled=False,
        paper_monitor_enabled=False,
        backtest_store="database",
        database_url=url,
    )  # type: ignore[call-arg]
    with TestClient(create_app(settings=s, provider=NonSyntheticStub(), clock=lambda: MID)) as c:
        run = c.get(f"/api/v1/backtests/{run_id}").json()
        assert run["status"] == "FAILED" and "interrupted" in run["error"]


@pytest.mark.parametrize(
    "model",
    [
        BacktestVariant,
        BacktestRequest,
        BacktestTrade,
        Funnel,
        SegmentStats,
        MonteCarlo,
        VariantResult,
        BacktestData,
        BacktestResult,
        BacktestProgress,
        BacktestRun,
        BacktestRunRow,
        BacktestStoreInfo,
        BacktestListResponse,
    ],
)
def test_backtest_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_results_in_an_older_format_are_reported_not_crashed(tmp_path):
    from app.services.backtest.service import result_hash

    with TestClient(app_client(tmp_path)) as c:
        body = {
            "symbol": "XAUUSD",
            "start": iso(MID - timedelta(hours=2)),
            "end": iso(MID - timedelta(hours=1)),
        }
        run_id = c.post("/api/v1/backtests", json=body).json()["id"]
        assert wait_done(c, run_id)["status"] == "COMPLETED"
        engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'bt.db').as_posix()}")
        with engine.begin() as conn:
            row = conn.execute(text("SELECT request, result, strategy_version FROM backtest_runs")).one()
            request, result = json.loads(row.request), json.loads(row.result)
            del result["configHash"]  # a field this version requires
            conn.execute(
                text("UPDATE backtest_runs SET result = :r, result_hash = :h"),
                {"r": json.dumps(result), "h": result_hash(run_id, request, result, row.strategy_version)},
            )
        run = c.get(f"/api/v1/backtests/{run_id}").json()
        assert run["integrity"] == "UNREADABLE" and run["result"] is None and "older format" in run["error"]
        assert c.get("/api/v1/backtests").json()["runs"][0]["id"] == run_id  # the list still works
