"""Replay API: Manual / Guided / Blind / Quiz sessions that never show the future (Phase 19)."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.replay.models import (
    CreateReplayRequest,
    QuizAnswerRequest,
    QuizQuestion,
    QuizResult,
    QuizScore,
    ReplayAnalysis,
    ReplayEvent,
    ReplayGuidance,
    ReplayReveal,
    ReplaySessionRow,
    ReplaySetupView,
    ReplayState,
    ReplayStatus,
    StepRequest,
)
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub

START = MID - timedelta(days=1)  # Wednesday 2024-04-17 15:01 UTC
M15 = timedelta(minutes=15)


def client() -> TestClient:
    s = Settings(_env_file=None, alert_monitor_enabled=False, paper_monitor_enabled=False)  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=NonSyntheticStub(), clock=lambda: MID))


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def create(c, mode="MANUAL", timeframe="M15", start=START):
    r = c.post(
        "/api/v1/replay/sessions",
        json={"symbol": "XAUUSD", "mode": mode, "timeframe": timeframe, "start": iso(start)},
    )
    assert r.status_code == 201, r.text
    return r.json()


def assert_no_future(state, tf=M15):
    cursor = ts(state["cursor"])
    assert state["candles"], "a replay always shows the known history"
    assert all(ts(c["time"]) + tf <= cursor and c["isClosed"] for c in state["candles"])
    assert (
        ts(state["candles"][-1]["time"]) + tf == cursor
    )  # the newest known bar closes exactly at the cursor


def test_manual_session_steps_both_ways_and_never_shows_the_future():
    c = client()
    s = create(c)
    assert s["authority"] == "EDUCATION_ONLY" and s["label"] == "XAUUSD" and s["masked"] is False
    assert (
        ts(s["cursor"]) <= START and s["analysis"] is not None and s["guidance"] is None and s["quiz"] is None
    )
    assert_no_future(s)
    live = c.get("/api/v1/candles/XAUUSD?timeframe=M15&limit=1000").json()["candles"]
    for bars in (4, 8, -3, 20):
        s = c.post(f"/api/v1/replay/sessions/{s['id']}/step", json={"bars": bars}).json()
        assert_no_future(s)
        known = [x for x in live if ts(x["time"]) + M15 <= ts(s["cursor"])]
        assert [x["close"] for x in s["candles"]] == [x["close"] for x in known[-len(s["candles"]) :]]
    first = create(c)
    stepped = c.post(f"/api/v1/replay/sessions/{first['id']}/step", json={"bars": 4}).json()
    assert ts(stepped["cursor"]) - ts(first["cursor"]) == 4 * M15  # mid-week: no gaps
    back = c.post(f"/api/v1/replay/sessions/{first['id']}/step", json={"bars": -4}).json()
    assert back["cursor"] == first["cursor"] and back["canStepBack"] is True
    assert c.post(f"/api/v1/replay/sessions/{first['id']}/step", json={"bars": 0}).status_code == 422


def test_guided_session_explains_each_step():
    c = client()
    s = create(c, "GUIDED")
    s = c.post(f"/api/v1/replay/sessions/{s['id']}/step", json={"bars": 8}).json()
    g = s["guidance"]
    assert g is not None and g["narrative"] and any("time quality" in line for line in g["narrative"])
    assert all(ts(e["time"]) + M15 <= ts(s["cursor"]) for e in g["events"])


def test_blind_session_masks_symbol_prices_and_dates_until_revealed():
    c = client()
    manual = create(c)
    blind = create(c, "BLIND")
    assert blind["label"] == "Hidden instrument" and blind["masked"] and blind["analysis"] is None
    assert "XAUUSD" not in json.dumps(blind)
    shift = ts(blind["cursor"]) - ts(manual["cursor"])
    assert shift.days % 7 == 0 and shift.days >= 520 * 7
    scale = blind["candles"][-1]["close"] / manual["candles"][-1]["close"]
    assert (
        0.6 <= scale <= 1.4 and abs(blind["candles"][0]["open"] / manual["candles"][0]["open"] - scale) < 1e-4
    )
    assert c.post(f"/api/v1/replay/sessions/{blind['id']}/step", json={"bars": -1}).status_code == 422
    stepped = c.post(f"/api/v1/replay/sessions/{blind['id']}/step", json={"bars": 3}).json()
    assert stepped["canStepBack"] is False and ts(stepped["cursor"]) - ts(blind["cursor"]) == 3 * M15
    reveal = c.post(f"/api/v1/replay/sessions/{blind['id']}/end").json()
    assert reveal["symbol"] == "XAUUSD" and reveal["priceScale"] == pytest.approx(scale, abs=1e-3)
    assert timedelta(weeks=reveal["weekShift"]) == shift
    after = c.get(f"/api/v1/replay/sessions/{blind['id']}").json()
    assert (
        after["ended"]
        and not after["masked"]
        and after["label"] == "XAUUSD"
        and after["analysis"] is not None
    )
    assert c.post(f"/api/v1/replay/sessions/{blind['id']}/step", json={"bars": 1}).status_code == 422


def test_quiz_questions_are_graded_only_after_the_cursor_moves():
    c = client()
    s = create(c, "QUIZ")
    q = s["quiz"]
    assert (
        q["id"] == "Q1"
        and q["type"] == "NEXT_BARS_DIRECTION"
        and s["analysis"] is None
        and s["lastResult"] is None
    )
    assert s["score"]["asked"] == 0 and q["referencePrice"] == s["candles"][-1]["close"]
    assert c.post(f"/api/v1/replay/sessions/{s['id']}/step", json={"bars": 1}).status_code == 422
    assert (
        c.post(
            f"/api/v1/replay/sessions/{s['id']}/answer", json={"questionId": "Q9", "answer": "UP"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            f"/api/v1/replay/sessions/{s['id']}/answer", json={"questionId": "Q1", "answer": "MAYBE"}
        ).status_code
        == 422
    )
    after = c.post(
        f"/api/v1/replay/sessions/{s['id']}/answer", json={"questionId": "Q1", "answer": "UP"}
    ).json()
    res = after["lastResult"]
    assert (
        ts(after["cursor"]) - ts(s["cursor"]) == q["horizonBars"] * M15
        and res["revealedToCursor"] == after["cursor"]
    )
    expected = "UP" if after["candles"][-1]["close"] > q["referencePrice"] else "DOWN"
    assert res["correctAnswer"] in (expected, None) and res["grade"] in ("CORRECT", "INCORRECT", "VOID")
    assert after["score"]["asked"] == 1 and after["analysis"] is not None and after["quiz"]["id"] == "Q2"
    assert_no_future(after)
    assert after["quiz"]["type"] == "LEVEL_FIRST"
    skipped = c.post(
        f"/api/v1/replay/sessions/{s['id']}/answer", json={"questionId": "Q2", "answer": "SKIP"}
    ).json()
    assert (
        skipped["lastResult"]["grade"] == "VOID"
        and skipped["score"]["void"] >= 1
        and len(skipped["history"]) == 2
    )


def test_errors_limits_listing_and_delete():
    c = client()
    assert (
        c.post(
            "/api/v1/replay/sessions", json={"symbol": "XAUUSD", "start": iso(MID + timedelta(hours=1))}
        ).status_code
        == 422
    )
    assert (
        c.post("/api/v1/replay/sessions", json={"symbol": "BTCUSD", "start": iso(START)}).status_code == 404
    )
    assert (
        c.post(
            "/api/v1/replay/sessions", json={"symbol": "XAUUSD", "timeframe": "H4", "start": iso(START)}
        ).status_code
        == 422
    )
    assert c.get("/api/v1/replay/sessions/00000000-0000-0000-0000-000000000000").status_code == 404
    s = create(c, "QUIZ")
    listing = c.get("/api/v1/replay/sessions").json()
    assert (
        listing["storage"] == "IN_MEMORY"
        and listing["sessions"][0]["id"] == s["id"]
        and listing["sessions"][0]["score"]["asked"] == 0
    )
    object.__setattr__(c.app.state.fmcc.replay._cfg, "max_sessions", 1)
    r = c.post("/api/v1/replay/sessions", json={"symbol": "XAUUSD", "start": iso(START)})
    assert r.status_code == 422 and "at most 1" in r.json()["detail"]
    assert c.delete(f"/api/v1/replay/sessions/{s['id']}").status_code == 204
    assert c.get(f"/api/v1/replay/sessions/{s['id']}").status_code == 404
    at_end = create(c, "MANUAL", start=MID - timedelta(minutes=30))
    end_state = c.post(f"/api/v1/replay/sessions/{at_end['id']}/step", json={"bars": 50}).json()
    assert end_state["atEnd"] is True and ts(end_state["cursor"]) <= MID  # capped by the data and the clock
    assert_no_future(end_state)


@pytest.mark.parametrize(
    "model",
    [
        CreateReplayRequest,
        StepRequest,
        QuizAnswerRequest,
        ReplaySetupView,
        ReplayAnalysis,
        ReplayEvent,
        ReplayGuidance,
        QuizQuestion,
        QuizResult,
        QuizScore,
        ReplayState,
        ReplayReveal,
        ReplaySessionRow,
        ReplayStatus,
    ],
)
def test_replay_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
