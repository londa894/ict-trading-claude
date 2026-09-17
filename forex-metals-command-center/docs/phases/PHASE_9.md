# Phase 9 — Risk Calculator

## GOAL
Build the broker-free risk management and position sizing engine (spec STEP 6): a manual account profile, risk profiles, locks, a loss-aware risk budget and never-guessed position sizing. Risk has final veto.

It runs on the Phase 8 confirmed plan, feeds the evaluation and the Master Decision, and has its own API and RISK tab.

**Verdict authority stays `FAIL_SAFE_ONLY`.** The news blackout gate (Phase 13) is still missing, so:
- the evaluation's missing gates drop `RISK_GATE_MISSING` but keep `NEWS_GATE_MISSING`;
- a clean risk check still leaves a confirmed plan `BLOCKED` / `CONFIRMED_PENDING_GATES`;
- a risk lock on a confirmed plan makes the outcome `NO_TRADE` (the veto);
- the Master Decision gets a risk status and risk blockers, never a position size, entry, stop or target.

## SCOPE BOUNDARY
- **No broker, no account connection.** Balance, day P/L, open positions and contract specs are typed in by the user. Nothing is read from or sent to a broker.
- **News lock:** `news` is always `NOT_EVALUATED` (calendar: Phase 13).
- **Journal / paper trading (Phases 15–16):** would supply realized P/L, trade counts and open positions automatically. Until then they are manual and a state from another trading day locks.
- **Live spread / slippage:** there is no quote feed; only the user's typical spread is used.
- **Correlation:** a currency-exposure overlap warning only. There is no dynamic correlation regime (Phase 14 / 20+).
- **Authority FULL:** not decided here (after Phase 13 at the earliest, as an explicit decision).

## INPUTS
- **Risk config (new):** `packages/strategy-spec/risk.json`:
  - presets CONSERVATIVE / STANDARD / AGGRESSIVE;
  - hard limits that cap every profile, including CUSTOM;
  - volatility lock (M15, ATR 14, 96-bar baseline, lock at 2.5×);
  - maximum spread as a share of the stop (10%);
  - spec consistency tolerance (1%);
  - conversion-rate maximum age (24 h).
- **Manual profile file (new, server-side, git-ignored):** path from `RISK_PROFILE_PATH`; template in `config/risk_profile.example.json`. It contains:
  - `account`: spec `AccountProfile` (balance, currency, profile, optional leverage, optional prop rules; CUSTOM sets every limit);
  - `state`: trading day, realized P/L today and this week, trades today, consecutive losses, open positions (symbol, direction, remaining risk, in loss);
  - `instrumentSpecs`: the user's broker contract specs (spec `InstrumentSpec` plus an optional platform pip size);
  - `conversionRates`: user-entered FX rates with an as-of time.
- **The confirmed plan:** the Phase 8 entry plan of the open setup when it is `BLOCKED` (direction, entry, stop).
- **M15 closed candles:** from the same setup run, for the volatility lock.

## RULES
1. **Limits.** A preset takes its limits from `risk.json`. A preset with explicit limits is rejected, so presets cannot be quietly edited. CUSTOM must set all seven limits and may not exceed any hard limit, otherwise the status is `INVALID_PROFILE`.

   | Preset | Risk/trade | Daily | Weekly | Max open risk | Trades/day | Positions | Consecutive losses |
   |---|---|---|---|---|---|---|---|
   | CONSERVATIVE | 0.5% | 1.5% | 3% | 1% | 2 | 1 | 2 |
   | STANDARD | 1% | 3% | 6% | 2% | 3 | 2 | 3 |
   | AGGRESSIVE | 2% | 5% | 10% | 4% | 5 | 3 | 4 |
   | Hard limits | 2% | 6% | 12% | 6% | 10 | 5 | 5 |

2. **Budget** (account currency; B = balance, loss = max(0, −realized P/L)):
   - per trade = B × risk%;
   - daily left = (B − P/L today) × daily% − loss today − open risk;
   - weekly left = (B − P/L week) × weekly% − loss week − open risk;
   - open-risk left = B × max open% − open risk;
   - prop left (if set) = min(day start × prop daily% − loss today, B − start × (1 − prop total%)) − open risk;
   - **effective = max(0, min of all caps).** Profits never raise a loss limit. Every cap shrinks after a loss. Risk can only go down after losing: no martingale, no revenge sizing. A property test checks this over 40 random accounts.
