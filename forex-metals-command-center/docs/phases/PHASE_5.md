# Phase 5 — No Wick Architecture V1

## GOAL
Deterministically measure every completed candle, classify no-wick candles (spec STEP 13), grade their strength,
score them with three separate numbers (CandleQualityScore, ContextScore, NoWickRelevanceScore), and track a
rebalance zone for each meaningful event through FRESH → … → REACTED / FAILED / INVALIDATED. The Master Decision
gets a `noWickState` for context only. **No Wick never authorizes LONG/SHORT**, and the verdict stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **SESSION context** needs the Session & Time engine (Phase 6), so it is `NOT_EVALUATED`.
- **NEWS context** needs the economic calendar (Phase 13), so it is `NOT_EVALUATED`. `NEWS_DRIVEN_NO_WICK` is **never assigned**; the web client rejects a payload that carries it, because without a calendar it would be an invented fact.
- **OB overlap** needs order blocks (Phase 20+ advanced PD arrays), so `obOverlap` is always `NOT_EVALUATED`.
- **"Adaptive No Wick"** (spec Phase 20+) is not built; thresholds are static spec values.
- No entry model uses no-wick zones yet (Phase 7/8).

## INPUTS
- Closed candles of the analysed timeframe: the same validated series as chart, structure, liquidity and PD arrays.
- Outputs of the earlier pipeline stages at the same timeframe, each optional:
  - qualified structure events;
  - liquidity events;
  - displacement events;
  - FVG/IFVG events and zones.
- `packages/strategy-spec/no_wick.json`; ATR period from `structure.json`.

## RULES
1. **Pipeline order:** structure → liquidity → DOL → displacement → FVG/IFVG → structure qualifiers → **No Wick** (`services/analysis/pipeline.py`).
   - No Wick runs in its own try/except.
   - When liquidity or PD arrays failed, their inputs are `None`, and the matching context components become `NOT_EVALUATED`. They are never silently scored 0.
2. **Features, for every candle:**
   - range, body, upper/lower wick;
   - body %, upper/lower wick % of range;
   - body/ATR, range/ATR;
   - body/median body, range/median range;
   - close location %.
   - ATR averages up to 14 true ranges **before** the candle, and the medians use up to 20 **prior** candles, so a candle never shrinks its own yardstick.
   - Ratios are null when undefined (zero range, no prior candles). The first candle has no ATR and is never classified.
3. **Direction:** BULLISH if close > open, BEARISH if close < open. A doji or zero range is not an event.
4. **Shape** (first match wins; "origin" wick = the lower wick for bullish candles, upper for bearish):

   | Priority | Shape | Condition |
   |---|---|---|
   | 1 | TRUE_{BULL,BEAR}ISH_MARUBOZU | body ≥ 90%, each wick ≤ 5% |
   | 2 | NEAR_{BULL,BEAR}ISH_MARUBOZU | body ≥ 80%, each wick ≤ 10% |
   | 3 | BULLISH_NO_LOWER_WICK / BEARISH_NO_UPPER_WICK | body ≥ 50%, origin wick ≤ 5% |
   | 4 | BULLISH_NO_UPPER_WICK / BEARISH_NO_LOWER_WICK | body ≥ 50%, destination wick ≤ 5% |

   - The 50% body floor for one-sided shapes is a research default. The spec gives none, and without it a spinning top with one flat side would qualify.
   - Comparisons use a 1e-9 tolerance, so a wick of exactly 5% qualifies. A larger wick is a real rejection and does not.
5. **Tags** (independent of shape): `NO_ORIGIN_SIDE_WICK` (origin wick ≤ 5%) and `NO_DESTINATION_SIDE_WICK` (destination wick ≤ 5%).
6. **Strength** by body/ATR: MEANINGFUL ≥ 0.8, STRONG ≥ 1.2, EXCEPTIONAL ≥ 1.5, otherwise INSIGNIFICANT.
   - `classification` equals the shape, or `INSIGNIFICANT_NO_WICK` when the strength is INSIGNIFICANT.
   - `shape` always keeps the geometry.
7. **CandleQualityScore** (0–100, candle only):
   - Origin wick: 25 × (1 − origin%/5%).
   - Destination wick: 15 × (1 − destination%/10%).
   - Body %: 30 × (body% − 50%)/50%.
   - Body/ATR: 30 × min(1, body/ATR ÷ 1.5).
   - Each term is clamped to ≥ 0.
