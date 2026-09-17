# Phase 13 — Economic Calendar

## GOAL
Build the economic calendar and the news gate (spec STEP 7 event model and news states; STEP 8 hard blocker "strict news blackout"): for each market, decide from the relevant calendar events whether news is CLEAR, CAUTION, BLACKOUT, POST_NEWS_WAIT or NORMALIZED — or UNAVAILABLE when the calendar cannot prove it. Feed the gate to the evaluation, the Master Decision, risk, alerts, the ready watch, the assistant and the UI.

**Every required gate now exists** (risk, Phase 9; news, Phase 13). **Verdict authority stays `FAIL_SAFE_ONLY`**:
- the decision gate still has no path that emits LONG/SHORT;
- a confirmed plan with every gate clear gets the new outcome `CONFIRMED_AWAITING_AUTHORITY`, never a direction or prices.

Switching authority is left to an explicit user decision (see KNOWN LIMITATIONS).

## SCOPE BOUNDARY
- **Macro interpretation** (Phase 14): surprise is reported as a number only (actual − forecast, %), with no bullish/bearish meaning. DXY/yields/sentiment are not built.
- **No live calendar vendor and no scraping.** Providers are:
  - `unconfigured` (default; blocks);
  - `file` (a server-side JSON calendar the user maintains from a licensed source);
  - `fixture` (a synthetic schedule for development that can never clear the gate).
- **No Wick** `NEWS_DRIVEN_NO_WICK` and the No Wick NEWS context component are still not assigned (the No Wick pipeline does not consume the calendar yet).
- **Geopolitical / unscheduled news** is not modelled.

## INPUTS
- **Calendar snapshot** from the provider: source, `fetchedAt`, `coverageStart`, `coverageEnd`, and events in the spec `EconomicEvent` model:
  - id, country, currency, name, `scheduledTime` (UTC), importance LOW/MEDIUM/HIGH/EXTREME;
  - actual / forecast / previous / revisedPrevious;
  - optional status (UPCOMING/IMMINENT/RELEASED/REVISED/COMPLETED/CANCELLED/DELAYED).
- **Instrument catalog** for relevant currencies.
- **Config (new):** `packages/strategy-spec/news.json`:
  - relevant currencies: METAL → quote (USD); FX → base and quote;
  - windows per importance: EXTREME blackout 30/30 min, post wait 15, caution lead 120; HIGH 15/15, post 10, caution 60; MEDIUM caution ±15;
  - normalized 30 min, imminent 15 min, delayed max 120 min;
  - calendar max age 24 h, required look-ahead 24 h, list window −6 h / +24 h, countdown thresholds 60 and 15 min.
- **Settings:** `CALENDAR_PROVIDER` (`unconfigured` | `fixture` | `file`), `CALENDAR_FILE_PATH`. Template: `config/calendar.example.json`.

## RULES
1. **Relevant events:** the market's currencies only. LOW importance never gates. CANCELLED events are ignored.
2. **Windows per event** at time T:

   | State | Window |
   |---|---|
   | BLACKOUT | [T − before, T + after); DELAYED events: until T + 120 min |
   | POST_NEWS_WAIT | [blackout end, + post wait) |
   | NORMALIZED | [post end, + 30 min) |
   | CAUTION | [T − caution lead, blackout start); MEDIUM events (no blackout): [T − 15, T + 15) |

   Overall state = the most severe across events: BLACKOUT > POST_NEWS_WAIT > CAUTION > NORMALIZED > CLEAR. The active event and its window are reported.
3. **Calendar trust:** the state is **UNAVAILABLE** (blocker `NEWS_DATA_UNAVAILABLE`) when:
   - no snapshot or provider error;
   - an invalid file (field paths only in the reason, never values);
   - fetched more than 24 h ago, or in the future;
   - coverage ends before now + 24 h;
   - coverage starts too late to evaluate recent post-news windows.

   A synthetic calendar adds `NEWS_DATA_SYNTHETIC`, so it can never clear the gate.
4. **Blockers:** BLACKOUT → `NEWS_BLACKOUT` (strict hard blocker); POST_NEWS_WAIT → `NEWS_POST_WAIT`; UNAVAILABLE → `NEWS_DATA_UNAVAILABLE`. CAUTION is a warning.
5. **Event view:**
   - derived status: UPCOMING, IMMINENT (≤ 15 min before), RELEASED (actual present or within its windows), COMPLETED; provider CANCELLED/DELAYED/REVISED are kept;
   - surprise = actual − forecast when both parse as numbers (%, K/M/B/T handled), plus surprise %;
   - blackout start/end and minutes to event.