3. **Account locks:**
   - `ACCOUNT_STATE_STALE`: state trading day ≠ the current New York trading day;
   - `DAILY_LOSS_LIMIT` / `WEEKLY_LOSS_LIMIT` / `MAX_OPEN_RISK` / `PROP_DAILY_DRAWDOWN` / `PROP_TOTAL_DRAWDOWN`: that cap ≤ 0;
   - `MAX_POSITIONS` / `MAX_TRADES_PER_DAY` / `CONSECUTIVE_LOSSES`: the count reached its maximum;
   - `VOLATILITY`: M15 ATR(14) ÷ median ATR over the previous 96 bars ≥ 2.5.
4. **Trade locks (with a plan):**
   - `INVALID_STOP`: the stop is not on the losing side;
   - `ADDING_TO_LOSER`: an open same-symbol, same-direction position is in loss;
   - `UNSAFE_SPREAD`: typical spread > 10% of the stop distance;
   - `INSUFFICIENT_MARGIN`: volume × contract size × entry × conversion ÷ leverage > balance;
   - `BELOW_MIN_VOLUME`: the floored volume is under the broker minimum.

   `CORRELATED_EXPOSURE` is a warning: an open position shares a currency exposure with the same sign (for example, long XAGUSD while going long XAUUSD is short USD twice).
5. **Sizing (never guessed):**
   - spec source: the catalog spec if it exists (none do), else the user's spec (`SIZED_FROM_USER_SPEC`);
   - no spec → `POSITION_SIZE_UNVERIFIED`;
   - the spec must be internally consistent: tick value = contract size × tick size in the quote currency, within 1%. XAUUSD 100 × 0.01 = 1 USD; EURUSD 100,000 × 0.00001 = 1 USD; USDJPY 100,000 × 0.001 = 100 JPY. Otherwise `SPEC_INCONSISTENT` → unverified. This catches a tick value typed in the account currency;
   - conversion: quote = account currency → 1. Otherwise a fresh (≤ 24 h, not in the future) direct or inverse user rate; rates are never chained. Missing → `CONVERSION_UNAVAILABLE` → unverified;
   - sizing distance = |entry − stop| + typical spread (when known);
   - risk per 1.0 volume = sizing distance ÷ tick size × tick value × conversion;
   - volume = floor(effective ÷ risk per volume) to the volume step, in exact decimal arithmetic. It is never rounded up; one more step would exceed the budget;
   - reported: price distance, points (÷ tick size), platform pips (÷ pip size if configured), spread, volume, risk at the stop with and without spread, risk %, margin.
6. **Status** (precedence): `NOT_CONFIGURED` (no file) · `INVALID_PROFILE` (file or limits invalid) · `UNAVAILABLE` (engine failure) · `LOCKED` (any lock) · `SIZE_UNVERIFIED` (plan, no verifiable size) · `WITHIN_LIMITS` (plan sized) · `CLEAR` (no plan, no lock).
7. **Blockers:**

   | Condition | Blocker |
   |---|---|
   | NOT_CONFIGURED | `RISK_PROFILE_MISSING` |
   | INVALID_PROFILE | `RISK_PROFILE_INVALID` |
   | UNAVAILABLE | `RISK_GATE_MISSING` |
   | any lock | `RISK_LOCKED` |
   | UNSAFE_SPREAD lock | also `UNSAFE_SPREAD` |
   | unverified size | `POSITION_SIZE_UNVERIFIED` |

   All of these are WAIT-class while authority is FAIL_SAFE_ONLY.
8. **Evaluation (scoring):**
   - `missingGates` = [`NEWS_GATE_MISSING`] when risk is wired;
   - hard blockers include the risk blockers;
   - `RISK_RR` points need R:R met **and** no risk lock;
   - a confirmed plan with a risk lock → `NO_TRADE`;
   - the devil's advocate lists every lock, a missing or invalid profile, and an unverified size;
   - confidence stays capped at MODERATE (the news gate is missing).
9. **Master Decision:**
   - `riskStatus` = the risk status, and the risk blockers are added, whatever the data eligibility (only when the verdict is not UNAVAILABLE);
   - `INSTRUMENT_SPEC_MISSING` is dropped when an eligible evaluation has a user spec;
   - the verdict is never changed and prices and size stay null. `enforce_verdict_authority` still runs after the enrichment.
