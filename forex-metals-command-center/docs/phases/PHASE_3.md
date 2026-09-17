# Phase 3 — Liquidity

## GOAL
Deterministically model liquidity (spec STEP 2, liquidity half):
- BSL/SSL pools, internal vs external, EQH/EQL, PDH/PDL/PWH/PWL;
- the states FRESH / APPROACHING / TOUCHED / SWEPT / RUN / BROKEN / RECLAIMED, with sweep vs run rules;
- a chronological event log;
- LiquidityMagnetScore 0–100, primary/secondary DOL and DOL confidence, with `DOL_UNCLEAR` → WAIT;
- the Phase 2 structure events' `liquidityQualifier`.

Expose it through the API, chart overlay and LIQUIDITY tab, and enrich the Master Decision **without** any authority over the verdict.

## SCOPE BOUNDARY
- **Asian / London / New York session highs/lows** are deferred to the Session & Time engine (Phase 6), which owns session windows, DST session rules and kill zones. They will plug into the same pool model as new pool types.
- **Numeric MTF alignment score** is deferred to scoring (Phase 8), as in Phase 2.

## INPUTS
- Closed candles of the analysed timeframe from `CandleService.load_series`, and its Phase 2 structure (internal + external swings).
- Closed New York-close D1 candles for key levels (~22 candles).
- `packages/strategy-spec/liquidity.json` (thresholds, magnet weights, DOL margins, decision timeframes); ATR period from `structure.json`.

## RULES
1. **One pipeline for every surface** (`services/analysis/pipeline.py`): structure → liquidity → DOL → qualifiers. The API, chart overlay, STRUCTURE/LIQUIDITY tabs and the decision all read the same result.
2. **Swing pools:**
   - Each confirmed swing high becomes a BSL pool; each swing low an SSL pool.
   - Internal and external pivots on the same candle are merged into one pool.
   - A pool is known from its swing's confirmation close and evaluated from the next candle.
   - Scope becomes EXTERNAL from the external pivot's confirmation.
3. **EQH/EQL:**
   - A newly active swing pool that is within `equal.toleranceAtr` (0.1×ATR) of an **untaken** same-side swing pool, and at least `equal.minBarsApart` (3) pivots away, forms or extends a cluster.
   - The cluster price is the outermost member (max for EQH, min for EQL).
4. **Key levels (no lookahead):**
   - PDH/PDL: each closed trading day, known at 17:00 New York.
   - PWH/PWL: each completed trading week (NY close dates in the same ISO week), known at the Friday close. If Friday is missing (holiday), it's known at the first close of the next week. An unprovable week produces no level.
   - A level is evaluated only from the first candle opening at or after `knownAt`.
5. **State machine** (BSL shown; SSL mirrors it). Tolerance = `touchToleranceAtr` 0.05×ATR:
   - **TOUCH** (→ TOUCHED): `P − tol ≤ high ≤ P`. It's counted once per episode.
   - **SWEEP** (→ SWEPT): `high > P` and `close ≤ P` on the same candle (the configured sweep rule).
   - **SWEEP_FAILED** (→ RUN): a close above the sweep high within `sweep.failureWindowBars` (3). This is the "false sweep".
   - **BREAK** (→ BROKEN): `high > P` and `close > P`.
   - **RECLAIM** (→ RECLAIMED): a close back at or below P within `break.acceptanceWindowBars` (3).
   - **RUN** (→ RUN): a later candle in the window, still above P, with best close since the break ≥ `P + 0.5×ATR(break)`.
   - Otherwise the pool stays BROKEN. RUN and RECLAIMED are terminal; SWEPT and BROKEN become terminal when their windows expire.
   - Untaken = FRESH/TOUCHED. **APPROACHING** is derived as of now (untaken and within 0.5×ATR of the last close); it is never an event.
6. **Pools known at the last close** (e.g. a swing confirmed by the latest candle, or PDH known exactly at the latest close) are included in the snapshot, but no candle has evaluated them yet.
7. **LiquidityMagnetScore** (untaken pools only; a deterministic ranking, **not a probability**):
   - Type (max 30): PWH/PWL 30 · PDH/PDL/EQ 25 · external swing 20 · internal swing 10.
   - Cluster (max 15): 5 per extra EQ member plus 2 per touch episode.
   - Proximity (max 30): linear to 0 at 10 ATR.
   - Stack (max 15): 5 per other untaken pool within 0.25 ATR.
   - Trend (10): BSL in a bullish / SSL in a bearish external trend.
