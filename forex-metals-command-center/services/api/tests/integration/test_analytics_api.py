"""Analytics API over real journal and paper records (Phase 17)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db.models import Base
from app.main import create_app
from app.providers.fixture import SyntheticFixtureProvider
from app.services.analytics.models import (
    AnalyticsFilters,
    AnalyticsReport,
    Breakdown,
    DolAccuracy,
    Drawdown,
    EquityPoint,
    ExcludedCounts,
    GroupStats,
    ProcessStats,
    Unavailable,
)
from tests.integration.test_news_api import CONTRACT, MID, NonSyntheticStub
from tests.integration.test_paper_api import Clock


def client(tmp_path: Path | None, clock: Clock | None = None, provider=None) -> TestClient:
    extra = {}
    if tmp_path is not None:
        url = f"sqlite+pysqlite:///{(tmp_path / 'fmcc.db').as_posix()}"
        Base.metadata.create_all(create_engine(url))
        extra = {"journal_store": "database", "paper_store": "database", "database_url": url}
    s = Settings(_env_file=None, alert_monitor_enabled=False, paper_monitor_enabled=False, **extra)  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=provider or NonSyntheticStub(), clock=clock or Clock()))


def trade(
    c: TestClient,
    exit_price: float,
    reason: str = "TARGET",
    direction: str = "BULLISH",
    minutes_ago: int = 60,
) -> str:
    fill = {
        "direction": direction,
        "entry": 2380.0,
        "stop": 2370.0 if direction == "BULLISH" else 2390.0,
        "targets": [],
        "openedAt": (MID - timedelta(minutes=minutes_ago)).isoformat(),
    }
    e = c.post("/api/v1/journal/entries", json={"symbol": "XAUUSD", "kind": "TRADE", "trade": fill}).json()
    body = {
        "exitPrice": exit_price,
        "exitedAt": (MID - timedelta(minutes=minutes_ago - 30)).isoformat(),
        "exitReason": reason,
        "mfePrice": max(exit_price, 2381.0) if direction == "BULLISH" else min(exit_price, 2379.0),
        "maePrice": min(2375.0, exit_price) if direction == "BULLISH" else max(2385.0, exit_price),
    }
    r = c.post(f"/api/v1/journal/entries/{e['id']}/outcomes", json=body)
    assert r.status_code == 201, r.text
    return e["id"]


def test_unconfigured_stores_report_unavailable():
    c = client(None)
    for source in ("JOURNAL", "PAPER"):
        r = c.get(f"/api/v1/analytics?source={source}").json()
        assert r["available"] is False and r["overall"]["count"] == 0 and r["authority"] == "DESCRIPTIVE_ONLY"
        assert r["overall"]["label"] == "INSUFFICIENT"


def test_journal_statistics_from_verified_closed_trades(tmp_path):
    c = client(tmp_path)
    trade(c, 2400.0, minutes_ago=300)  # +2R
    trade(c, 2370.0, "STOP", minutes_ago=240)  # -1R
    trade(c, 2410.0, minutes_ago=180)  # +3R
    trade(c, 2380.5, "MANUAL", minutes_ago=120)  # +0.05R break-even
    c.post("/api/v1/journal/entries", json={"symbol": "XAUUSD", "kind": "NO_TRADE"})
    c.post("/api/v1/journal/entries", json={"symbol": "XAUUSD", "kind": "MISSED_ENTRY"})
    open_trade = {
        "direction": "BULLISH",
        "entry": 2380.0,
        "stop": 2370.0,
        "targets": [],
        "openedAt": MID.isoformat(),
    }
    c.post("/api/v1/journal/entries", json={"symbol": "XAUUSD", "kind": "TRADE", "trade": open_trade})
    r = c.get("/api/v1/analytics").json()
    o = r["overall"]
    assert r["available"] and r["filters"]["source"] == "JOURNAL" and r["authority"] == "DESCRIPTIVE_ONLY"
    assert (o["count"], o["wins"], o["losses"], o["breakeven"]) == (4, 2, 1, 1)
    assert o["totalR"] == pytest.approx(4.05) and o["winRate"] == 0.5 and o["label"] == "INSUFFICIENT"
    assert o["profitFactor"] == pytest.approx(5.05)
    assert r["drawdown"]["maxDrawdownR"] == 1.0 and r["drawdown"]["recoveryTrades"] == 1
    assert [p["cumulativeR"] for p in r["equityCurve"]] == [2.0, 1.0, 4.0, 4.05]
    assert r["decisionRecords"] == {"NO_TRADE": 1, "MISSED_ENTRY": 1} and r["excluded"]["notClosed"] == 1
    assert r["alertUsefulness"]["available"] is False and "not linked" in r["alertUsefulness"]["reason"]
    assert all(b["best"] is None for b in r["breakdowns"])  # never a "best" on 4 records
    assert {b["dimension"] for b in r["breakdowns"]} >= {
        "ASSET",
        "SESSION",
        "SETUP_TYPE",
        "TIMEFRAME",
        "DAY_OF_WEEK",
        "NO_WICK",
        "LIQUIDITY_EVENT",
    }
    assert (
        r["process"]["classifications"]["BAD_PROCESS_WIN"] == 2
    )  # NO_CONFIRMED_PLAN detected on every trade
    assert r["process"]["violationCounts"]["NO_CONFIRMED_PLAN"] == 4
    assert "not a probability" in r["disclaimer"].lower()
    assert c.get("/api/v1/analytics?source=PAPER").json()["overall"]["count"] == 0  # sources are never mixed
    assert c.get("/api/v1/analytics?symbol=EURUSD").json()["overall"]["count"] == 0
    later = c.get(
        f"/api/v1/analytics?from={(MID - timedelta(minutes=150)).isoformat().replace('+00:00', 'Z')}"
    ).json()
    assert later["overall"]["count"] == 2 and later["excluded"]["filtered"] == 2  # closes at or after "from"


def test_tampered_records_are_excluded(tmp_path):
    c = client(tmp_path)
    trade(c, 2400.0)
    trade(c, 2370.0, "STOP", minutes_ago=90)
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'fmcc.db').as_posix()}")
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id, record FROM journal_outcomes ORDER BY id LIMIT 1")).one()
        record = json.loads(row.record)
        record["rMultiple"] = 50.0
        conn.execute(
            text("UPDATE journal_outcomes SET record = :r WHERE id = :i"),
            {"r": json.dumps(record), "i": row.id},
        )
    r = c.get("/api/v1/analytics").json()
    assert r["overall"]["count"] == 1 and r["excluded"]["tampered"] == 1 and r["overall"]["totalR"] == -1.0


def test_paper_statistics_use_net_r_of_closed_sims(tmp_path):
    clock = Clock()
    c = client(tmp_path, clock)
    closes = [
        x for x in c.get("/api/v1/candles/XAUUSD?timeframe=M5&limit=5").json()["candles"] if x["isClosed"]
    ]
    ref = closes[-1]["close"]
    c.post(
        "/api/v1/paper/sims",
        json={"symbol": "XAUUSD", "direction": "BULLISH", "stop": ref - 0.5, "target": ref + 200},
    )
    c.post(
        "/api/v1/paper/sims",
        json={
            "symbol": "XAUUSD",
            "direction": "BULLISH",
            "entryType": "LIMIT",
            "limitPrice": ref - 300,
            "stop": ref - 400,
            "target": ref + 1,
        },
    )
    clock.now = MID + timedelta(hours=3)
    c.get("/api/v1/paper/sims")  # advance
    r = c.get("/api/v1/analytics?source=PAPER").json()
    assert r["available"] and r["overall"]["count"] == 1 and r["overall"]["losses"] == 1
    assert r["overall"]["totalR"] < 0 and r["excluded"]["notClosed"] == 1  # the expired limit
    assert r["decisionRecords"] == {}


def test_synthetic_records_are_excluded_unless_requested(tmp_path):
    c = client(tmp_path, provider=SyntheticFixtureProvider())
    trade(c, 2400.0)
    assert c.get("/api/v1/analytics").json()["excluded"]["synthetic"] == 1
    included = c.get("/api/v1/analytics?includeSynthetic=true").json()
    assert included["overall"]["count"] == 1 and included["includesSynthetic"] is True


@pytest.mark.parametrize(
    "model",
    [
        GroupStats,
        Breakdown,
        Drawdown,
        EquityPoint,
        ProcessStats,
        DolAccuracy,
        Unavailable,
        AnalyticsFilters,
        ExcludedCounts,
        AnalyticsReport,
    ],
)
def test_analytics_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


def test_assistant_journal_answer_carries_labelled_statistics(tmp_path):
    c = client(tmp_path)
    trade(c, 2400.0)
    answer = c.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "show my journal"}).json()
    assert "Recorded statistics over 1 closed XAUUSD trades (INSUFFICIENT)" in answer["answer"]
    assert "not a probability" in answer["answer"].lower()
    assert any("Performance edge: UNKNOWN" in u for u in answer["unknowns"])
