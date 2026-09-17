"""Assistant intent classifier, glossary, grounding guard and external-model client (Phase 12)."""

import json

import httpx
import pytest

from app.domain.enums import AssistantIntent, EducationLevel, ToolStatus
from app.services.assistant import guard
from app.services.assistant.anthropic import AnthropicAssistant, AssistantProviderError, _extract_json
from app.services.assistant.deterministic import classify
from app.services.assistant.glossary import GLOSSARY, define, find_term
from app.services.assistant.tools import TOOLS

I = AssistantIntent  # noqa: E741


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Analyze Gold", I.ANALYZE),
        ("Why are we waiting?", I.WHY_WAITING),
        ("Why isn't this a buy?", I.WHY_NOT_DIRECTION),
        ("why no short here", I.WHY_NOT_DIRECTION),
        ("Where is liquidity?", I.LIQUIDITY),
        ("What is DOL?", I.DOL),
        ("Why did score drop?", I.SCORE_CHANGE),
        ("Is this No Wick important?", I.NO_WICK),
        ("What invalidates this?", I.INVALIDATION),
        ("Give trade plan", I.TRADE_PLAN),
        ("What is the risk?", I.RISK),
        ("Which session is it?", I.SESSION),
        ("Show A+ setups", I.A_PLUS_SETUPS),
        ("Compare assets", I.COMPARE),
        ("Backtest setup", I.BACKTEST),
        ("What does the DXY say?", I.MACRO),
        ("Is there high-impact news today?", I.NEWS),
        ("Alert me when ready", I.CREATE_ALERT),
        ("What is an FVG?", I.EXPLAIN_TERM),
        ("explain change of character", I.EXPLAIN_TERM),
        ("hello there", I.HELP),
    ],
)
def test_intents(question, intent):
    assert classify(question) is intent


def test_glossary_has_every_level_and_finds_the_longest_alias():
    for term, entry in GLOSSARY.items():
        for level in EducationLevel:
            assert isinstance(entry[level], str) and entry[level], (term, level)
    assert find_term("what is an inverse fvg?") == "IFVG"
    assert find_term("what is an fvg?") == "FVG"
    assert find_term("tell me about the weather") is None
    assert define("DOL", EducationLevel.BEGINNER) != define("DOL", EducationLevel.PROFESSIONAL)
    assert "Phase 20" in define("ORDER_BLOCK", EducationLevel.ADVANCED)  # unbuilt engines say so


CORPUS = [{"decision": {"verdict": "WAIT", "primaryDol": "H1 BSL EQH x3 @ 2050.91"}}, {"price": 2054.87}]


def test_guard_accepts_a_grounded_answer():
    text = "XAUUSD verdict is WAIT. The DOL is EQH at 2050.91; the weekly high is 2054.87 (5 blockers, M15)."
    assert guard.check(text, "WAIT", "WAIT", CORPUS, "XAUUSD", set()) == []


@pytest.mark.parametrize(
    ("text", "verdict", "fragment"),
    [
        ("XAUUSD is WAIT; price should reach 2099.5 soon.", "WAIT", "value 2099.5"),
        ("It is a LONG.", "LONG", "stated verdict"),
        ("Verdict WAIT, but you should buy the dip.", "WAIT", "trade instruction"),
        ("Verdict WAIT. Go long above the EQH.", "WAIT", "trade instruction"),
        ("Verdict WAIT. Place an order at the FVG.", "WAIT", "trade instruction"),
        ("Verdict WAIT, and EURUSD looks similar.", "WAIT", "mentions EURUSD"),
    ],
)
def test_guard_rejections(text, verdict, fragment):
    violations = guard.check(text, verdict, "WAIT", CORPUS, "XAUUSD", set())
    assert any(fragment in v for v in violations), violations


def test_guard_allows_negated_instructions_and_scanned_symbols():
    text = "Verdict WAIT: do not go long yet. EURUSD ranks second in the scan."
    assert guard.check(text, "WAIT", "WAIT", CORPUS, "XAUUSD", {"EURUSD"}) == []


def test_extract_json():
    assert _extract_json('Here you go {"answer": "x", "verdict": "WAIT"}')["answer"] == "x"
    for bad in ("no json", "{not json}", '{"verdict": "WAIT"}'):
        with pytest.raises(AssistantProviderError):
            _extract_json(bad)


class FakeContext:
    symbol = "XAUUSD"

    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return ToolStatus.OK, {"decision": {"verdict": "WAIT"}}


def scripted(responses, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((dict(request.headers), body))
        return httpx.Response(200, json=responses[len(seen) - 1])

    return httpx.MockTransport(handler)


async def test_client_runs_the_tool_loop_and_parses_the_final_json():
    seen: list = []
    responses = [
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "get_market_state", "input": {"symbol": "XAUUSD"}}
            ],
        },
        {
            "stop_reason": "end_turn",
            "content": [
                {
                    "type": "text",
                    "text": '{"answer": "XAUUSD is WAIT.", "verdict": "WAIT", "unknowns": ["macro"]}',
                }
            ],
        },
    ]
    client = AnthropicAssistant("sk-test", "claude-sonnet-5", 5, transport=scripted(responses, seen))
    ctx = FakeContext()
    result = await client.answer(ctx, "Analyze Gold", EducationLevel.BEGINNER)  # type: ignore[arg-type]
    assert result.answer == "XAUUSD is WAIT." and result.verdict == "WAIT" and result.unknowns == ["macro"]
    assert ctx.calls == [("get_market_state", {"symbol": "XAUUSD"})]
    headers, first = seen[0]
    assert headers["x-api-key"] == "sk-test" and headers["anthropic-version"] == "2023-06-01"
    assert first["model"] == "claude-sonnet-5" and {t["name"] for t in first["tools"]} == {
        t.name for t in TOOLS
    }
    assert "Never invent" in first["system"] and "BEGINNER" in first["messages"][0]["content"]
    tool_result = seen[1][1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"
    assert json.loads(tool_result["content"]) == {"decision": {"verdict": "WAIT"}}


async def test_client_errors():
    def status(code):
        return httpx.MockTransport(lambda _r: httpx.Response(code, json={}))

    with pytest.raises(AssistantProviderError, match="HTTP 401"):
        await AnthropicAssistant("k", "m", 5, transport=status(401)).answer(
            FakeContext(), "q", EducationLevel.BEGINNER
        )  # type: ignore[arg-type]

    def boom(_r):
        raise httpx.ConnectError("offline")

    with pytest.raises(AssistantProviderError, match="request failed"):
        await AnthropicAssistant("k", "m", 5, transport=httpx.MockTransport(boom)).answer(
            FakeContext(), "q", EducationLevel.BEGINNER
        )  # type: ignore[arg-type]

    looping = [
        {
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "get_market_state", "input": {}}],
        }
        for i in range(10)
    ]
    with pytest.raises(AssistantProviderError, match="too many tool rounds"):
        await AnthropicAssistant("k", "m", 5, transport=scripted(looping, [])).answer(
            FakeContext(), "q", EducationLevel.BEGINNER
        )  # type: ignore[arg-type]