6. **Evaluation:**
   - `missingGates` lists `NEWS_GATE_MISSING` only when the news gate is not wired;
   - news blockers join the hard blockers;
   - `BEFORE_MAJOR_NEWS` early-entry warning during CAUTION/BLACKOUT;
   - outcome for a confirmed plan: **NO_TRADE** under a risk lock or a strict news BLACKOUT (final veto); **CONFIRMED_AWAITING_AUTHORITY** when there are no missing gates and no hard blockers; otherwise **CONFIRMED_PENDING_GATES**;
   - confidence stays capped at MODERATE while authority is FAIL_SAFE_ONLY.
7. **Master Decision:** `newsState` (state, currencies, active and next event, window end, calendar available/synthetic) plus news blockers.
   - It is set from the evaluation, and directly from the news service **even when market data is unusable** (the calendar does not depend on market data), so every surface shows one news state.
   - The verdict is never changed; `enforce_verdict_authority` runs afterwards.
8. **Risk:** `risk.news` reports the news state; `NEWS_NOT_EVALUATED` is warned only when news is UNAVAILABLE. The news veto itself is applied by the evaluation (not duplicated as a risk lock).
9. **Alerts:**
   - `NEWS_BLACKOUT` (RISK/HIGH) when the state enters BLACKOUT;
   - `NEWS_CLEARED` when BLACKOUT/POST_NEWS_WAIT ends;
   - `NEWS_COUNTDOWN` (RISK/MEDIUM) when the next HIGH/EXTREME event crosses 60 or 15 min (only the nearest threshold is announced if first seen late; a baseline records crossed thresholds silently).

   News alerts are calendar-driven and fire even while market data is unusable.
10. **Ready watch:** NEWS_GATE is MET only for CLEAR / NORMALIZED / CAUTION without news blockers; it is required for GATES_PENDING.
11. **Assistant:** tool `get_news_state`; intent NEWS (news, calendar, CPI, NFP, FOMC, payrolls, high-impact, blackout); glossary term NEWS_GATE. MACRO questions still return UNAVAILABLE (Phase 14).

## OUTPUTS
- `GET /api/v1/news/{symbol}` → `NewsAssessment` (state, relevant currencies, active/next event, window, events −6 h…+24 h, calendar info, blockers, warnings).
- `GET /api/v1/calendar?hoursBefore&hoursAfter&currency&minImportance` → `CalendarResponse`.
- `MasterDecision.newsState`, `DecisionEvaluation.news`, `RiskAssessment.news` = news state.
- Enums: `NewsState`, `EventImportance`, `EventStatus`.
  - `Blocker` + NEWS_BLACKOUT, NEWS_POST_WAIT, NEWS_DATA_UNAVAILABLE, NEWS_DATA_SYNTHETIC;
  - `EvaluationOutcome` + CONFIRMED_AWAITING_AUTHORITY;
  - `AlertType` + NEWS_COUNTDOWN, NEWS_BLACKOUT, NEWS_CLEARED;
  - `AssistantIntent` + NEWS.
- Contract fields: `EconomicEvent`, `CalendarFile`, `EventView`, `CalendarInfo`, `NewsAssessment`, `CalendarResponse`; updated `MasterDecision`, `DecisionEvaluation`.

## STATES
- News: CLEAR / CAUTION / BLACKOUT / POST_NEWS_WAIT / NORMALIZED / UNAVAILABLE.
- Event status: UPCOMING / IMMINENT / RELEASED / REVISED / COMPLETED / CANCELLED / DELAYED.
- Evaluation outcome adds CONFIRMED_AWAITING_AUTHORITY.

## INVALIDATION
- The news state is recomputed from the calendar at every request. An edited calendar file is re-read when it changes.
- A calendar older than 24 h, or not covering the next 24 h, stops proving clear, so the state becomes UNAVAILABLE.
- DELAYED events keep the blackout for up to 120 min after the scheduled time.

