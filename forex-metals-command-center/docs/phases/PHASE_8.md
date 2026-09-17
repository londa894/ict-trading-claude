# Phase 8 — Entry & Verdict

## GOAL
Build the deterministic entry confirmation engine (spec STEP 5: models, modes, plan, chase protection) and the scoring / verdict evaluation engine (spec STEP 8: weights, grades, confidence, conflict, devil's advocate, hard blockers).

Both run end to end on the Phase 7 setup state machine and are exposed to the Master Decision and the UI.

**Verdict authority stays `FAIL_SAFE_ONLY`.** The spec's hard blockers include risk locks, a strict news blackout and missing instrument specs, and STEP 19 says to "keep verdict fail-safe until required gates exist". The risk engine (Phase 9) and the economic calendar (Phase 13) don't exist yet. So:
- a confirmed plan becomes `BLOCKED` (pending gates), never `LONG_READY` / `SHORT_READY`;
- the evaluation outcome never contains LONG/SHORT;
- the Master Decision keeps a WAIT/UNAVAILABLE verdict with no entry, stop or targets.

## SCOPE BOUNDARY
- **BREAKER** entry model is not available: breaker blocks are Phase 20+ advanced PD arrays. Config load refuses it.
- **Early-entry warnings** `BEFORE_CLOSE` and `BEFORE_MAJOR_NEWS` are enumerated but never emitted:
  - every engine uses closed candles only;
  - there is no calendar before Phase 13.
- **Phase 9:** position sizing, dollar risk, risk locks and instrument specs. R:R is computed from price only; `RISK_RR` is scored on R:R alone.
- **Phase 14:** macro score (`MACRO` is NOT_EVALUATED, 5 points never earned).
- **Later:** `ACTIVE` / `CLOSED` (paper trading, Phase 16); authority FULL (after Phase 13 at the earliest; not decided here).

## INPUTS
- The Phase 7 setup inputs (M15 pipeline, H4/H1 bias, sessions), plus:
  - M15 no-wick events (for NO_WICK_REBALANCE and the NO_WICK score);
  - M5 candles and M5 structure (for LTF_REFINEMENT);
  - confirmed IFVG zones (the IFVG label).
- Decision context: HTF bias and primary DOL from the structure/liquidity enrichment, and the session clock time quality and ADR expansion.
- **Config (new):**
  - `packages/strategy-spec/entry.json`: mode, models per mode, minimum R:R, M5 execution, windows, buffers, weak-FVG size;
  - `packages/strategy-spec/scoring.json`: spec weights, points, adjustments, conflict weights, grade floors, confidence thresholds and cap.

## RULES
1. **Modes** (default STANDARD):

   | Mode | Models (priority order) | Min R:R |
   |---|---|---|
   | CONSERVATIVE | CONSERVATIVE_RETEST | 2.5 |
   | STANDARD | LTF_REFINEMENT, NO_WICK_REBALANCE, M15_CLOSE | 2.0 |
   | AGGRESSIVE | LIMIT_RESEARCH, LTF_REFINEMENT, NO_WICK_REBALANCE, M15_CLOSE | 1.5 |

2. **Confirmation** is evaluated on the zone-touch candle and each later candle, up to `confirmationWindowBars` (4) after the touch. The first model satisfied, in priority order, confirms. "Edge" = the touched zone's entry edge (bullish: top). Models:
   - **M15_CLOSE:** the candle closes beyond the edge and is a candle in the setup direction. Entry = close. On a confirmed IFVG leg zone the model is labelled **IFVG**.
   - **NO_WICK_REBALANCE:** the candle is a same-direction MEANINGFUL-or-stronger no-wick candle (Phase 5) and closes beyond the edge. Entry = close.
   - **LTF_REFINEMENT:** the earliest confirmed M5 CHoCH/MSS in the setup direction whose M5 candle opens at or after the touch candle and closes by the evaluated M15 candle's close. Entry = that M5 candle's close.
   - **CONSERVATIVE_RETEST:** an M15_CLOSE candle arms the retest; the next candle must close beyond it. Entry = the second close. If the retest fails, a qualifying candle can re-arm it.
   - **LIMIT_RESEARCH:** on the touch candle, entry = the edge. Flagged `researchOnly` and scored at half.
3. **Plan** (bullish; bearish mirrors):
   - stop = protective extreme − 0.1 ATR; risk = entry − stop (must be > 0);
   - TP1 = the setup's DOL target;
   - TP2 / TP3 = the next untaken BSL pools beyond TP1, each > 0.25 ATR from the previous;
   - R:R_n = (TP_n − entry) ÷ risk.
4. **Chase protection → ENTRY_MISSED** (terminal):
   - At confirmation: TP1 under 1.0 ATR from entry, or R:R1 below the mode minimum.
   - After BLOCKED: the target pool is swept or broken ("TP1 reached before an authorized entry"), or the R:R from the latest close, when price moved beyond entry, drops below the minimum ("do not chase").
5. **After BLOCKED:**
   - a wick through the plan stop, or a close beyond the protective extreme → INVALIDATED;
   - the trading-day end → EXPIRED;
   - an HTF bias change → INVALIDATED;
   - no confirmation within the window → EXPIRED ("no entry confirmation within 4 bars of the touch").
6. **Transitions added to ALLOWED:**
   - ENTRY_ZONE_TOUCHED / WAITING_FOR_CONFIRMATION → BLOCKED, ENTRY_MISSED;
   - BLOCKED → ENTRY_MISSED, INVALIDATED, EXPIRED.

   Steps: LTF_CONFIRMATION is now DONE/PENDING; RISK stays NOT_EVALUATED.
7. **Score** for the open setup in direction D (spec weights, total 100):

   | Factor | Points |
   |---|---|
   | HTF 15 | decision HTF bias = D → 15; not opposite → 8 (setup bias only); opposite → 0 |
   | LIQUIDITY 15 | DOL target 5 + liquidity event 10 |
   | STRUCTURE 15 | MSS 15, CHoCH 10 |
   | DISPLACEMENT 10 | PRESENT on the break → 10 |
   | PD_ARRAY 10 | leg zone 5 + touched 5 |
   | NO_WICK 5 | a same-direction MEANINGFUL+ no-wick candle since the liquidity event → 5 |
   | SESSION 10 | time quality: IDEAL 10, ACCEPTABLE 6, LOW_QUALITY 2, AVOID 0 |
   | MACRO 5 | NOT_EVALUATED |
   | ENTRY 10 | confirmed plan → 10 (research-only → 5) |
   | RISK_RR 5 | R:R1 ≥ minimum → 5 |

   - Adjustments: CORRELATED_EVIDENCE −5 (displacement is only the break's own qualifier); COUNTER_TREND −10 (decision HTF bias opposite).
   - The score is clamped to 0–100; `evaluatedMax` = 95.
   - **Grade:** A+ ≥ 90, A ≥ 80, B ≥ 70, C ≥ 60, D otherwise.
8. **ConflictScore** (capped at 100):
   - HTF opposite 30;
   - primary DOL on the opposite side 25;
   - time AVOID 20 / LOW_QUALITY 10;
   - an opposite-direction MEANINGFUL+ no-wick candle since the sweep 15;
   - ADR EXHAUSTED 10.

   **DataQualityScore:** CURRENT/LIVE 100, DELAYED 70, STALE 30, otherwise or synthetic 0.
9. **Confidence:**
   - by score: MODERATE ≥ 60, HIGH ≥ 80, VERY_HIGH ≥ 90, otherwise LOW;
   - one level lower when conflict ≥ 40;
   - **capped at MODERATE while the gates are missing** (so LOW or MODERATE in Phase 8);
   - LOW when there is no score.
10. **Warnings** (as of the analysis):

    | Warning | When |
    |---|---|
    | EARLY_BEFORE_MSS | pre-break states |
    | DURING_SWEEP | the sweep is the latest candle |
    | INSIDE_DISPLACEMENT | armed on the latest candle |
    | BEFORE_RETRACEMENT | armed but not yet touched |
    | WEAK_FVG | every leg zone is below 0.3 ATR |
    | AGAINST_HTF | HTF bias opposite |

11. **Outcome** (authority order: data/system first):
    - UNAVAILABLE when the setup data is ineligible (no score);
    - otherwise, with an open setup: **CONFIRMED_PENDING_GATES** when BLOCKED with a plan, else **WAIT**;
    - with no open setup: **NO_TRADE** when the last setup was ENTRY_MISSED, else **WAIT**.

    Hard blockers: INSTRUMENT_SPEC_MISSING (no spec); ENTRY_MISSED and INSUFFICIENT_RR for a missed entry. Missing gates are always [RISK_GATE_MISSING, NEWS_GATE_MISSING]. `authority` is always NOT_AUTHORIZED.
12. **Devil's advocate:** every conflict, every warning, research-only entries, and the unevaluated macro, news, risk and position-size items. `evidenceFor` / `evidenceAgainst` list the scored components and the conflicts.
13. **Decision (enrichment only):**
    - Skipped when the gate verdict is UNAVAILABLE.
    - From eligible data, the evaluation (reusing the decision's HTF bias and DOL, with no second structure/liquidity pass) sets `setupState` / `setupType`, `setupScore`, `setupGrade` and `decisionConfidence`.
    - It adds WAIT-class blockers: hard blockers, plus RISK_GATE_MISSING / NEWS_GATE_MISSING for a confirmed plan.
    - Verdict, direction, entry zone, preferred entry, stop, TP1–3 and R:R are **never** set. The authority guard re-runs.
    - On failure the decision is unchanged.

## OUTPUTS
- `GET /api/v1/evaluation/{symbol}` → `DecisionEvaluation {outcome, score, grade, confidence, conflictScore, dataQualityScore, components, adjustments, hardBlockers, missingGates, warnings, evidence, devilsAdvocate, plan, authority: NOT_AUTHORIZED}`.
- `GET /api/v1/setups/{symbol}`: setups carry `entryPlan`; events include BLOCKED and ENTRY_MISSED.
- Master Decision: `setupScore`, `setupGrade`, `decisionConfidence`, `setupState`, `setupType`, plus WAIT-class blockers.
- System status: phase 8, `entry` and `scoring` enabled.
- Web:
  - ENTRY tab (enabled);
  - status bar Score ("85 (A)" or "—");
  - Setup overlay: confirmation/missed markers and plan entry/stop/TP2/TP3 lines labelled "not authorized".

## STATES
- EntryModel: CONSERVATIVE_RETEST / M15_CLOSE / LTF_REFINEMENT / NO_WICK_REBALANCE / BREAKER (unavailable) / IFVG / LIMIT_RESEARCH.
- EntryMode: CONSERVATIVE / STANDARD / AGGRESSIVE.
- EntryWarning: EARLY_BEFORE_MSS / DURING_SWEEP / INSIDE_DISPLACEMENT / BEFORE_CLOSE / BEFORE_RETRACEMENT / WEAK_FVG / AGAINST_HTF / BEFORE_MAJOR_NEWS.
- ScoreFactor: HTF / LIQUIDITY / STRUCTURE / DISPLACEMENT / PD_ARRAY / NO_WICK / SESSION / MACRO / ENTRY / RISK_RR.
- SetupGrade: A+ / A / B / C / D.
- EvaluationOutcome: UNAVAILABLE / NO_TRADE / WAIT / CONFIRMED_PENDING_GATES.
- Blocker gains ENTRY_MISSED / INSUFFICIENT_RR / RISK_GATE_MISSING / NEWS_GATE_MISSING.
- AnalysisIneligibility gains EVALUATION_FAILED.
- SetupState now emits BLOCKED (confirmed, pending gates) and ENTRY_MISSED.

## INVALIDATION
See rules 4–5. A plan exists only while BLOCKED, and on its terminal state afterwards. It is never revived or re-entered.

## ERROR STATES
| Condition | Result |
|---|---|
| Unknown symbol | HTTP 404 |
| `entry.json` lists BREAKER or IFVG as a model, or an LTF BOS | configuration load fails |
| `scoring.json` weights don't cover every factor or don't sum to 100 | configuration load fails |
| Ineligible setup data (synthetic/stale/invalid, bias unavailable) | evaluation UNAVAILABLE, no score, confidence LOW |
| M5 series unusable | LTF_REFINEMENT cannot confirm (other models still can) |
| Scoring engine throws | evaluation UNAVAILABLE with EVALUATION_FAILED |
| Evaluation throws during the decision | decision unchanged |
| READY/ACTIVE/CLOSED `setupState` reaches the decision | UNAVAILABLE + SYSTEM_INTEGRITY_FAILURE (Phase 7 guard, unchanged) |
| Web: authority claimed, confidence above MODERATE, outcome/plan mismatch, grade ≠ score, points > max, unknown warning | ENTRY tab shows EVALUATION UNAVAILABLE |
| Web: BLOCKED setup without a plan, LONG_READY/SHORT_READY/ACTIVE/CLOSED, evaluated RISK step | setup panel/overlay unavailable |

## UNIT TESTS
- `test_entry_engine.py` (16). Hand-built on the Phase 7 scenario (bullish and mirrored bearish):
  - 15M close → BLOCKED with the exact plan (entry, buffered stop, TP1, R:R, steps, next event, no authority state);
  - the touch candle confirming itself;
  - no-wick priority (MEANINGFUL+ only);
  - LTF refinement from an M5 break after the touch (one before the touch is ignored);
  - IFVG label and exclusion by config;
  - conservative retest success and failure;
  - aggressive limit research;
  - chase protection at confirmation (R:R, TP1 distance);
  - the confirmation window expiring;
  - after BLOCKED: stop traded, chase, TP1 reached;
  - plan maths (TP2/TP3 distinctness, bearish mirror, entry beyond stop, chase boundary);
  - config refusal of BREAKER/IFVG.
- `test_scoring_engine.py` (19), on real engine setups:
  - a confirmed plan scores 85 / A, capped MODERATE, CONFIRMED_PENDING_GATES, NOT_AUTHORIZED, no LONG/SHORT token in the payload;
  - counter-trend and DOL conflicts (60 / C, then conflict 55 → LOW);
  - no-wick, session and exhaustion inputs;
  - research-only half credit;
  - NO_TRADE after a missed entry with ENTRY_MISSED + INSUFFICIENT_RR; ineligible → UNAVAILABLE;
  - warnings (DURING_SWEEP, INSIDE_DISPLACEMENT, WEAK_FVG);
  - grade and confidence tables (including the cap); weights must sum to 100; DOL parsing.
- `test_setup_properties.py` (21, was 11): no-lookahead and ALLOWED-transition properties now run in **STANDARD and AGGRESSIVE** modes, with no-wick inputs. Every plan emitted satisfies R:R ≥ minimum, risk > 0 and correct price ordering; no LONG/SHORT_READY/ACTIVE/CLOSED is ever emitted.
- Updated Phase 7 tests: the happy path's candle 29 made non-confirming; LTF step PENDING; "retracement zone" wording.
- **Contracts:** 6 enums, 4 blockers and 1 ineligibility value checked from Python and TypeScript; `Setup.entryPlan` plus 4 new wire models (shared-types 108 tests, was 98).
- **Web:**
  - `evaluation.test.tsx` (18): rejection matrix (authority, confidence cap, LONG outcome, grade/score, plan/outcome, points, warnings); grading parity; ENTRY tab content (NOT AUTHORIZED banner, plan, breakdown with n/e and adjustments, evidence, devil's advocate, research-only flag, fail-safe); status bar Score; loader; setup payloads with BLOCKED/ENTRY_MISSED and not-authorized plan lines; authority states still rejected.
  - `setups.test.tsx` updated.

## INTEGRATION TESTS
`test_evaluation_api.py` (12):
- Endpoint on synthetic data is UNAVAILABLE and NOT_AUTHORIZED, with missing gates; 404.
- An eligible evaluation scores the open setup (10 components, evaluatedMax 95, confidence ≤ MODERATE, no LONG/SHORT).
- **A hand-made CONFIRMED_PENDING_GATES evaluation (score 95, A+) keeps the decision WAIT, direction null, entry/stop/TP/R:R null, confidence MODERATE, and adds only the two gate blockers.**
- The real evaluation enrichment is verdict-neutral; evaluation failure leaves the decision identical.
- Scoring-engine failure → EVALUATION_FAILED.
- A synthetic decision has no score/grade/prices; field contracts.

**API smoke** (2026-09-13, fixture provider):
- evaluation 200, ~0.7 s; synthetic → UNAVAILABLE / NOT_AUTHORIZED, score null, confidence LOW, both missing gates.
- setups: 8 setups, no plan in the current window; no LONG_READY/SHORT_READY anywhere; BTCUSD → 404.
- Market state: synthetic UNAVAILABLE with null score and null stop/TP.
- Status: phase 8, FAIL_SAFE_ONLY, `entry`/`scoring` enabled.
- **Non-synthetic fixture data, 30 h earlier:** market state WAIT, `setupState` WATCH, score 24 / D, confidence LOW, blockers [INSTRUMENT_SPEC_MISSING, ANALYSIS_GATES_NOT_IMPLEMENTED]. Decision **~1.9 s cold / ~1.0 s warm**; evaluation 0.75 s.
- Across 20 as-of times on non-synthetic fixture data: 2 setups reached a zone touch and both ended ENTRY_MISSED ("IFVG confirmed but R:R 1.00 below 2.0"); no plan survived chase protection.

**Manual browser check** (1600×900):
- The ENTRY tab (enabled) showed "NOT AUTHORIZED · missing gates: RISK_GATE_MISSING, NEWS_GATE_MISSING · outcome UNAVAILABLE", the ineligibility reasons, "no open setup", "— (a ranking, not a probability)", confidence LOW / data quality 0, and the devil's advocate line.
- Status bar Score "—"; only MACRO, RISK and AI_ANALYSIS tabs remain pending.
- **Cosmetic fix:** an empty "Evidence" heading showed when there was no evidence; now hidden.

## UI DISPLAY
- **ENTRY tab:** NOT AUTHORIZED banner with missing gates and outcome; ineligibility; setup · score / evaluated max · grade ("a ranking, not a probability"); confidence · conflict · data quality; confirmed plan table (model, mode, research-only flag, entry/stop/risk, TP1–3, R:R vs minimum); score breakdown with n/e and adjustments; warnings; evidence for/against; devil's advocate; hard blockers.
- **Status bar:** Score from the Master Decision.
- **OVERVIEW setup panel:** LTF confirmation step; BLOCKED explained as pending the risk/news gates.
- **Setup overlay (M15):** "Confirmed (pending gates)" / "Entry missed" markers; for a confirmed open setup, "Plan entry / stop / TP2 / TP3 (not authorized)" lines next to the DOL (TP1) and invalidation lines.

## KNOWN LIMITATIONS
- ⚠️ **No verdict authority.** This is by design: authority needs Phase 9 (risk) and Phase 13 (news). Whether and when to switch `verdictAuthority` must be an explicit decision, not a side effect.
- ⚠️ **Uncalibrated:** models, windows, R:R minimums, buffers and score/conflict weights are research defaults. On synthetic data the few touches failed chase protection, so the full BLOCKED path is proven by hand-built scenarios and random-walk properties (aggressive mode), not by live-like data.
- **Intrabar order unknown:**
  - LTF refinement accepts an M5 break anywhere inside the touch M15 candle, even before the wick reached the zone.
  - A limit entry assumes a fill at the edge.
- **Entry price for LTF refinement** is the M5 close; no slippage or spread (unknown without a quote feed).
- **TP2/TP3** use the as-of pool universe (see the Phase 7 limitation on windowed key levels and session pools).
- **HTF component on the API route** resolves the decision's HTF bias via the structure service; the decision passes its own (no second pass).
- **Latency:** decision ~1.0 s warm / ~1.9 s cold on non-synthetic fixture data; still no caching.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling.

## STRATEGY VERSION
`0.8.0-phase8`, verdict authority `FAIL_SAFE_ONLY`.
