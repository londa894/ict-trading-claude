"""Journal V1 API: storage, immutable snapshots, outcomes, privacy and assistant wiring (Phase 15)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db.models import Base
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.journal.models import (
    CreateJournalEntryRequest,
    JournalEntry,
    JournalEntryRow,
    JournalExport,
    JournalListResponse,
    JournalOutcome,
    JournalSnapshot,
    JournalStoreInfo,
    JournalTradeFill,
    RecordOutcomeRequest,
    SnapshotSummary,
)
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub


def db_url(tmp_path: Path, create: bool = True) -> str:
    url = f"sqlite+pysqlite:///{(tmp_path / 'journal.db').as_posix()}"
    if create:
        Base.metadata.create_all(create_engine(url))
    return url


def client(tmp_path: Path | None = None, provider=None, clock=lambda: MID, **settings) -> TestClient:
    extra = {"journal_store": "database", "database_url": db_url(tmp_path)} if tmp_path is not None else {}
    s = Settings(_env_file=None, alert_monitor_enabled=False, **{**extra, **settings})  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=provider or NonSyntheticStub(), clock=clock))


def trade(**over):
    body = {
        "symbol": "XAUUSD",
        "kind": "TRADE",
        "trade": {
            "direction": "BULLISH",
            "entry": 2380.0,
            "stop": 2370.0,
            "targets": [2410.0],
            "volume": 0.5,
            "riskPct": 0.5,
            "openedAt": (MID - timedelta(minutes=2)).isoformat(),
        },
        "notes": "took the sweep",
    }
    body.update(over)
    return body


def test_unconfigured_journal_saves_nothing_and_says_so():
    c = client()
    status = c.get("/api/v1/journal/status").json()
    assert status == {"available": False, "backend": "unconfigured", "reason": status["reason"]}
    assert "JOURNAL_STORE" in status["reason"]
    listing = c.get("/api/v1/journal/entries").json()
    assert listing["entries"] == [] and listing["store"]["available"] is False
    r = c.post("/api/v1/journal/entries", json=trade())
    assert r.status_code == 503 and "nothing is saved" in r.json()["detail"]
    assert c.get("/api/v1/journal/export").status_code == 503


def test_missing_tables_make_the_journal_unavailable(tmp_path):
    c = client(journal_store="database", database_url=db_url(tmp_path, create=False))
    status = c.get("/api/v1/journal/status").json()
    assert not status["available"] and "alembic upgrade head" in status["reason"]
    assert status["backend"] == "database:sqlite"


def test_trade_entry_captures_an_immutable_snapshot_and_detects_violations(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/v1/journal/status").json()["available"] is True
    r = c.post("/api/v1/journal/entries", json=trade())
    assert r.status_code == 201
    e = r.json()
    assert e["kind"] == "TRADE" and e["status"] == "OPEN" and e["result"] is None
    snap = e["snapshot"]
    assert snap["integrity"] == "VERIFIED" and snap["timing"] == "PRE_ENTRY" and len(snap["hash"]) == 64
    decision = c.get("/api/v1/market-state/XAUUSD").json()["decision"]
    assert snap["decision"]["verdict"] == decision["verdict"] == e["summary"]["verdict"]
    assert e["summary"]["engineAuthorization"] == "NOT_AUTHORIZED" and e["summary"]["dayOfWeek"] == "Thursday"
    assert "NO_CONFIRMED_PLAN" in e["detectedViolations"]  # no confirmed plan at MID
    assert "LONG" not in json.dumps(snap["decision"]["verdict"])
    again = c.get(f"/api/v1/journal/entries/{e['id']}").json()
    assert again["snapshot"]["hash"] == snap["hash"] and again["snapshot"]["integrity"] == "VERIFIED"


def test_tampering_is_detected_and_blocks_outcomes(tmp_path):
    c = client(tmp_path)
    e = c.post("/api/v1/journal/entries", json=trade()).json()
    engine = create_engine(db_url(tmp_path, create=False))
    with engine.begin() as conn:
        record = json.loads(conn.execute(text("SELECT record FROM journal_entries")).scalar_one())
        record["trade"]["entry"] = 2375.0  # rewrite history
        conn.execute(text("UPDATE journal_entries SET record = :r"), {"r": json.dumps(record)})
    got = c.get(f"/api/v1/journal/entries/{e['id']}").json()
    assert got["snapshot"]["integrity"] == "TAMPERED"
    assert c.get("/api/v1/journal/entries").json()["entries"][0]["integrity"] == "TAMPERED"
    body = {"exitPrice": 2390.0, "exitedAt": MID.isoformat(), "exitReason": "MANUAL"}
    r = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=body)
    assert r.status_code == 422 and "integrity" in r.json()["detail"]


def test_outcomes_are_append_only_revisions_with_computed_metrics(tmp_path):
    c = client(tmp_path)
    e = c.post("/api/v1/journal/entries", json=trade()).json()
    body = {
        "exitPrice": 2400.0,
        "exitedAt": MID.isoformat(),
        "exitReason": "TARGET",
        "mfePrice": 2402.0,
        "maePrice": 2376.0,
    }
    first = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=body)
    assert first.status_code == 201
    o = first.json()["outcome"]
    assert (
        o["revision"] == 1 and o["rMultiple"] == 2.0 and o["plannedR"] == 3.0 and o["result"] == "PARTIAL_WIN"
    )
    assert o["extremeSource"] == "MANUAL" and o["mfeR"] == 2.2 and o["maeR"] == -0.4
    assert o["classification"] == "BAD_PROCESS_WIN" and "NO_CONFIRMED_PLAN" in o["violations"]
    assert first.json()["status"] == "CLOSED"
    corrected = {
        **body,
        "exitPrice": 2370.0,
        "exitReason": "STOP",
        "mfePrice": None,
        "maePrice": None,
        "reportedViolations": ["MOVED_STOP"],
    }
    second = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=corrected).json()
    assert [x["revision"] for x in second["outcomeRevisions"]] == [1, 2]
    assert (
        second["outcome"]["result"] == "FULL_LOSS" and second["outcome"]["classification"] == "PROCESS_ERROR"
    )
    assert second["outcome"]["extremeSource"] == "CANDLES" and second["outcome"]["extremesSynthetic"] is False
    assert second["outcomeRevisions"][0]["result"] == "PARTIAL_WIN"  # earlier revision kept


def test_outcome_validation_and_non_trade_entries(tmp_path):
    c = client(tmp_path)
    e = c.post("/api/v1/journal/entries", json=trade()).json()
    early = {"exitPrice": 2390.0, "exitedAt": (MID - timedelta(hours=1)).isoformat(), "exitReason": "MANUAL"}
    r = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=early)
    assert r.status_code == 422 and "after" in r.json()["detail"]
    bad_mfe = {"exitPrice": 2390.0, "exitedAt": MID.isoformat(), "exitReason": "MANUAL", "mfePrice": 2385.123}
    r = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=bad_mfe)
    assert r.status_code == 422 and "2385.123" not in r.text
    missed = c.post(
        "/api/v1/journal/entries", json={"symbol": "xauusd", "kind": "MISSED_ENTRY", "notes": "late"}
    ).json()
    assert (
        missed["status"] == "CLOSED"
        and missed["result"] == "MISSED_ENTRY"
        and missed["snapshot"]["timing"] == "NOT_APPLICABLE"
    )
    ok = {"exitPrice": 2390.0, "exitedAt": MID.isoformat(), "exitReason": "MANUAL"}
    r = c.post(f"/api/v1/journal/entries/{missed['id']}/outcomes", json=ok)
    assert r.status_code == 422 and "TRADE" in r.json()["detail"]
    assert c.post("/api/v1/journal/entries", json=trade(symbol="BTCUSD")).status_code == 404
    wrong_side = trade()
    wrong_side["trade"] = {**wrong_side["trade"], "stop": 2390.0, "volume": 123.456}
    r = c.post("/api/v1/journal/entries", json=wrong_side)
    assert r.status_code == 422 and "123.456" not in r.text
    future = trade()
    future["trade"] = {**future["trade"], "openedAt": (MID + timedelta(hours=1)).isoformat()}
    assert c.post("/api/v1/journal/entries", json=future).status_code == 422
    assert c.get("/api/v1/journal/entries/not-a-uuid").status_code == 422
    assert c.get("/api/v1/journal/entries/00000000-0000-0000-0000-000000000000").status_code == 404


def test_post_entry_snapshot_is_labelled(tmp_path):
    c = client(tmp_path)
    late = trade()
    late["trade"] = {**late["trade"], "openedAt": (MID - timedelta(hours=3)).isoformat()}
    assert c.post("/api/v1/journal/entries", json=late).json()["snapshot"]["timing"] == "POST_ENTRY"


def test_list_filters_paging_export_and_delete(tmp_path):
    ticks = iter(MID + timedelta(seconds=i) for i in range(100))
    c = client(tmp_path, clock=lambda: next(ticks))
    ids = [
        c.post("/api/v1/journal/entries", json={"symbol": s, "kind": "NO_TRADE"}).json()["id"]
        for s in ("XAUUSD", "EURUSD", "XAUUSD")
    ]
    page = c.get("/api/v1/journal/entries?limit=2").json()
    assert [x["id"] for x in page["entries"]] == [ids[2], ids[1]] and page["nextCursor"]
    rest = c.get(f"/api/v1/journal/entries?limit=2&cursor={page['nextCursor']}").json()
    assert [x["id"] for x in rest["entries"]] == [ids[0]] and rest["nextCursor"] is None
    assert len(c.get("/api/v1/journal/entries?symbol=XAUUSD").json()["entries"]) == 2
    assert c.get("/api/v1/journal/entries?kind=TRADE").json()["entries"] == []
    assert c.get("/api/v1/journal/entries?cursor=garbage").status_code == 422
    export = c.get("/api/v1/journal/export").json()
    assert len(export["entries"]) == 3 and all(
        x["snapshot"]["integrity"] == "VERIFIED" for x in export["entries"]
    )
    assert c.delete(f"/api/v1/journal/entries/{ids[1]}").status_code == 204
    assert c.delete(f"/api/v1/journal/entries/{ids[1]}").status_code == 404
    assert len(c.get("/api/v1/journal/entries").json()["entries"]) == 2


def test_synthetic_market_data_is_flagged(tmp_path):
    c = client(tmp_path, provider=SyntheticFixtureProvider())
    e = c.post("/api/v1/journal/entries", json=trade()).json()
    assert e["summary"]["isSynthetic"] is True and "TRADED_ON_UNAVAILABLE_DECISION" in e["detectedViolations"]
    assert c.get("/api/v1/journal/entries").json()["entries"][0]["isSynthetic"] is True


def test_assistant_reads_the_journal_and_withholds_fill_prices(tmp_path):
    c = client(tmp_path)
    c.post("/api/v1/journal/entries", json=trade())
    caps = {t["name"]: t for t in c.get("/api/v1/assistant/capabilities").json()["tools"]}
    assert caps["query_journal"]["available"] is True
    answer = c.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "Show my journal"}).json()
    assert answer["intent"] == "JOURNAL" and "query_journal" in [t["name"] for t in answer["tools"]]
    assert "journal entries" in answer["answer"] and "2380" not in json.dumps(answer)
    unconfigured = (
        client().post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "my trades"}).json()
    )
    assert any("Journal: UNAVAILABLE" in u for u in unconfigured["unknowns"])


@pytest.mark.parametrize(
    "model",
    [
        JournalTradeFill,
        CreateJournalEntryRequest,
        RecordOutcomeRequest,
        SnapshotSummary,
        JournalSnapshot,
        JournalOutcome,
        JournalEntry,
        JournalEntryRow,
        JournalStoreInfo,
        JournalListResponse,
        JournalExport,
    ],
)
def test_journal_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_candle_extremes_say_when_the_data_ends_before_the_trade(tmp_path):
    later = SERIES_END + timedelta(days=3)
    c = client(tmp_path, clock=lambda: later)
    body = trade()
    body["trade"] = {**body["trade"], "openedAt": (later - timedelta(minutes=3)).isoformat()}
    e = c.post("/api/v1/journal/entries", json=body).json()
    done = {"exitPrice": 2390.0, "exitedAt": later.isoformat(), "exitReason": "MANUAL"}
    o = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=done).json()["outcome"]
    assert o["extremeSource"] == "UNAVAILABLE" and "before the trade" in o["extremesDetail"]
    assert o["mfeR"] is None and o["entryEfficiency"] is None and o["rMultiple"] == 1.0
