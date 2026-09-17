"""AI assistant API on real engine state (Phase 12): grounding, isolation, guard fallback, privacy."""

import json
import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import ToolStatus
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.assistant.anthropic import AnthropicAssistant
from app.services.assistant.models import (
    AlertProposal,
    AskRequest,
    AssistantAnswer,
    AssistantCapabilities,
    DecisionRef,
    Fact,
    GuardReport,
    ToolCallRecord,
    ToolInfo,
)
from app.services.assistant.tools import AssistantContext
from tests.risk_helpers import spec

MID = SERIES_END - timedelta(hours=30, minutes=-1)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def make_app(provider=None, clock=lambda: MID, **settings):
    s = Settings(_env_file=None, alert_monitor_enabled=False, **settings)  # type: ignore[call-arg]
    return create_app(settings=s, provider=provider or NonSyntheticStub(), clock=clock)


@pytest.fixture(scope="module")
def client():
    return TestClient(make_app())


def ask(c, question, **extra):
    r = c.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": question, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_capabilities(client):
    caps = client.get("/api/v1/assistant/capabilities").json()
    assert (
        caps["provider"] == "DETERMINISTIC"
        and caps["externalAiConfigured"] is False
        and caps["model"] is None
    )
    assert caps["sharesAccountData"] is False and caps["authority"] == "NOT_AUTHORIZED"
    tools = {t["name"]: t for t in caps["tools"]}
    assert tools["get_macro_state"]["available"] is True  # Phase 14
    assert tools["run_backtest"]["available"] is True and tools["get_market_state"]["available"] is True
    assert "Analyze Gold" in caps["commands"] and caps["levels"] == [
        "BEGINNER",
        "INTERMEDIATE",
        "ADVANCED",
        "PROFESSIONAL",
    ]


def numbers(text):
    return {float(x) for x in re.findall(r"(?<![A-Za-z])\d+\.\d+", text)}


@pytest.mark.parametrize(
    "question",
    [
        "Analyze Gold",
        "Why are we waiting?",
        "Why isn't this a buy?",
        "Where is liquidity?",
        "What is DOL?",
        "Is this No Wick important?",
        "What invalidates this?",
        "Give trade plan",
        "What is the risk?",
        "Which session is it?",
    ],
)
def test_answers_are_grounded_in_engine_state(client, question):
    body = ask(client, question)
    decision = client.get("/api/v1/market-state/XAUUSD").json()["decision"]
    evaluation = client.get("/api/v1/evaluation/XAUUSD").json()
    liquidity_h1 = client.get("/api/v1/liquidity/XAUUSD", params={"timeframe": "M15"}).json()
    assert body["decision"]["verdict"] == decision["verdict"] == "WAIT"
    assert body["authority"] == "NOT_AUTHORIZED" and body["provider"] == "DETERMINISTIC"
    assert body["guard"]["status"] == "NOT_APPLICABLE"
    assert "LONG" not in body["answer"] and "SHORT" not in body["answer"]
    corpus = json.dumps([decision, evaluation, liquidity_h1, client.get("/api/v1/sessions/XAUUSD").json()])
    for value in numbers(body["answer"] + " ".join(f["value"] for f in body["facts"])):
        assert (
            str(value).rstrip("0").rstrip(".") in corpus or f"{value:.2f}" in corpus or str(value) in corpus
        ), (question, value)
    assert all(t["symbol"] in (None, "XAUUSD") for t in body["tools"])


def test_unknowns_and_unavailable_engines(client):
    assert ask(client, "Why did score drop?")["unknowns"] == [
        "Score history: UNKNOWN (scores are not recorded over time; "
        "journal snapshots keep only the score at logging time)"
    ]
    macro = ask(client, "What does macro say?")
    assert macro["unknowns"] == [
        "Macro context: UNKNOWN (no macro data provider is configured (MACRO_PROVIDER))"
    ]
    assert [t["name"] for t in macro["tools"]][-1] == "get_macro_state"
    assert (
        "no completed backtest" in ask(client, "Backtest setup")["answer"]
        or "BACKTEST_STORE" in ask(client, "Backtest setup")["answer"]
    )


def test_education_levels_change_the_explanation(client):
    beginner = ask(client, "What is an FVG?", level="BEGINNER")["answer"]
    professional = ask(client, "What is an FVG?", level="PROFESSIONAL")["answer"]
    assert beginner != professional and "fair value gap" in beginner.lower()


def test_create_alert_is_only_a_proposal(client):
    body = ask(client, "Alert me when ready for a short")
    assert body["proposal"] == {
        "symbol": "XAUUSD",
        "direction": "BEARISH",
        "reason": body["proposal"]["reason"],
    }
    assert client.get("/api/v1/alerts/ready-watches").json() == []  # nothing was created


def test_compare_scans_each_market_decision(client):
    body = client.post(
        "/api/v1/assistant/ask",
        json={"symbol": "XAUUSD", "question": "Compare assets", "compareSymbols": ["XAUUSD", "EURUSD"]},
    ).json()
    assert body["intent"] == "COMPARE" and [f["label"] for f in body["facts"][-2:]] == [
        "#1 XAUUSD",
        "#2 EURUSD",
    ]
    assert "research only" in body["facts"][-1]["value"]


