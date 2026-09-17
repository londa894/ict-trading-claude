# Phase 14 — Basic Macro

## GOAL
Build basic macro context (spec STEP 7 macro states, STEP 8 Macro weight 5). For each market:
- read DXY, US 2Y, US 10Y, US 10Y real yield and VIX daily series;
- turn their recent direction and the market's configured relationships into a macro **bias** (score −1…+1);
- compare the bias with the open setup's direction to get a **state**: STRONGLY_SUPPORTIVE / SUPPORTIVE / NEUTRAL / CONFLICT / STRONG_CONFLICT / UNAVAILABLE.

The state feeds the MACRO score factor, conflict, the devil's advocate, the Master Decision, alerts, the assistant and the MACRO tab.

**Macro is context:**
- it modifies score, confidence (through conflict) and narrative;
- it never replaces technical structure, never adds a blocker and never changes the verdict.

**Verdict authority stays `FAIL_SAFE_ONLY`.**

## SCOPE BOUNDARY
- **No live macro vendor and no scraping.** Providers:
  - `unconfigured` (default; MACRO not evaluated);
  - `file` (a server-side JSON of daily series the user maintains from a licensed source);
  - `fixture` (synthetic series: shown, never scored).
- **Advanced macro** (Phase 20+) is not built: SMT, risk-on/off regimes, currency-strength meters, Fed-expectation pricing, geopolitical source confidence, reaction divergence, revision tracking.
- **The USD data surprise is keyword-based** (event name → USD direction) and uses HIGH+ USD releases in the news assessment only. Other currencies' surprises are not interpreted.
- Only the DXY correlation regime is measured.
- Silver's industrial-growth sensitivity is not modelled beyond its drivers.
- No macro series storage or history (no database writes).

## INPUTS
- **Macro snapshot** from the provider: source, `fetchedAt`, series `{id, name, unit, observations[{date, value}]}`. Dates are New York calendar dates in increasing order, without duplicates.
- **Market D1 closes** (CandleService, 80 bars) for the correlation regime. Days are labelled by trading day (17:00 New York close).
- **News assessment** (Phase 13) for released USD surprises.
- **Open setup direction** from the setup run (the evaluation) **only when its data is eligible for a decision**, or the `direction` query on the macro route.
- **Config (new):** `packages/strategy-spec/macro.json`:
  - trend: lookback 5 observations, volatility window 20, min 25 observations, flat |z| < 0.5, stale after 4 days; required series: DXY;
  - thresholds: supportive 0.2, strongly supportive 0.6;
  - correlation: DXY, 20 matching daily changes, |r| < 0.2 = WEAK, inverted weight factor 0.5;
  - surprise: weight 1, look-back 6 h (bounded by the news list window; validated), min importance HIGH, USD-positive / USD-negative keywords;
  - drivers per market, e.g. XAUUSD: DXY −1 ×3, US10Y_REAL −1 ×3 (fallback US10Y), US2Y −1 ×1, VIX +1 ×1; USDJPY: DXY +1 ×3, US10Y +1 ×2, VIX −1 ×1.
- **scoring.json:** `points.macro` = STRONGLY_SUPPORTIVE 5, SUPPORTIVE 4, NEUTRAL 2.5, CONFLICT 0, STRONG_CONFLICT 0; `conflict.macroConflict` 15, `conflict.macroStrongConflict` 30.
- **Settings:** `MACRO_PROVIDER` (`unconfigured` | `fixture` | `file`), `MACRO_FILE_PATH`. Template: `config/macro.example.json` (example values only).

## RULES
1. **Series trend:**
   - change = last − value 5 observations earlier;
   - z = change / (stdev of the last 20 daily changes × √5);
   - UP when z > 0.5, DOWN when z < −0.5, else FLAT (no variance: sign of the change);
   - fewer than 25 observations → not usable; last observation older than 4 days → stale.
2. **Availability:** UNAVAILABLE (bias UNAVAILABLE, score null) when:
   - no snapshot (unconfigured / missing / invalid file, with field paths only);
   - a required series (DXY) is missing, too short or stale;
   - the market has no configured drivers.
3. **Drivers:**
   - contribution = sign(direction) × relationship × weight;
   - a missing or stale primary uses its fallback (with a warning);
   - drivers with neither are NOT EVALUATED and leave their weight out.
