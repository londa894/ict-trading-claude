"""Grounding guard for external-model answers (spec STEP 14: never invent market facts).

An answer is published only if:
1. it states the Master Decision's verdict exactly;
2. every number above SMALL_NUMBER appears in a tool payload returned during the request (or the glossary
   text shown);
3. it contains no trade instruction (buy/sell/enter/place an order...), unless negated;
4. it mentions no catalog symbol other than the conversation's, except symbols returned by scan_markets.
Otherwise the deterministic answer is published with the violations listed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from app.domain.instrument import instrument_registry

SMALL_NUMBER = 20.0
NUMBER = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)?")
INSTRUCTION = re.compile(
    r"\b(you should|i recommend|we recommend|recommend(?:ed)?|consider)\s+"
    r"(buying|selling|buy|sell|entering|going long|going short)\b"
    r"|\b(go long|go short|enter (?:a |the )?(?:long|short|trade|position)"
    r"|place (?:an? |the )?(?:order|trade)|open (?:a )?(?:long|short|position)|buy now|sell now)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(not|never|don'?t|do not|no|cannot|can'?t|without)\b[^.]{0,30}$", re.IGNORECASE)


def _numbers(corpus: Iterable[Any]) -> list[float]:
    text = json.dumps(list(corpus), default=str)
    return [float(x.replace(",", ".")) for x in NUMBER.findall(text)]


def check(
    answer: str,
    stated_verdict: str | None,
    decision_verdict: str,
    corpus: Iterable[Any],
    symbol: str,
    allowed_symbols: set[str],
) -> list[str]:
    violations: list[str] = []
    if stated_verdict != decision_verdict:
        violations.append(
            f"stated verdict {stated_verdict!r} differs from the Master Decision {decision_verdict}"
        )

    grounded = _numbers(corpus)
    for raw in NUMBER.findall(answer):
        value = float(raw.replace(",", "."))
        if abs(value) <= SMALL_NUMBER:
            continue
        tolerance = max(0.011, abs(value) * 1e-6)
        if not any(abs(value - g) <= tolerance for g in grounded):
            violations.append(f"value {raw} is not in the engine state")

    for m in INSTRUCTION.finditer(answer):
        if not NEGATION.search(answer[max(0, m.start() - 40) : m.start()]):
            violations.append(f"trade instruction: {m.group(0)!r}")

    catalog = set(instrument_registry())
    mentioned = {s for s in catalog if re.search(rf"\b{s}\b", answer.upper())}
    for other in sorted(mentioned - {symbol} - allowed_symbols):
        violations.append(f"mentions {other} outside this {symbol} conversation")
    return violations