def test_requests_are_validated(client):
    assert (
        client.post("/api/v1/assistant/ask", json={"symbol": "BTCUSD", "question": "hi"}).status_code == 404
    )
    assert (
        client.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "x" * 501}).status_code
        == 422
    )
    assert client.post("/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": ""}).status_code == 422
    assert (
        client.post(
            "/api/v1/assistant/ask", json={"symbol": "XAUUSD", "question": "hi", "level": "GURU"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/assistant/ask",
            json={"symbol": "XAUUSD", "question": "Compare", "compareSymbols": ["BTCUSD"]},
        ).status_code
        == 404
    )


def test_synthetic_data_never_yields_analysis_values():
    c = TestClient(
        make_app(provider=SyntheticFixtureProvider(), clock=lambda: SERIES_END + timedelta(seconds=30))
    )
    body = ask(c, "Where is liquidity?")
    assert body["decision"]["verdict"] == "UNAVAILABLE"
    assert "Setup-timeframe liquidity: UNAVAILABLE" in " ".join(body["unknowns"])
    assert all(f["label"] in ("Verdict", "Data quality", "Blockers") for f in body["facts"])
    assert ("get_liquidity", "UNAVAILABLE") in [(t["name"], t["status"]) for t in body["tools"]]


async def test_tools_keep_asset_contexts_isolated():
    app = make_app()
    st = app.state.fmcc
    ctx = AssistantContext("XAUUSD", st.market_state, st.evaluation, st.structure, st.scanner, False)
    status, payload = await ctx.call("get_liquidity", {"symbol": "EURUSD"})
    assert status is ToolStatus.REJECTED and "isolated" in payload["reason"]
    status, _ = await ctx.call("delete_everything", {})
    assert status is ToolStatus.REJECTED


async def test_risk_payload_withholds_account_data_unless_shared(tmp_path):
    profile = {
        "account": {"balance": 123456, "currency": "USD", "profile": "STANDARD"},
        "state": {
            "tradingDay": "2024-04-18",
            "realizedPnlToday": -500,
            "realizedPnlWeek": 0,
            "tradesToday": 0,
            "consecutiveLosses": 0,
        },
        "instrumentSpecs": {"XAUUSD": spec().model_dump(by_alias=True)},
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    for share, visible in ((False, False), (True, True)):
        app = make_app(risk_profile_path=str(path), ai_share_account_data=share)
        st = app.state.fmcc
        ctx = AssistantContext("XAUUSD", st.market_state, st.evaluation, st.structure, st.scanner, share)
        _, payload = await ctx.call("get_risk_calculation", {"symbol": "XAUUSD"})
        text = json.dumps(payload)
        assert ("1234.56" in text) is visible  # 1% of the balance
        assert payload["status"] in ("CLEAR", "LOCKED")


def external(app, responses):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        rounds = sum(1 for m in body["messages"] if m["role"] == "assistant")
        return httpx.Response(200, json=responses[rounds])

    app.state.fmcc.assistant._external = AnthropicAssistant(
        "sk-test", "claude-sonnet-5", 5, transport=httpx.MockTransport(handler)
    )


def final(answer, verdict="WAIT"):
    return {
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": json.dumps({"answer": answer, "verdict": verdict, "unknowns": []})}
        ],
    }


TOOL_ROUND = {
    "stop_reason": "tool_use",
    "content": [{"type": "tool_use", "id": "t1", "name": "get_liquidity", "input": {"symbol": "XAUUSD"}}],
}


def test_external_answer_is_published_when_grounded():
    app = make_app()
    c = TestClient(app)
    liq = client_liquidity_price(c)
    external(app, [TOOL_ROUND, final(f"XAUUSD is WAIT. The M15 draw on liquidity is at {liq}.")])
    body = ask(c, "Where is liquidity?")
    assert body["provider"] == "ANTHROPIC" and body["guard"] == {"status": "PASSED", "violations": []}
    assert (
        body["answer"] == f"XAUUSD is WAIT. The M15 draw on liquidity is at {liq}."
        and body["model"] == "claude-sonnet-5"
    )
    assert body["facts"]  # deterministic facts are always attached


def client_liquidity_price(c):
    body = ask(c, "Where is liquidity?")
    fact = next(f for f in body["facts"] if f["label"].startswith("Setup DOL"))
    return fact["value"].split("@ ")[1]


@pytest.mark.parametrize(
    ("responses", "violation"),
    [
        ([TOOL_ROUND, final("XAUUSD is WAIT. Price will tag 2099.99 next.")], "value 2099.99"),
        ([final("XAUUSD looks bullish: go long now.")], "trade instruction"),
        ([final("It is a LONG.", verdict="LONG")], "stated verdict"),
        ([final("XAUUSD is WAIT, and GBPUSD is similar.")], "mentions GBPUSD"),
        (
            [{"stop_reason": "end_turn", "content": [{"type": "text", "text": "no json"}]}],
            "external model unavailable",
        ),
    ],
)
def test_external_answer_falls_back_when_the_guard_fails(responses, violation):
    app = make_app()
    c = TestClient(app)
    external(app, responses)
    body = ask(c, "Where is liquidity?")
    assert body["provider"] == "DETERMINISTIC" and body["guard"]["status"] == "FALLBACK"
    assert any(violation in v for v in body["guard"]["violations"]), body["guard"]
    assert "2099.99" not in body["answer"] and "go long" not in body["answer"]
    assert body["answer"].startswith("Master Decision primary DOL")


@pytest.mark.parametrize(
    "model",
    [
        AskRequest,
        Fact,
        ToolCallRecord,
        DecisionRef,
        AlertProposal,
        GuardReport,
        AssistantAnswer,
        ToolInfo,
        AssistantCapabilities,
    ],
)
def test_assistant_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