8. **DOL:**
   - Primary = the highest magnet score (ties: nearer, then id). Secondary = the next pool more than 0.25 ATR away from the primary's price.
   - Confidence comes from the margin to the best opposite-side pool: ≥25 HIGH, ≥15 MODERATE, ≥8 LOW, otherwise **UNCLEAR** (two-sided).
   - It is also UNCLEAR when there are no untaken pools or ATR = 0.
9. **Eligibility:** structure eligibility (not synthetic, not stale/invalid, ≥50 candles) plus key levels available, plus no liquidity failure. INVALID/DISCONNECTED data → no liquidity analysis at all.
10. **Decision enrichment:**
    - Skipped when the gate verdict is UNAVAILABLE.
    - From eligible data only: `primaryDol` and `secondaryDol` (H1), and `liquidityEvent` (latest M15 SWEEP/BREAK/RUN/RECLAIM/SWEEP_FAILED).
    - If eligible and UNCLEAR, add the WAIT-class `DOL_UNCLEAR` blocker. Nothing else changes: verdict, other blockers and data quality are untouched, and the authority guard re-runs.
    - Any failure sets the fields to null and adds no blocker.
11. **Structure qualifiers:** an event is `liquidityQualifier = PRESENT` if an opposite-side SWEEP/RECLAIM occurred in the 20 bars up to and including the event candle (bearish needs BSL taken, bullish needs SSL taken). Otherwise ABSENT. If liquidity fails, it stays NOT_EVALUATED. `displacementQualifier` stays NOT_EVALUATED (Phase 4).
12. **Chart overlay:**
    - Drawn only when chart and liquidity are READY on the same timeframe and every event time is on the drawn candles; otherwise hidden with a reason.
    - Lines: DOL/DOL2 (solid), plus the latest untaken level of each key/EQ type (dashed). An older level never stands in for a taken or DOL-drawn latest one.
    - Markers: up to 30 taking events (sweep/false sweep/run/reclaim); touches and breaks are not drawn.

## OUTPUTS
- `GET /api/v1/liquidity/{symbol}?timeframe=&limit=` → `LiquidityAnalysis {pools, events, dol, eligibility, keyLevelsAvailable, …}`.
- Structure events carry `liquidityQualifier` PRESENT/ABSENT.
- Master Decision: `primaryDol`, `secondaryDol`, `liquidityEvent`, and possibly the `DOL_UNCLEAR` blocker.

## STATES
- LiquidityState: FRESH / APPROACHING / TOUCHED / SWEPT / RUN / BROKEN / RECLAIMED
- LiquidityEventType: TOUCH / SWEEP / BREAK / RUN / RECLAIM / SWEEP_FAILED
- DolConfidence: HIGH / MODERATE / LOW / UNCLEAR
- Pool types: SWING_HIGH / SWING_LOW / EQH / EQL / PDH / PDL / PWH / PWL; side BSL/SSL; scope INTERNAL/EXTERNAL

## INVALIDATION
- A pool is taken by SWEEP or BREAK, and then no longer ranks for DOL.
- A sweep is invalidated by SWEEP_FAILED; a break by RECLAIM.
- An EQ cluster can't absorb taken members.
- DOL is invalidated when its pool is taken (re-evaluated every request).

## ERROR STATES
| Condition | Result |
|---|---|
| Unsupported timeframe / limit | HTTP 422 |
| Unknown symbol | HTTP 404 |
| INVALID / DISCONNECTED data | 200, `pools=[]`, `events=[]`, `dol=null`, reasons listed |
| D1 key-level series unusable | analysis returned, `keyLevelsAvailable=false`, ineligible `KEY_LEVELS_UNAVAILABLE` |
| Liquidity engine throws | structure unaffected (qualifiers NOT_EVALUATED); liquidity withheld, `LIQUIDITY_ANALYSIS_FAILED` |
| Liquidity step throws during decision | decision unchanged except DOL/event fields null |
| Web: malformed / inconsistent payload | overlay hidden with note; LIQUIDITY tab shows LIQUIDITY UNAVAILABLE |