4. **USD data surprise:**
   - uses released / revised / completed HIGH+ USD events inside the look-back with a numeric surprise;
   - a USD-positive keyword (CPI, PCE, PPI, payrolls, retail sales, GDP, ISM, PMI, earnings) beating forecast = USD up; a USD-negative keyword (unemployment rate, jobless claims) beating forecast = USD down;
   - the net sign is added as a driver with DXY's relationship × weight 1;
   - a synthetic or unavailable calendar supplies nothing.
5. **Correlation regime:**
   - Pearson r of the market's D1 close returns versus DXY changes on the last 20 matching days;
   - |r| < 0.2 → WEAK;
   - sign matching the relationship → ALIGNED; opposite → INVERTED (DXY weight × 0.5 plus a warning);
   - fewer than 20 matching days → UNAVAILABLE (weights unchanged).
6. **Score and bias:**
   - score = Σ contributions / Σ evaluated weights (rounded to 3 decimals);
   - BULLISH ≥ 0.2, BEARISH ≤ −0.2, else NEUTRAL.
7. **State versus a direction D** (s = score for BULLISH, −score for BEARISH):
   - s ≥ 0.6 STRONGLY_SUPPORTIVE;
   - s ≥ 0.2 SUPPORTIVE;
   - s > −0.2 NEUTRAL;
   - s > −0.6 CONFLICT;
   - else STRONG_CONFLICT.

   With no direction the state is null (bias only).
8. **Scoring:** the MACRO factor is **EVALUATED** only when macro is available, not synthetic and assessed for the setup's direction.
   - Points follow the state (max 5).
   - CONFLICT adds conflict 15 and STRONG_CONFLICT adds 30, with evidence-against lines. Conflict ≥ the downgrade threshold lowers confidence.
   - Otherwise MACRO is NOT_EVALUATED, with the reason in the item and the devil's advocate.
   - An inverted correlation is named in the devil's advocate.
   - Macro never touches hard blockers, missing gates or the outcome.
9. **Master Decision:**
   - `macroState` = {authority CONTEXT_ONLY, state, bias, score, available, synthetic, correlationRegime, drivers};
   - set from the evaluation, or directly from the macro service when market data is unusable (bias only);
   - no blockers; `enforce_verdict_authority` runs afterwards.
10. **Alerts:**
    - `MACRO_SHIFT` (WATCH/MEDIUM) when the available bias changes between two cycles;
    - not on the silent baseline, not when data drops out (the last bias is kept, so a return does not re-announce);
    - the message marks synthetic data and says macro never creates a trade.
11. **Assistant:**
    - tool `get_macro_state` is now available (payload plus authority CONTEXT_ONLY);
    - the MACRO intent answers with bias, score, state versus the setup, evaluated drivers and correlation regime from tool facts only;
    - unavailable macro becomes an UNKNOWN;
    - glossary term MACRO_CONTEXT.

