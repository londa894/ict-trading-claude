# Phase 7 — Core Setup State Machine

## GOAL
Build the deterministic setup lifecycle (spec STEP 5) that ties the earlier engines together in the spec's sequence: HTF bias → DOL → liquidity → displacement → MSS → PD array → retracement. Readiness still needs LTF confirmation and risk, which don't exist yet.

The engine tracks one setup model, `LIQUIDITY_SWEEP_MSS`, candle by candle on M15:
- every transition is logged with the reason;
- the current state, the missing next event and a step checklist are exposed;
- PO3 and setup expiration (deferred from Phase 6) are included.

The Master Decision gets `setupState` / `setupType` as context. **Verdict authority stays `FAIL_SAFE_ONLY`**: no LONG_READY / SHORT_READY, no entry, stop or target.

## SCOPE BOUNDARY
- **Phase 8 (Entry & Verdict):**
  - LTF confirmation and its entry models (Conservative Retest, 15M Close, LTF Refinement, No Wick Rebalance, Breaker, IFVG);
  - CONSERVATIVE / STANDARD / AGGRESSIVE modes;
  - early-entry warnings, chase protection;
  - `ENTRY_MISSED`, `LONG_READY` / `SHORT_READY`;
  - entry PD-array selection and verdict authority.
- **Phase 9 (Risk):** stops, targets, R:R and sizing.
- **Phase 16 (Paper Trading):** `ACTIVE` / `CLOSED`.
- These states exist in the enum (all 18 spec states), but the engine never emits them:
  - The market-state guard turns any READY/ACTIVE/CLOSED `setupState` into UNAVAILABLE plus SYSTEM_INTEGRITY_FAILURE.
  - The web client rejects any payload that contains them.
- **Setup timeframe is M15 only** (spec default: 15M context/confirmation, 5M execution). There is still one symbol, XAUUSD.
- "Setup expiration" is implemented. "Session target completion" is covered by "target liquidity reached before a confirmed entry".

## INPUTS
- **M15:** closed candles plus the shared pipeline output on the same series — qualified structure events, liquidity pools and events (including key levels and session pools), FVG zones and events.
- **Bias timeframes H4 and H1:** closed candles → structure analysis → confirmed EXTERNAL BOS/MSS, each known at its breaking candle's close.
- **Session analysis:** daily open and ADR, for PO3.
- **Config:** `packages/strategy-spec/setup.json` (new) and the ATR period from `structure.json`.

## RULES
1. **Bias at a candle close** = the direction shared by the latest known confirmed external BOS/MSS on **every** bias timeframe. A missing timeframe or a disagreement means no bias.

   This is an event-based as-of trend, deliberately different from the decision's state-based `htfBias` (D1+H4), which cannot be replayed candle by candle cheaply.
2. **At most one open setup.** A new setup can start only on a later candle than the one that ended the previous setup.
3. **Transitions** (bullish shown; bearish mirrors it), evaluated on each candle close in this order: bias check → day-end expiry → state rules.

   | State | Entered when |
   |---|---|
   | DISCOVERED | bias exists (setup opened); also when no untaken target is left before the liquidity event |
   | WATCH | an untaken BSL pool above the close exists; target = nearest, refreshed each candle until the liquidity event |
   | SETUP_FORMING | close within 1.0 ATR above an untaken SSL pool (back to WATCH when it moves away) |
   | LIQUIDITY_EVENT | an SSL SWEEP or RECLAIM event on this candle (the most extreme wins) |
   | WAITING_FOR_MSS | a later candle without the confirmation break (a deeper sweep updates the swept extreme) |
   | SETUP_ARMED | confirmed bullish MSS or CHoCH (any level) with `displacementQualifier = PRESENT`, within 12 bars of the liquidity event (possibly the same candle). Protective extreme = lowest low from the liquidity candle to the break candle |
   | WAITING_FOR_RETRACEMENT | a later candle while leg FVGs are live |
   | ENTRY_ZONE_APPROACHING | close within 0.5 ATR above the entry edge (top) of the highest live leg FVG |
   | ENTRY_ZONE_TOUCHED | a candle's low reaches that edge (FVG created before the candle) |
   | WAITING_FOR_CONFIRMATION | the next candle. It stays here: confirmation is Phase 8 |

   - Leg FVGs = bullish FVGs created from the liquidity candle up to 2 bars after the break.
   - The ALLOWED transition table in `engine.py` is enforced at runtime and by property tests.
4. **Invalidation (terminal):**
   - the HTF bias is no longer bullish;
   - before the break, a close below the swept extreme ("liquidity ran");
   - after arming, a close below the protective extreme;
   - the target pool is swept or broken before a confirmed entry ("do not chase");
   - every leg FVG is invalidated.
5. **Expiry (terminal):**
   - no break within 12 bars;
   - no leg FVG by the end of the 2-bar grace window;
   - no touch within 24 bars of the break;
   - the trading day (New York 17:00 roll) ends after the liquidity event.

   Before the liquidity event a setup persists while its bias holds.