## ERROR STATES
| Condition | Result |
|---|---|
| `CALENDAR_PROVIDER=unconfigured` (default) | UNAVAILABLE, `NEWS_DATA_UNAVAILABLE` on every decision |
| File missing / invalid / stale / short coverage / future fetch time | UNAVAILABLE with the reason (field paths only) |
| Provider exception | UNAVAILABLE ("calendar provider failed") |
| Synthetic calendar | states computed for display, `NEWS_DATA_SYNTHETIC` blocks |
| Unknown symbol; invalid `minImportance` / `currency` / hours | 404; 422 |
| Web: BLACKOUT/POST/UNAVAILABLE without its blocker, availability mismatch, unflagged synthetic calendar, events for unrelated currencies, news for another market; evaluation outcome inconsistent with gates | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_news_engine.py` (23):
- 13 window cases across importance levels;
- most severe event wins, and blockers;
- relevant currencies (gold USD only, FX base+quote);
- cancelled and delayed events;
- 4 calendar-trust failures plus a missing provider;
- a synthetic calendar never clears;
- event views: statuses, surprise parsing, list window, next gating event;
- calendar file validation; providers (unconfigured, file, fixture, factory); config guard; the committed example calendar validates.

`tests/unit/test_scoring_engine.py` (+2):
- every gate clear → CONFIRMED_AWAITING_AUTHORITY (still NOT_AUTHORIZED, confidence capped);
- BLACKOUT → NO_TRADE with `BEFORE_MAJOR_NEWS`; CAUTION warns; POST_NEWS_WAIT / UNAVAILABLE / synthetic → CONFIRMED_PENDING_GATES.

`tests/unit/test_alert_rules.py` (+3):
- ready watch needs a clear news gate;
- blackout, cleared and countdown alerts, including on untrusted market data, without repeats;
- a late first sighting announces only the nearest threshold; MEDIUM events get no countdown.

## INTEGRATION TESTS
`tests/integration/test_news_api.py` (14):
- unconfigured calendar blocks the decision (newsState, evaluation, risk.news);
- a HIGH USD event 5 min away → BLACKOUT through news, decision, evaluation, risk and the assistant; EUR events don't gate gold but do gate EURUSD;
- a clear calendar clears the gate (verdict still WAIT); fixture calendar synthetic plus calendar filters;
- invalid file UNAVAILABLE without echoing values;
- **an awaiting-authority evaluation never changes the verdict or publishes prices**;
- **unusable market data still carries the news state** (one state everywhere);
- contract fields.

Updated: evaluation/risk API expectations (no missing gates), gate and setup wording, assistant intent test.

Web `tests/news.test.tsx` (20): news trust rules; decision news label; status bar; chart markers only for HIGH/EXTREME events inside the candle range; MACRO tab news calendar (state, next event, surprise) and unavailable reason; evaluation outcome rules with the news gate (3 accepted, 7 rejected). Updated: status bar test (news not assumed clear), risk news validation, evaluation fixtures.

## UI DISPLAY
- **Status bar News:** `BLACKOUT · HIGH USD in 4m`, `CLEAR · …`, `UNAVAILABLE` (never assumed clear), `· synthetic` when applicable.
- **MACRO tab** (enabled; macro itself still Phase 14):
  - news state (red with blockers), relevant currencies, window with active event, next event countdown;
  - calendar provider / fetch / coverage or reason; blockers and warnings;
  - events table: time, importance, event, status, actual / forecast / previous, surprise (%);
  - a note that surprise meaning is the macro engine's job.
- **Chart "News" toggle** (on by default): markers for HIGH (amber) and EXTREME (red) events inside the drawn candles, snapped to the containing candle.
- **RISK tab** shows the news state; alerts and assistant display the new news items.

## KNOWN LIMITATIONS
- ⚠️ **Verdict authority:**
  - all required gates exist, but switching `verdictAuthority` to FULL is **not** done and requires an explicit decision;
  - it also needs building: a directional verdict path in the decision gate from CONFIRMED_AWAITING_AUTHORITY, publishing entry/stop/targets/size into the decision, client acceptance, and tests;
  - the evidence base is still uncalibrated fixture data with only XAUUSD validated, and no real market-data vendor.
- ⚠️ **No real calendar feed:** the file provider depends on the user keeping a licensed calendar current (fetched < 24 h, covering the next 24 h). Otherwise every decision is blocked by `NEWS_DATA_UNAVAILABLE` (fail safe).
- **Uncalibrated research defaults:** windows, caution leads and delayed handling. Unscheduled or geopolitical news is invisible.
- **Surprise is numeric only;** units are the calendar's. No revision tracking beyond the `revisedPrevious` field.
- Countdown alerts depend on the monitor cadence (new M5 bar or 5 min), so a threshold can be announced up to ~5 min late.
- No Wick does not yet use the calendar (`NEWS_DRIVEN_NO_WICK` not assigned).

## STRATEGY VERSION
`0.13.0-phase13` (phase 13, "Economic Calendar"). Engines: + `news`. Verdict authority: **FAIL_SAFE_ONLY** (all gates exist; authority switch is an explicit decision).
