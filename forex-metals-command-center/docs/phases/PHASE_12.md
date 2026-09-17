# Phase 12 — AI Assistant V1

## GOAL
An assistant that answers the spec STEP 14 commands by reading the structured, deterministic state through tools (getMarketState, getStructure, getLiquidity, getPDArrayState, getNoWickEvents, getSessionState, getTradePlan, getRiskCalculation, scanMarkets…) and explaining it at four education levels. It exists as an offline deterministic explainer (default) and an optional external model (Anthropic, server-side key) whose answers are published only after a grounding guard.

**Verdict authority stays `FAIL_SAFE_ONLY`.** The assistant never becomes the price engine: it repeats the Master Decision's verdict, invents no values, gives no trade instructions, returns UNKNOWN / UNAVAILABLE / NOT CONFIRMED when the engines do not know, and keeps asset contexts isolated.

## SCOPE BOUNDARY
- **Tools for unbuilt engines** return UNAVAILABLE with their phase: `get_macro_state` (14), `query_journal` (15), `run_backtest` (18). "Why did score drop?" answers UNKNOWN because no score history is stored (journal, Phase 15).
- **`create_alert` is never executed** by the assistant: it returns a proposal the user confirms (ALERTS / "Confirm watch").
- **Education modes:** the four levels are implemented for definitions and explanations. Socratic, Quiz, Replay Tutor, Journal Coaching and mistake analysis come with replay/journal (Phases 15–19).
- **No memory:** each question is stateless, and the web keeps the chat per symbol (cleared on symbol switch).
- **Natural-language chart-object explain** (clicking a chart object) is not built.
- **Not tested live:** the external model path was not exercised against the real Anthropic API in this environment (no key, no data egress); it is tested against a scripted HTTP transport.

## INPUTS
- Tools over existing services only: `MarketStateService` (Master Decision + data report), `EvaluationService.evaluate_with_run` (setup run: M15 structure / liquidity / PD arrays / no wick, sessions, evaluation, risk), `StructureService.alignment`, `ScannerService.scan`.
- The glossary (`services/api/app/services/assistant/glossary.py`): 15 terms × 4 levels. Terms for unbuilt engines (order blocks, premium/discount) say "not implemented (Phase 20+)".
- Settings:
  - `AI_PROVIDER` (`deterministic` | `anthropic`), `ANTHROPIC_API_KEY` (server-side `SecretStr`);
  - `AI_MODEL` (default `claude-sonnet-5`), `AI_TIMEOUT_SECONDS`;
  - `AI_SHARE_ACCOUNT_DATA` (default false).

## RULES
1. **Intent classification** (ordered regex, deterministic):
   - ANALYZE, WHY_WAITING, WHY_NOT_DIRECTION, LIQUIDITY, DOL, SCORE_CHANGE, NO_WICK, INVALIDATION, TRADE_PLAN;
   - RISK, SESSION, A_PLUS_SETUPS, COMPARE, BACKTEST, MACRO, CREATE_ALERT;
   - EXPLAIN_TERM (glossary term found), otherwise HELP.
2. **Deterministic answers** use tool payloads only. Every answer starts from `get_market_state` and states its verdict.
   - Blockers are explained in plain words.
   - The DOL answer separates the Master Decision's H1 DOL from the M15 setup-timeframe DOL.
   - A missing value is reported as NOT CONFIRMED / UNKNOWN in `unknowns`.
3. **Trust:** analysis tools (structure, liquidity, PD arrays, no wick, trade plan) return `UNAVAILABLE` when the decision is UNAVAILABLE or the evaluation is ineligible. Synthetic or stale data therefore never yields analysis values.
4. **Isolation:** symbol-scoped tools called for another symbol are `REJECTED`; unknown tools are `REJECTED`. Only `scan_markets` (compare / A+) reads other markets, through each market's own decision.
5. **Privacy:** unless `AI_SHARE_ACCOUNT_DATA=true`, `get_risk_calculation` withholds the budget, currency, lock details and money amounts (risk at stop, risk per volume, margin). Status, locks, limits in %, size status and volume remain.
6. **External model (Anthropic Messages API, tool use):**
   - the system prompt states the rules;
   - at most 6 tool rounds and a 30 s timeout;
   - the final reply must be JSON `{answer, verdict, unknowns}`.
7. **Grounding guard** (external answers only), all required:
   - (a) the stated verdict equals the Master Decision;
   - (b) every number above 20 in the answer appears in a tool payload, a deterministic fact or the glossary text for that question;
   - (c) no trade instruction ("you should buy", "go long", "enter a trade", "place an order"…) unless negated;
   - (d) no catalog symbol other than the conversation's, except symbols returned by `scan_markets`.

   If the guard fails, or the model errors or returns bad JSON, the deterministic answer is published with `guard.status=FALLBACK` and the violations listed.
8. **Facts** attached to every answer come from the deterministic explainer, even when the external narrative is used.