## OUTPUTS
- `GET /api/v1/macro/{symbol}?direction=BULLISH|BEARISH` → `MacroAssessment` (bias, score, state, direction, drivers, series trends, correlation, provider/source/synthetic/available/fetchedAt/reason, warnings, thresholds).
- `GET /api/v1/macro/series` → `MacroSeriesResponse` (the provider's raw series or the reason).
- `DecisionEvaluation.macro`, `MasterDecision.macroState`.
- Enums: `MacroState`, `MacroBias`, `MacroSeriesId`, `SeriesDirection`, `CorrelationRegime`; `AlertType` + MACRO_SHIFT.
- Contract fields: `MacroObservation`, `MacroSeries`, `MacroFile`, `SeriesTrend`, `DriverContribution`, `CorrelationInfo`, `MacroAssessment`, `MacroSeriesResponse`; updated `DecisionEvaluation`.

## STATES
- Macro state: STRONGLY_SUPPORTIVE / SUPPORTIVE / NEUTRAL / CONFLICT / STRONG_CONFLICT / UNAVAILABLE (null without a direction).
- Bias: BULLISH / BEARISH / NEUTRAL / UNAVAILABLE.
- Series direction: UP / DOWN / FLAT. Correlation regime: ALIGNED / WEAK / INVERTED / UNAVAILABLE.

## INVALIDATION
- Recomputed at every request. The file is re-read when it changes.
- A series goes stale 4 days after its last observation, which covers a normal weekend. A stale DXY makes macro UNAVAILABLE.
- The state follows the current open setup; when the setup changes direction or ends, the state changes or becomes null.

## ERROR STATES
| Condition | Result |
|---|---|
| `MACRO_PROVIDER=unconfigured` (default) | UNAVAILABLE; MACRO NOT_EVALUATED; no blocker |
| File missing / invalid (field paths only) / DXY missing, short or stale | UNAVAILABLE with the reason |
| Provider or D1-load exception | UNAVAILABLE ("macro provider failed") / correlation UNAVAILABLE |
| Synthetic series | shown with the synthetic flag; never scored |
| Unknown symbol; invalid `direction` | 404; 422 |
| Web: bias without data, score out of range, bias/state not matching the score and thresholds, state without direction, blockers on macro, unknown regime, macro for another market; evaluation with MACRO EVALUATED on untrusted or other-direction macro | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_macro_engine.py` (25):
- trend UP/DOWN/FLAT from z, minimum observations, stale, no variance;
- gold bullish on a falling dollar and real yields (0.75, STRONGLY_SUPPORTIVE / STRONG_CONFLICT / null);
- mixed drivers are NEUTRAL; USDJPY relationships flip;
- required series missing/stale; no provider; fallback series and unevaluated drivers; unknown market; synthetic flag;
- 6 state threshold cases;
- Pearson; correlation ALIGNED/INVERTED/short/missing; inverted weight halves DXY;
- USD surprise keywords (positive, negative, none, importance floor, synthetic calendar); a hot CPI weighs against gold;
- config validation (thresholds, observations, surprise look-back, relationship); series ordering;
- providers: unconfigured, deterministic fixture, factory, file (valid, missing, invalid without echoing values, unreadable).

`tests/unit/test_scoring_engine.py` (+10): 5 macro states → points and conflict (no blocker or outcome change); 4 unscored cases (not wired, unavailable, synthetic, other direction); inverted-correlation advocate.

`tests/unit/test_alert_rules.py` (+1): MACRO_SHIFT only on a bias change, kept through an outage, synthetic marked.

## INTEGRATION TESTS
`tests/integration/test_macro_api.py` (15):
- unconfigured macro unavailable through macro, series, evaluation and decision (no MACRO blockers);
- file macro: gold BULLISH, drivers, direction query state, USDJPY BEARISH, series, 404/422, decision macroState;
- stale and invalid files without echoing values;
- fixture synthetic and never scored;
- **unusable market data still carries macro context** (one bias everywhere) with no state versus an untrusted setup (evaluation, decision and assistant);
- assistant MACRO answer from the tool (and UNKNOWN when unconfigured);
- 9 contract-field cases.

Updated: assistant capabilities/unknowns (macro tool available), evaluation fixture (`macro`), version strings.

Web `tests/macro.test.tsx` (16):
- macro trust rules (4 accepted, 9 rejected) and threshold mirroring;
- loader direction query;
- evaluation MACRO factor acceptance;
- MACRO tab (bias/state, correlation, fallback and unevaluated drivers), unavailable / rejected / synthetic displays;
- status bar macro label from the decision.

Updated: news tab text, evaluation fixtures (`macro: null`).

## UI DISPLAY
- **Status bar Macro:** `BULLISH · SUPPORTIVE`, `NEUTRAL`, `UNAVAILABLE` (never assumed), `· synthetic`.
- **MACRO tab:** a macro section above the news gate:
  - bias with score and the state versus the open setup direction (or "no open setup direction");
  - correlation regime with r; provider / source / SYNTHETIC (not scored);
  - drivers table: driver (fallback noted), direction or NOT EVALUATED, relationship, weight, contribution;
  - warnings; the unavailable reason when not evaluated;
  - a note that macro never blocks.
- The page loads macro with the evaluation's setup direction, so the tab and the status bar show one state.

## KNOWN LIMITATIONS
- ⚠️ **Verdict authority:** unchanged at FAIL_SAFE_ONLY. Every required gate exists and macro is context, not a gate. Switching to FULL still needs an explicit decision and a directional verdict path.
- ⚠️ **No real macro feed:** the file provider depends on the user keeping licensed daily series current (DXY within 4 days). Otherwise MACRO is NOT_EVALUATED; this is fail safe and nothing is blocked.
- **Uncalibrated research defaults:** relationships, weights, z threshold, look-backs, surprise keywords and point values.
- **Correlation only against DXY**, from D1 closes of the market-data provider. With the fixture provider (80 synthetic days) the regime is computed but meaningless.
- **Surprise look-back is 6 h** (the news assessment lists events only 6 h back), not the 24 h first planned; keyword mapping is English-name based.
- A bias computed from end-of-day series can lag intraday moves by up to a day.
- No macro history or journal record yet (Phase 15).

## STRATEGY VERSION
`0.14.0-phase14` (phase 14, "Basic Macro"). Engines: + `macro`. Verdict authority: **FAIL_SAFE_ONLY**.
