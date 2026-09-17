"""Optional external model (Anthropic Messages API with tool use). The key stays server-side.

The model only sees tool payloads from the deterministic services and must return JSON
{"answer": str, "verdict": str, "unknowns": [str]}. Its answer is published only after the grounding guard
passes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.domain.enums import EducationLevel
from app.services.assistant.tools import TOOLS, AssistantContext

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_ROUNDS = 6
MAX_TOKENS = 1200

SYSTEM = """You are the explanation layer of a broker-free Forex & Metals ICT/SMC decision-support system.
Rules you must follow:
- Call tools to read the deterministic engine state. Never invent prices, levels, scores, times or events.
- The Master Decision (get_market_state) is the only source of the verdict. Repeat its verdict exactly.
- Never give trade instructions (do not tell the user to buy, sell, enter or place an order).
- Only discuss the conversation's symbol, unless you called scan_markets for a comparison.
- If a value is unavailable, say UNKNOWN, UNAVAILABLE or NOT CONFIRMED and list it in "unknowns".
- Keep the answer short and matched to the requested education level.
Finish with a single JSON object and nothing else:
{"answer": "<plain text>", "verdict": "<the Master Decision verdict>", "unknowns": ["..."]}"""


class AssistantProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelAnswer:
    answer: str
    verdict: str | None
    unknowns: list[str]


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AssistantProviderError("model did not return JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AssistantProviderError("model returned invalid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        raise AssistantProviderError("model JSON has no answer")
    return data


class AnthropicAssistant:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    async def answer(self, ctx: AssistantContext, question: str, level: EducationLevel) -> ModelAnswer:
        tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema()} for t in TOOLS
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"Symbol: {ctx.symbol}. Education level: {level.value}. Question: {question}",
            }
        ]
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            for _ in range(MAX_ROUNDS):
                body = {
                    "model": self._model,
                    "max_tokens": MAX_TOKENS,
                    "system": SYSTEM,
                    "tools": tools,
                    "messages": messages,
                }
                try:
                    response = await client.post(API_URL, headers=headers, json=body)
                except httpx.HTTPError as exc:
                    raise AssistantProviderError(f"request failed ({type(exc).__name__})") from exc
                if response.status_code != 200:
                    raise AssistantProviderError(f"HTTP {response.status_code}")
                data = response.json()
                content = data.get("content", [])
                messages.append({"role": "assistant", "content": content})
                if data.get("stop_reason") == "tool_use":
                    results = []
                    for block in content:
                        if block.get("type") != "tool_use":
                            continue
                        _status, payload = await ctx.call(
                            str(block.get("name")), dict(block.get("input") or {})
                        )
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("id"),
                                "content": json.dumps(payload, default=str),
                            }
                        )
                    messages.append({"role": "user", "content": results})
                    continue
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                parsed = _extract_json(text)
                unknowns = parsed.get("unknowns")
                return ModelAnswer(
                    answer=parsed["answer"].strip(),
                    verdict=str(parsed["verdict"]) if parsed.get("verdict") is not None else None,
                    unknowns=[str(u) for u in unknowns] if isinstance(unknowns, list) else [],
                )
        raise AssistantProviderError("too many tool rounds")