10. **What-if calculator:**
   - the same engine, fed only by the request (account, optional state, optional spec, optional conversion rate);
   - it stores and logs nothing; volatility is not checked (`VOLATILITY_NOT_CHECKED`); without a state, account locks are not checked (`ACCOUNT_STATE_NOT_PROVIDED`);
   - presets only from the UI (CUSTOM lives in the profile file).

## OUTPUTS
- `GET /api/v1/risk/{symbol}` → `RiskAssessment`: status, profile, currency, profile error, limits, budget, locks, warnings, volatility, position, size status, blockers, news `NOT_EVALUATED`, authority `NOT_AUTHORIZED`. It uses the same evaluation run as `/evaluation`.
- `POST /api/v1/risk/calculate` (`RiskCalculationRequest`) → `RiskAssessment`. It is the only non-GET route; CORS now allows POST.
- `DecisionEvaluation.risk`; `MasterDecision.riskStatus` and risk blockers.
- New enums: `RiskStatus`, `RiskProfileName`, `RiskLock`, `RiskWarning`.
  - `Blocker` gains `RISK_PROFILE_MISSING`, `RISK_PROFILE_INVALID`, `RISK_LOCKED`, `UNSAFE_SPREAD`, `POSITION_SIZE_UNVERIFIED`.
  - `PositionSizeStatus` gains `SIZED_FROM_USER_SPEC`.
- `InstrumentSpec.platformPipSize` (optional). The contract fields cover every risk model, the request models and `InstrumentSpec`.

## STATES
`NOT_CONFIGURED`, `INVALID_PROFILE`, `UNAVAILABLE`, `LOCKED`, `SIZE_UNVERIFIED`, `WITHIN_LIMITS`, `CLEAR`. Size statuses: `VERIFIED` (catalog spec, none yet), `SIZED_FROM_USER_SPEC`, `POSITION_SIZE_UNVERIFIED`. Setup states are unchanged; the setup step `RISK` stays NOT_EVALUATED in the setup engine and points to the evaluation.

## INVALIDATION
- A lock on a confirmed plan vetoes it for the decision (outcome `NO_TRADE`). The setup itself keeps following the Phase 8 rules (it can still be invalidated, expire or be missed).
- A stale account state (another trading day) locks until the file is updated. The file is re-read when it changes; no restart is needed.
- A conversion rate older than 24 h, or dated in the future, is ignored.

## ERROR STATES
| Condition | Result |
|---|---|
| `RISK_PROFILE_PATH` unset / file missing | `NOT_CONFIGURED`, blocker `RISK_PROFILE_MISSING` |
| Unreadable JSON, schema error, preset with limits, CUSTOM missing a limit, unknown symbol, spec keyed to another symbol | `INVALID_PROFILE`; the error lists field paths and messages only, never values |
| CUSTOM above a hard limit | `INVALID_PROFILE` naming the fields |
| Engine exception | `UNAVAILABLE`, blocker `RISK_GATE_MISSING`; the evaluation and decision continue fail-safe |
| Too few M15 candles | warning `VOLATILITY_UNAVAILABLE` (the volatility lock cannot fire) |
| Calculator: unknown symbol / invalid body / spec for another symbol | 404 / 422 / 422 |
| Web: any inconsistent risk payload (authority claimed, news evaluated, locks vs status, sized risk above budget or limit, unverified with volume, unassessed with values) | rejected; the RISK tab or evaluation shows UNAVAILABLE |

## UNIT TESTS
`tests/unit/test_risk_engine.py` (29):
- XAUUSD sizing maths end to end (320 points, 32 pips, 0.28 volume, 98 USD incl. spread, margin 568.96);
- catalog vs user spec;
- missing, inconsistent and unconverted specs are never guessed;
- direct, inverse, stale and future conversion rates; no chaining;
- floor to step, minimum volume;
- spec consistency (metals, JPY-quoted);
- unsafe and unknown spread; invalid stops; margin and leverage;
- daily, weekly, trades and consecutive-loss locks; open risk and position count; limits that reduce before they lock;
- stale state; prop daily and total drawdown;
- adding to a loser; correlated exposure;
- volatility lock, calm, unavailable, not checked;
- account-only CLEAR; missing state;
- CUSTOM within and above hard limits; preset edits rejected;
- presets match the spec and cannot exceed hard limits;
- profile file validation; the committed example profile validates.