## UNIT TESTS
- `test_liquidity_engine.py` (16 hand-built scenarios with exact events): confirmation timing, sweep, false sweep, sweep window expiry, break→reclaim, break→run, stays broken, touch episodes, SSL mirror of all paths, EQH formation, EQH sweep, no EQH (too close / out of tolerance / first taken), EQL mirror, scope upgrade timing, key level unsweepable before known, APPROACHING derivation.
- `test_liquidity_levels_scoring.py`: PDH/PDL known at close, PWH/PWL at Friday close, holiday week, magnet components and caps, DOL primary/secondary/distinct price, confidence buckets, two-sided/empty/zero-ATR UNCLEAR, qualifiers PRESENT/ABSENT/no-lookahead/touches don't count.
- `test_liquidity_properties.py`:
  - **No-lookahead:** every prefix of 5 random series gives the same events and pool set known at that point.
  - Per-event price invariants.
  - Per-pool transition grammar `T*(S F? | B (R|C)?)`.
  - The trend input changes scores only.
  - Coverage check: the data contains every pool type (including EQ and key levels) and every event type.
- Field contracts (Python + TypeScript) for 5 models; 6 new enums; `DOL_UNCLEAR`, `KEY_LEVELS_UNAVAILABLE`, `LIQUIDITY_ANALYSIS_FAILED`.
- Web `liquidity.test.tsx` (18): rejection matrix (10), overlay content, the no-mislabelled-older-level rule (both paths), sync/hide rules, merge ordering, loader, LIQUIDITY tab content and fail-safe, overview DOL.

## INTEGRATION TESTS
`test_liquidity_api.py` (13):
- Endpoint on 4 timeframes consistent with `/candles` (event anchors, pool references, score/taken pairing, PDH/PDL present).
- Structure events carry qualifiers.
- INVALID withheld; 422/404.
- Missing key levels → ineligible.
- Engine failure keeps structure.
- **Enrichment only adds `DOL_UNCLEAR` and never changes the verdict**, with the blocker exactly when context is UNCLEAR.
- Forced UNCLEAR adds an ordered blocker.
- Decision-time failure fails safe.
- A synthetic decision gets no liquidity context.

**Manual browser check** (2026-09-13, fixture provider, 1600×900, H1):
- DOL line on PWH (magnet 78.7), DOL2 on a swing high, PDH/PDL/PWL dashed lines, sweep/run markers drawn together with structure.
- The LIQUIDITY tab matched the API: confidence HIGH (margin 31.3), key levels, nearest pools marked APPROACHING, events, "NOT used by the decision: DATA_SYNTHETIC, DATA_STALE".
- The TradingView Desktop row still showed CONNECTED.

## UI DISPLAY
- **Chart toolbar:** "Liquidity" toggle (on by default).
- **Overlay:** DOL/DOL2 solid gold lines; PDH/PDL/PWH/PWL/EQH/EQL dashed lines; markers "Sweep", "False sweep", "Run", "Reclaim" (pool type named for key/EQ levels only).
- **LIQUIDITY tab:** eligibility; DOL confidence, primary, secondary and why; key levels with states; the 4 nearest untaken pools above and below with state and magnet score; recent liquidity events (no touches); what the decision uses.
- **OVERVIEW:** primary DOL and liquidity event from the decision.
- **STRUCTURE tab:** events that followed a sweep/reclaim are marked "after liquidity taken".

## KNOWN LIMITATIONS
- **Session highs/lows** (Asia/London/NY) are not yet pools (Phase 6).
- **Uncalibrated defaults:** all thresholds and magnet weights are research defaults, not calibrated on real XAUUSD data (still no vendor). The magnet score ranks pools and must not be read as probability.
- **EQ clusters use swing pivots only:** equal highs formed inside a pivot's shoulder are not detected.
- **Pool prices are single levels, not zones.** Sweep/touch use candle close and extremes only; intrabar sequence is unknown (the spec's intrabar ambiguity disclosure applies to backtesting later).
- **Decision latency:** the decision now runs the pipeline on several timeframes. Measured on the fixture: warm ~0.5 s (Phase 2: ~0.12 s), cold ~1.9 s; a liquidity endpoint request ~60–80 ms. There's no caching yet.
- **Fixture ties:** the synthetic data can produce ties (e.g. PWH = PDH on a Friday), which appear as stacked lines.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling, UTC-only display.

## STRATEGY VERSION
`0.3.0-phase3`, verdict authority `FAIL_SAFE_ONLY`.