6. **Current view** (`currentState`):
   - the open setup's state;
   - BLOCKED when the setup data is ineligible (synthetic / stale / invalid, bias unavailable, any upstream ineligibility, or an engine failure);
   - null when there is no open setup.
7. **Steps checklist:** HTF_BIAS, DOL_TARGET, LIQUIDITY_EVENT, DISPLACEMENT, MSS, PD_ARRAY and RETRACEMENT are DONE or PENDING. LTF_CONFIRMATION and RISK are always NOT_EVALUATED.
8. **PO3** (bias BULLISH; O = daily open, A = ADR; distances are 15% of ADR):

   | Phase | Condition |
   |---|---|
   | MANIPULATION | the day's low reached O − 0.15A |
   | DISTRIBUTION | a manipulation happened, then a later close ≥ O + 0.15A |
   | ACCUMULATION | no manipulation and no expansion |
   | UNCLEAR | expansion without a manipulation, or no bias / open / ADR |

   PO3 is context only.
9. **Decision (enrichment only):**
   - Skipped when the gate verdict is UNAVAILABLE.
   - From eligible data: `setupState` = the open setup's state or `NO_SETUP`, and `setupType = LIQUIDITY_SWEEP_MSS` when a setup is open.
   - Verdict, direction, blockers and quality are unchanged. The authority guard re-runs and also rejects READY/ACTIVE/CLOSED states.
   - On failure or ineligibility, `setupState` stays `NOT_EVALUATED`.
10. **Chart:** a "Setup" toggle (off by default; M15 only; hidden unless every milestone is on the drawn candles) draws:
    - markers for liquidity events, arming, zone touches and invalidation/expiry;
    - for the open setup only, a dotted "Setup DOL" line and a dashed "Setup invalidation" line.

    No entry, stop or take-profit is drawn.

## OUTPUTS
- `GET /api/v1/setups/{symbol}` → `SetupAnalysis {bias, currentState, current, setups, events, po3, eligibility, …}`.
- Master Decision: `setupState`, `setupType`.
- System status: phase 7, `setup_state` enabled.
- Web:
  - OVERVIEW "Setup progress" panel (state, missing next event, step checklist, last closed setup with its reason);
  - "Setup" chart overlay;
  - left nav "Setups" links to the panel.

## STATES
- SetupState: the 18 spec states. Phase 7 emits DISCOVERED … WAITING_FOR_CONFIRMATION, INVALIDATED and EXPIRED. BLOCKED appears only as the as-of view.
- SetupType: LIQUIDITY_SWEEP_MSS.
- SetupStep: HTF_BIAS / DOL_TARGET / LIQUIDITY_EVENT / DISPLACEMENT / MSS / PD_ARRAY / RETRACEMENT / LTF_CONFIRMATION / RISK.
- SetupStepStatus: DONE / PENDING / NOT_EVALUATED.
- Po3Phase: ACCUMULATION / MANIPULATION / DISTRIBUTION / UNCLEAR.
- AnalysisIneligibility gains SETUP_BIAS_UNAVAILABLE and SETUP_ANALYSIS_FAILED.

## INVALIDATION
See rules 4–5. Terminal setups are never revived; a new setup gets a new id. Setup events are historical facts at their candle.

## ERROR STATES
| Condition | Result |
|---|---|
| Unknown symbol | HTTP 404 |
| `mss.breakTypes` includes BOS | configuration load fails (a confirmation must be a reversal) |
| INVALID / DISCONNECTED setup series | 200, no setups/events, `currentState` BLOCKED, reasons listed |
| Synthetic / stale / upstream-ineligible data | setups shown, `currentState` BLOCKED, decision `setupState` NOT_EVALUATED |
| A bias timeframe unusable or without external structure | `SETUP_BIAS_UNAVAILABLE` |
| Engine throws | `SETUP_ANALYSIS_FAILED`, BLOCKED, no setups |
| Illegal transition attempted | raised inside the engine → `SETUP_ANALYSIS_FAILED` (fail-safe) |
| READY/ACTIVE/CLOSED `setupState` reaches the decision | verdict UNAVAILABLE, SYSTEM_INTEGRITY_FAILURE, `setupState` BLOCKED |
| Web: authority state, two open setups, current ≠ open setup, BLOCKED vs eligibility mismatch, LTF/RISK step evaluated, dangling event | panel shows SETUP STATE UNAVAILABLE; overlay hidden with a note |

## UNIT TESTS
- `test_setup_engine.py` (25). Hand-built scenarios on a flat M15 base (ATR 1.0) with stand-in upstream facts:
  - the full happy path to WAITING_FOR_CONFIRMATION with exact candle indexes, bullish **and** mirrored bearish: target, liquidity event, break, protective extreme, leg FVG, touched zone, steps, next event;
  - liquidity ran; no break within the window; a break without displacement doesn't arm (and does when not required);
  - no leg FVG, and an FVG after the grace window isn't a leg FVG;
  - protective close; target taken; every FVG invalidated; retracement window;
  - HTF bias change, with no restart while bias is mixed;
  - a new setup only on a later candle;
  - trading-day-end expiry (and disabled by config);
  - WATCH ↔ SETUP_FORMING ↔ DISCOVERED;
  - no bias, no setup; `bias_at` agreement; BOS refused as a confirmation;
  - PO3: all 4 phases plus bearish, and missing inputs.