`tests/unit/test_risk_properties.py` (80, 40 seeds each):
- sized risk ≤ effective budget ≤ every cap; risk % ≤ the limit;
- volume is a whole number of steps, and one more step would exceed the budget;
- a further loss never increases the budget or volume and never clears an account lock.

`tests/unit/test_scoring_engine.py` (+3):
- the risk gate replaces the missing risk gate;
- a risk lock vetoes a confirmed plan (NO_TRADE, RISK_RR 0);
- a missing profile keeps the plan pending with `RISK_PROFILE_MISSING`.

## INTEGRATION TESTS
`tests/integration/test_risk_api.py` (26):
- profile store: missing / invalid / reload, and no values echoed;
- no profile → decision blocked by `RISK_PROFILE_MISSING`, verdict WAIT;
- valid profile → CLEAR, M15 volatility, missing gates only NEWS, user spec removes `INSTRUMENT_SPEC_MISSING`;
- locked, stale and invalid profiles reach the decision;
- calculator: sizing, stores nothing, EUR without and with a rate, no spec;
- 404 and 422 cases;
- CORS POST;
- risk failure → UNAVAILABLE;
- **a risk-vetoed A+ confirmed evaluation leaves the verdict WAIT with no direction or prices**;
- wire-field contract for 12 models;
- validation errors never echo submitted values.

Updated: the read-only API test allows only `POST /api/v1/risk/calculate`; the synthetic evaluation now reports the risk gate; the version is 0.9.0-phase9.

Web `tests/risk.test.tsx` (35):
- risk payload rules; evaluation–risk consistency;
- calculator form building and validation; POST client;
- RISK tab: live assessment, setup instructions, locks, unavailable;
- `#risk` deep link;
- the calculator stores nothing in the browser (`localStorage.setItem` never called);
- status bar risk status.

Shared-types: 16 new contract checks.

## UI DISPLAY
- **RISK tab** (enabled):
  - banner "NOT AUTHORIZED · risk has final veto · news gate missing (Phase 13)";
  - live assessment: status, profile and currency, size status, news; setup instructions when not configured; profile error; locks (final veto); budget table (per trade, daily/weekly left, open risk, prop, effective); position size (not authorized): distance · points · pips, spread, volume and step, risk at stop with % and without spread, margin; volatility ratio; warnings; blockers;
  - **What-if calculator:** direction, preset, currencies, entry, stop, balance, leverage, contract spec fields (empty, never pre-filled), spread, pip size, conversion rate. The result is shown with the same view; nothing is saved.
- **Left nav:** "Risk" is enabled and opens the RISK tab (`#risk`).
- **Status bar:** Risk shows `decision.riskStatus` (for example `NOT_CONFIGURED`).
- **OVERVIEW:** blockers now include risk blockers.

## KNOWN LIMITATIONS
- ⚠️ **Manual state:** locks are only as good as the numbers in the profile file. A stale trading day locks, but wrong numbers typed today are trusted. The journal and paper trading (Phases 15–16) are needed for automatic P/L, counts and positions.
- ⚠️ **Presets, hard limits, the volatility ratio and the spread share are uncalibrated research defaults, not advice.**
- **Budget rules:**
  - daily and weekly limits count realized losses plus open risk (remaining risk to stop), not unrealized equity swings;
  - prop rules are a simplified balance-based model; real prop firms differ (equity-based, trailing drawdown).
- **Sizing limits:**
  - the spec consistency rule assumes linear, quote-currency-denominated contracts, which is right for spot FX and metals CFDs. Other contract types would read as inconsistent and stay unverified (fail-safe);
  - margin uses a flat leverage and the balance, not free margin or broker tiers;
  - spread is the typical spread only; there is no slippage or commission model.
- **Correlation** is a same-sign currency overlap warning, not a statistical correlation.
- **Privacy:** the balance is readable by anyone who can reach the local API (no auth yet, V1 item). The file never leaves the machine and is git-ignored.
- **Volatility** uses the setup run's M15 candles; when the setup data is ineligible the lock may be unavailable.
- **The confirmed-plan sizing path is proven by unit, property and hand-built integration tests only**; fixture data has not produced a confirmed plan yet (see Phase 8).

## STRATEGY VERSION
`0.9.0-phase9` (phase 9, "Risk Calculator"). Engines: + `risk`. Verdict authority: **FAIL_SAFE_ONLY**.