## OUTPUTS
- `POST /api/v1/assistant/ask` (`{symbol, question ≤ 500, level, compareSymbols?}`) → `AssistantAnswer`:
  - symbol, question, intent, level, answer, facts (label/value/source), unknowns;
  - tools (name/symbol/status/detail), decision reference (verdict, data quality, blockers, strategy version, updated at);
  - proposal, provider, model, guard, `authority: NOT_AUTHORIZED`, strategy version, generated at.
- `GET /api/v1/assistant/capabilities` → provider, model, external AI configured, shares account data, commands, levels, tools with availability.
- Enums: `AssistantIntent`, `EducationLevel`, `AssistantProvider`, `GuardStatus`, `ToolStatus`. Contract fields for the 9 assistant models.

## STATES
- Provider: DETERMINISTIC / ANTHROPIC.
- Guard: NOT_APPLICABLE (deterministic), PASSED (external published), FALLBACK (external withheld).
- Tool status: OK / UNAVAILABLE / PROPOSED / REJECTED.

## INVALIDATION
- An answer describes the decision at `decision.updatedAt`; it is not refreshed. Ask again for the current state.
- A proposal is only a suggestion until the user confirms it.

## ERROR STATES
| Condition | Result |
|---|---|
| Unknown symbol (question or compare list) | 404 |
| Empty / > 500-char question, unknown level | 422 (values not echoed) |
| `AI_PROVIDER=anthropic` without a key | warning at startup; deterministic assistant |
| External model HTTP error, timeout, too many tool rounds, invalid JSON | deterministic answer, `FALLBACK`, `external model unavailable: …` |
| External answer fails the guard | deterministic answer, `FALLBACK`, each violation listed |
| Web: authority claimed, other market, external answer without PASSED, tool read another market, trade-instruction wording, proposal for another market | answer rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_assistant_core.py` (32):
- 20 intent cases;
- glossary completeness (every term × level), longest-alias match, unbuilt engines labelled;
- guard: grounded answer passes; rejects an invented price, a verdict mismatch, three instruction forms and another symbol; allows negated instructions and scanned symbols;
- JSON extraction;
- Anthropic client with a scripted transport: tool loop, tool_result forwarding, headers/model/tools/system prompt, HTTP error, network error, too many rounds.

## INTEGRATION TESTS
`tests/integration/test_assistant_api.py` (34), on real engine state (non-synthetic fixture stub):
- capabilities;
- 10 commands whose every decimal value is found in `/market-state`, `/evaluation`, `/liquidity` or `/sessions` (grounded), verdict = decision, no LONG/SHORT;
- unknowns (score history, macro, backtest); education levels differ;
- create_alert proposes without creating a watch; compare scans each market's decision;
- validation 404/422; synthetic data yields no analysis values;
- tool isolation (REJECTED); risk payload withholds a 123,456 balance unless sharing is enabled;
- external answer published when grounded (PASSED);
- **fallback** on an invented price, a trade instruction, a LONG verdict, another symbol, or invalid JSON;
- contract fields.

Updated: the route surface test allows `POST /api/v1/assistant/ask`; the decision's default next required event no longer says "Analysis engines (Phase 1+)" (found during the smoke run).

Web `tests/assistant.test.tsx` (15): answer trust rules (3 accepted, 9 rejected); instruction regex vs explanations; capabilities; POST client; AI_ANALYSIS tab enabled with disclaimer; ask flow with facts / guard violations / unknowns / decision reference; untrusted answer rejected; watch created only after "Confirm watch".

## UI DISPLAY
- **AI_ANALYSIS tab** (enabled):
  - disclaimer ("never creates prices, verdicts or trade instructions") and a provider line (deterministic, or external model with guard/privacy note);
  - education level selector, question box (500 chars), and suggested commands (Analyze Gold, Why are we waiting?, Where is liquidity?, What invalidates this?, Give trade plan, What is an FVG?);
  - newest-first turns: answer; intent · provider (model) · guard · decision verdict @ time; guard violations when an external answer was withheld; facts table (label, value, source tool); unknowns; proposal with **Confirm watch**; tools used.
- The panel remounts per symbol, so a conversation never crosses markets.

## KNOWN LIMITATIONS
- ⚠️ **The external model path is verified only against a scripted transport**, not the live Anthropic API (no key here). The guard is deliberately strict: an external answer that computes a new number (e.g. a percentage) falls back.
- ⚠️ **Keyword intent classification:** paraphrases outside the patterns fall to HELP (deterministic mode). The external model handles free wording but still only publishes grounded answers.
- **Latency:** ~0.7–1.6 s per question (one evaluation run); comparisons and "A+ setups" scan markets (cold ~16 s for all 9, cached 60 s).
- **Grounding scope:** numbers ≤ 20 (counts, phases, timeframes) are not checked. Wording can still be imprecise even when the values are grounded.
- **No conversation memory or score history;** answers are snapshots.
- **Egress:** with the external provider, engine state (not account amounts by default) is sent to Anthropic. Enabling it is an explicit setting.

## STRATEGY VERSION
`0.12.0-phase12` (phase 12, "AI Assistant V1"). Engines: + `assistant`. Verdict authority: **FAIL_SAFE_ONLY**.