- `test_setup_properties.py` (11), on 5 random 4-day M15 walks through the real structure / liquidity / FVG pipeline with random bias timelines:
  - **no lookahead** — every 53rd prefix gives exactly the full run's events up to that candle;
  - every transition is in ALLOWED, each setup starts at DISCOVERED, and terminal states are last;
  - no authority / ENTRY_MISSED / BLOCKED state is ever emitted; at most one open setup;
  - the lifecycle is exercised through INVALIDATED and EXPIRED.
- `test_gate.py` (+8): the authority guard rejects LONG_READY / SHORT_READY / ACTIVE / CLOSED and allows progress states.
- **Contracts:** 5 enums plus 2 ineligibility values checked from Python and TypeScript; 10 wire models in `api_fields.json` checked from both sides (shared-types 98 tests, was 83).
- **Web `setups.test.tsx` (18):**
  - rejection matrix (12, including LONG_READY/SHORT_READY/ENTRY_MISSED and an evaluated RISK step);
  - overlay milestones and DOL/invalidation lines for the open setup only;
  - sync/hide/M15 only; loader;
  - OVERVIEW panel content, no-setup and fail-safe.
- `shell.test.tsx`: the left nav now links "Setups".

## INTEGRATION TESTS
`test_setups_api.py` (17):
- Fixture endpoint is BLOCKED (synthetic) with setups whose events sit on M15 candles and only Phase 7 states; 404.
- INVALID data → no setups.
- **Eligible analysis on non-synthetic fixture data** (bias present, an open setup with its next event, all transitions allowed).
- Engine failure is reported, not raised.
- **Enrichment is verdict-neutral** (verdict WAIT, direction null, blockers and quality identical, state within the Phase 7 set or NO_SETUP).
- Failure fails safe; a synthetic decision keeps NOT_EVALUATED; field contracts.

**API smoke** (2026-09-13, fixture provider):
- setups 200, ~2.0 s cold / ~0.5 s warm.
- BLOCKED (DATA_SYNTHETIC, DATA_STALE); bias BULLISH (H4 and H1 bullish).
- 8 setups (4 INVALIDATED, 3 EXPIRED, 1 open WATCH): 4 "liquidity ran", 2 "no confirmation break", 1 "trading day ended".
- 41 events; PO3 DISTRIBUTION.
- 0 forbidden states; BTCUSD → 404.
- Synthetic decision UNAVAILABLE with `setupState` NOT_EVALUATED.
- Status: phase 7, FAIL_SAFE_ONLY, `setup_state` enabled.

On non-synthetic fixture data 30 hours earlier, 19 setups, 2 armed (both then invalidated by the target being reached).

**Manual browser check** (1600×900, M15, structure/liquidity/FVG off, Setup on):
- Setup sweep / expired / invalidated markers and the "Setup DOL PDH 2024-04-19" line drawn.
- The OVERVIEW panel showed "State BLOCKED · bias BULLISH (H4+H1) · PO3 DISTRIBUTION", missing next "price trading into SSL liquidity", the step checklist with n/e for LTF confirmation and risk, and the last closed setup's reason.
- The intelligence panel scrolls inside its column (no page overflow); the left nav "Setups" links to the panel.

## UI DISPLAY
- **OVERVIEW → Setup progress:** state · bias · PO3 · eligibility; "Missing next"; the step checklist (✓ done, · pending, n/e not evaluated); the last closed setup and its reason; the disclaimer that READY states need Phases 8–9.
- **Decision list:** "Setup state" shows `setupState (setupType)`.
- **Chart:** "Setup" toggle (M15).
- **Left nav:** "Setups" enabled (anchor to the panel).

## KNOWN LIMITATIONS
- **Uncalibrated:** bias timeframes, approach distances, windows and PO3 thresholds are research defaults (still no real vendor). On synthetic data most setups end "liquidity ran" or "no confirmation break".
- ⚠️ **Historical replay uses an as-of pool universe.**
  - Key levels (latest 5 PDH/PDL, 2 PWH/PWL) and session pools (latest 2 per session) are selected relative to the analysis time. A setup replayed far back can see a different target pool than was live then.
  - The current setup, which is what the decision uses, is unaffected.
  - The engine's no-lookahead property is proven with prefix-stable pools (swings/EQ). Backtesting and replay (Phases 18–19) must rebuild inputs per step.
- **Setup bias differs from the decision's `htfBias`** (event-based H4+H1 vs state-based D1+H4); both are shown.
- **One setup model and one open setup** per symbol; no competing long and short setups at the same time.
- **Retracement uses the leg's FVGs only.** Order blocks, breakers and IFVG entries come in Phase 8/20+. WAITING_FOR_CONFIRMATION persists until invalidation or day end.
- **Latency:** a setups request runs the M15 pipeline plus H4/H1 structure plus sessions (~0.5 s warm), and the decision adds it again. Still no caching.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling.

## STRATEGY VERSION
`0.7.0-phase7`, verdict authority `FAIL_SAFE_ONLY`.