8. **ContextScore** (0–100) uses **only facts known at the candle's close**. Each factor is reported as a `ScoreComponent {factor, status, points, maxPoints, detail}`:

   | Factor | Points |
   |---|---|
   | DISPLACEMENT | same-direction displacement emitted **on this candle**: WEAK 10 / MODERATE 18 / STRONG 25 / EXCEPTIONAL 25 |
   | STRUCTURE | same-direction confirmed break **on this candle**: BOS 15 / CHoCH 15 / MSS 25 (best) |
   | LIQUIDITY | 20 if an opposite-side SWEEP or RECLAIM (SSL for bullish, BSL for bearish) happened in the 3 bars up to and including this candle |
   | FVG | 15 if a same-direction FVG was **created on this candle** (this candle is the gap's third candle) |
   | TREND | 15 if the last confirmed EXTERNAL BOS/MSS up to and including this candle points the same way |
   | SESSION / NEWS | always NOT_EVALUATED |

   **No lookahead:** an FVG completed by a later candle, or a later break, can never add points to an earlier event (tested).
9. **NoWickRelevanceScore** = 0.4 × quality + 0.6 × context, halved for an **inside bar** (high ≤ previous high and low ≥ previous low), and 0 for INSIGNIFICANT events. None of the three scores is a probability.
10. **Rebalance zone** for each MEANINGFUL+ event with ATR > 0:
    - `closeLevel` = close (destination), `openLevel` = open (full body), `originExtreme` = low (bullish) or high (bearish).
    - `level25/50/75` are measured from the close toward the open.
    - `fvgOverlapIds` lists FVG/IFVG zones created at or before the candle, not invalidated by then, that overlap the body.
11. **Zone evaluation** starts on the next candle and runs in this order on each candle:
    1. **Depth:** `rebalancePct` = max wick penetration past the close level ÷ body × 100, capped at 100.
       - Each reference emits once, in order: TOUCHED (within 0.02 ATR) → REBALANCE_25 → REBALANCE_50 → REBALANCE_75 → FULLY_REBALANCED.
       - State: TOUCHED / PARTIAL (≥25) / HALF_REBALANCED (≥50) / FULLY_REBALANCED (≥100).
    2. **INVALIDATED** (terminal): close beyond the origin extreme.
    3. **FAILED**: close beyond the open (the body is lost). Not terminal: a later close beyond the origin still invalidates it.
    4. **REACTED** (terminal): not failed, within 5 bars of the first touch, and the close is back beyond the close level by 0.5 ATR (ATR at creation).
12. **APPROACHING** is derived only for the as-of view: a FRESH zone with the last close within 0.5 ATR of the close level. Active zones are FRESH, APPROACHING, TOUCHED, PARTIAL and HALF_REBALANCED.
13. **Decision (enrichment only):**
    - Skipped when the gate verdict is UNAVAILABLE.
    - From **eligible** M15 data, `noWickState` = the latest MEANINGFUL+ event: `{timeframe, time, direction, classification, strength, candleQualityScore, contextScore, relevanceScore, zoneState, authority: "CONTEXT_ONLY"}`.
    - No blocker is added. Verdict, blockers and quality are unchanged, and the authority guard re-runs.
    - On failure the field is null.
14. **Chart overlay** (hidden with a reason unless chart and No Wick analysis are READY for the same timeframe, with every meaningful event and active zone anchored on the drawn candles):
    - A small "NW M/S/X" circle for the last 30 MEANINGFUL+ events (teal bullish, orange bearish).
    - For the 6 most recent active zones: a solid close-level segment and a dashed open-level segment.
    - Off by default.

## OUTPUTS
- `GET /api/v1/no-wick/{symbol}?timeframe=&limit=` → `NoWickAnalysis {features, events, zones, zoneEvents, eligibility, …}`.
- Master Decision: `noWickState` (typed as `NoWickDecisionState` in shared-types).
- System status: phase 5, `enabledEngines` includes `no_wick`.

## STATES
- NoWickClassification: the 12 spec values (NEWS_DRIVEN_NO_WICK is reserved and never emitted).
- NoWickStrength: INSIGNIFICANT / MEANINGFUL / STRONG / EXCEPTIONAL.
- NoWickZoneState: FRESH / APPROACHING / TOUCHED / PARTIAL / HALF_REBALANCED / FULLY_REBALANCED / REACTED / FAILED / INVALIDATED.
- NoWickZoneEventType: TOUCHED / REBALANCE_25 / REBALANCE_50 / REBALANCE_75 / FULLY_REBALANCED / REACTED / FAILED / INVALIDATED.
- NoWickContextFactor: DISPLACEMENT / STRUCTURE / LIQUIDITY / FVG / TREND / SESSION / NEWS.
- ScoreComponentStatus: EVALUATED / NOT_EVALUATED.
- AnalysisIneligibility gains `NO_WICK_ANALYSIS_FAILED`.
- Zone event grammar (enforced by property test): `((T|Ta|Tab|Tabc)R? | TabcU(R|F|FI|I)?)?`. FAILED and INVALIDATED imply the wick crossed the whole body first.

## INVALIDATION
- A zone is INVALIDATED by a close beyond the origin extreme (terminal).
- It is FAILED by a close beyond the open (still tracked for invalidation).
- REACTED is terminal. Wicks only rebalance.
- No-wick events are historical facts at their candle and are never invalidated or re-scored.

## ERROR STATES
| Condition | Result |
|---|---|
| Unsupported timeframe | HTTP 422 |
| Unknown symbol | HTTP 404 |
| INVALID / DISCONNECTED data | 200, empty features/events/zones, reasons listed |
| No Wick engine throws | No Wick analysis withheld (`NO_WICK_ANALYSIS_FAILED`); structure, liquidity, PD arrays unaffected |
| Liquidity or PD stage throws | No Wick still runs; LIQUIDITY or DISPLACEMENT/FVG components `NOT_EVALUATED`, no FVG overlaps |
| No Wick step throws during decision | decision unchanged except `noWickState=null` |
| Synthetic / ineligible data | `noWickState=null` |
| Web: malformed payload, score out of range, unevaluated component with points, news-driven label, zone state/active mismatch, dangling references | overlay hidden with a note; NO_WICK tab shows NO WICK UNAVAILABLE |
| Web: unexpected `noWickState` shape or authority ≠ CONTEXT_ONLY | treated as absent ("—") |

## UNIT TESTS
- `test_no_wick_engine.py` (35). Hand-built scenarios on a flat base where ATR = 1.0, so every value is hand-computed:
  - all 4 shapes × bullish/bearish mirror with exact strength and tags;
  - INSIGNIFICANT classification (no zone, relevance 0);
  - **5% origin wick qualifies but 6% does not** (tiny rejection);
  - one-sided shape needs a 50% body;
  - zero range and doji;
  - first candle without ATR; features use prior candles only;
  - **inside bar** halves relevance;
  - **news** never assigned, SESSION/NEWS never scored;
  - missing engines → NOT_EVALUATED, while empty inputs → EVALUATED 0;
  - context scoring with an FVG completed by the **next** candle (0 points; **no-lookahead**) vs one completed on the candle (15);
  - later structure ignored; liquidity lookback and side;
  - FVG overlap excludes later and already-invalidated zones;
  - **rebalance** 25/50/75/full → REACTED; FAILED → INVALIDATED; direct invalidation; partial states; exact reference levels (bullish and bearish);
  - reaction outside the window ignored; APPROACHING vs FRESH.
- `test_no_wick_properties.py` (11):
  - **No-lookahead:** every 7th prefix of 5 random series gives identical features, events (including all scores and components), zone events and known zone ids, with live displacement/FVG inputs computed per prefix.
  - Zone grammar and invariants: events after creation, invalidation closes beyond origin, levels ordered, 0 ≤ rebalance ≤ 100, scores in range, zone iff MEANINGFUL+, no news label.
  - Coverage: the random data exercises MEANINGFUL/INSIGNIFICANT events and TOUCHED/FULLY_REBALANCED/INVALIDATED zone events.
- Contracts:
  - 6 new enums + `NO_WICK_ANALYSIS_FAILED` checked in Python and TypeScript;
  - wire fields of 6 models in `api_fields.json` checked from both sides (shared-types 69 tests, was 57).
- Web `noWick.test.tsx` (20):
  - rejection matrix (12);
  - overlay (MEANINGFUL+ markers only, close/open edges, cap);
  - sync/hide/off by default;
  - loader;
  - `noWickState` validation;
  - NO_WICK tab content (newest first, n/e components, active zones only, FVG overlap count, "never authorizes a trade");
  - fail-safe;
  - overview row.

## INTEGRATION TESTS
`test_no_wick_api.py` (16):
- Endpoint on M5/M15/H1 is consistent with `/candles`: every anchor is present, zone/event references are valid, 7 components per event, no news label, `obOverlap` NOT_EVALUATED.
- INVALID data withheld; 422/404.
- **No Wick failure isolated** from structure, liquidity and PD arrays.
- **A PD failure leaves No Wick running** with DISPLACEMENT/FVG `NOT_EVALUATED`.
- **Enrichment is verdict-neutral:** verdict, blockers and quality identical; `authority` CONTEXT_ONLY.
- Decision-time failure fails safe; a synthetic decision gets no `noWickState`; field contracts.

**API smoke** (2026-09-13, fixture provider, 300 candles):

| TF | Events (INSIG / MEAN / STRONG / EXC) | Zones | Active | Terminal zone states |
|---|---|---|---|---|
| M5 | 52 (28 / 17 / 5 / 2) | 24 | 0 | 14 REACTED, 9 INVALIDATED |
| M15 | 53 (27 / 17 / 7 / 2) | 26 | 1 | 12 REACTED, 12 INVALIDATED |
| H1 | 64 (24 / 27 / 9 / 4) | 40 | 1 | 20 REACTED, 16 INVALIDATED, 1 FAILED |
| H4 | 40 (18 / 15 / 4 / 3) | 22 | 1 | 10 REACTED, 11 INVALIDATED |

- All 8 zone event types appeared on every timeframe.
- W1 → 422, BTCUSD → 404.
- Market state UNAVAILABLE with `noWickState` null (synthetic).
- System status: phase 5, `no_wick` enabled.
- Latency: M5 ~1.2 s on a cold process, M15 ~0.45 s, H1/H4 ~0.1 s warm.

**Manual browser check** (1600×900, M5 and H1):
- The "No Wick" toggle draws "NW M/S/X" markers alongside the existing overlays, with no overlay-hidden note.
- The NO_WICK tab (no longer "NOT AVAILABLE") matched the API: 6 recent MEANINGFUL+ candles with q/ctx/rel, latest components `DISPLACEMENT 25/25 · STRUCTURE 0/25 · TREND 0/15 · LIQUIDITY 0/20 · FVG 15/15 · SESSION n/e · NEWS n/e`, 0 active zones on M5, "NOT used by the decision: DATA_SYNTHETIC, DATA_STALE", decision context "none".
- The overview shows "No wick (context only) —".
- No horizontal overflow; no-wick requests returned 200.

## UI DISPLAY
- **Chart toolbar:** "No Wick" toggle (off by default, to keep the chart readable next to structure/liquidity/FVG).
- **Overlay:** "NW M" / "NW S" / "NW X" circles below bullish and above bearish candles. Active zones get a solid close-level line and a dashed open-level line (teal bullish, orange bearish).
- **NO_WICK tab:**
  - eligibility;
  - recent MEANINGFUL+ candles (classification, strength, body ATR, inside bar, q/ctx/rel);
  - latest event's context components (`n/e` = not evaluated);
  - active rebalance zones by relevance (close→open, 50% level, state, rebalance %, FVG overlap count);
  - the decision's context and the disclaimer that scores are not probabilities and never authorize a trade.
- **OVERVIEW:** "No wick (context only)" row from the validated `noWickState`.

## KNOWN LIMITATIONS
- **Uncalibrated:** the classification thresholds are the spec's initial research values. The strength cut-offs, the one-sided body floor, score weights and zone rules (touch 0.02 ATR, reaction 0.5 ATR within 5 bars, approach 0.5 ATR) are research defaults, not calibrated on real XAUUSD (still no vendor).
- ⚠️ **REACTED is common on synthetic data** (about half of all zones), and few zones stay active. The reaction rule may be too loose, or random-walk data may simply oscillate. Review against real data before any entry model uses no-wick zones.
- **"Relative size"** (body/median) and range ratios are reported but not scored; strength uses body/ATR only. "HTF alignment" is scored as the analysed timeframe's external trend; true multi-timeframe weighting belongs to scoring (Phase 8).
- **Structure consequence** is scored only when the break is confirmed on the no-wick candle itself; a break a few candles later is deliberately ignored (it would be lookahead).
- **Candle resolution only:** intrabar order is unknown, so a candle that both touches and closes beyond the origin is evaluated in the fixed rule order.
- **Response size:** `features` returns one row per candle (300 by default).
- **Decision latency:** the decision now runs one more M15 pipeline pass (no caching). Not re-measured this phase; Phase 4 was ~0.4 s warm / ~1.5 s cold.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling, UTC-only display.

## STRATEGY VERSION
`0.5.0-phase5`, verdict authority `FAIL_SAFE_ONLY`.

## ADDENDUM — Phase 6 change to No Wick context
Phase 6 made the SESSION component evaluated on M5/M15/H1 (IDEAL 10 / ACCEPTABLE 5 / otherwise 0 points, from the time
quality of the candle's open time; NOT_EVALUATED on H4/D1). To keep evaluated weights at 100, FVG and TREND dropped
from 15 to 10 points each. See `PHASE_6.md` rule 14. Strategy version `0.6.0-phase6`.
