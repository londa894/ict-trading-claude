# Phase 19 — Replay

## GOAL
Build replay (spec STEP 10 replay): Manual, Guided, Blind and Quiz modes that **hide the future**, using the **same strategy code** as live ("same strategy code for LIVE / PAPER / REPLAY / BACKTEST").

A replay session is practice on closed history with `authority: EDUCATION_ONLY`. It never authorizes, forecasts or signals anything. Verdict authority stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **In memory only.** Sessions are not persisted: at most 20, idle expiry 6 h, lost on restart. No database table, no store setting.
- **Not built:**
  - autoplay or timers;
  - drawing tools and trade entry inside replay (paper trading stays in Phase 16);
  - replay of the news or macro state as of the cursor;
  - multi-timeframe replay in one session;
  - saved quiz history or leaderboards;
  - AI tutoring (the guided narrative is deterministic text built from the engine output).
- **No new gates and no verdict path.** Replay shows the setup analysis as of the cursor. It never shows a decision that could be read as a live verdict.
- Later phases (STEP 15, Phase 20+) are not started.

## INPUTS
- **Create** (`POST /api/v1/replay/sessions`):
  - symbol;
  - mode MANUAL / GUIDED / BLIND / QUIZ (default MANUAL);
  - timeframe M5 / M15 / H1 (default M15);
  - `start` (UTC-aware, must be in the past).
- **Step:** `bars` between −96 and 96, non-zero.
- **Answer:** `questionId` and `answer` (one of the question's options, including SKIP).
- **History:** closed candles through the CandleService (validated, normalized), filtered to `close_time ≤ cursor`.
- **Engine:** `SetupService.run(symbol, now = cursor)` (the live path) and the session clock at the cursor.
- **Config (new):** `packages/strategy-spec/replay.json`:
  - timeframes, window 300 bars, max step 96 bars;
  - quiz horizon 6 bars, level ATR period 14;
  - max 20 sessions, idle TTL 6 h;
  - blind price scale 0.6–1.4 and a whole-week date shift of 520–1560 weeks.

## RULES
1. **Cursor:**
   - the cursor is always the close time of a closed candle;
   - create snaps the start to the last closed candle at or before it, capped by the data and the clock;
   - stepping walks closed candles (weekend and daily-break gaps are skipped) and stops at the newest closed bar (`atEnd`).
2. **Hide the future.** Every response is computed as of the cursor:
   - candles (≤ 300) have `close_time ≤ cursor`;
   - the setup analysis runs with `now = cursor`;
   - guided events are only those that closed between the previous and the current cursor.
   - Tested: replay candles equal the live history truncated at the cursor.
3. **MANUAL:** step forward and back; the engine view shows eligibility with its reasons, setup state, current setup (plan levels when present) and New York time, sessions and time quality.
4. **GUIDED:** MANUAL plus a tutor narrative (eligibility, setup progress, time quality) and the setup events since the last step.
5. **BLIND:**
   - the label is "Hidden instrument";
   - prices are multiplied by a random scale;
   - dates are shifted by a random whole number of weeks, so weekday and time of day are kept;
   - no engine view or guidance; forward only (stepping back → 422, because the future was already shown).
   - `end` returns the reveal (symbol, real start and cursor, scale, week shift); after that the session shows the real symbol and the engine view and cannot step.
6. **QUIZ:**
   - every question is built only from known bars. Types rotate NEXT_BARS_DIRECTION → LEVEL_FIRST → SETUP_PROGRESS, and a type is skipped when it does not apply (LEVEL_FIRST needs an ATR, SETUP_PROGRESS needs an open setup);
   - the session moves only by answering (step → 422);
   - answering advances the cursor by the horizon (6 bars) and grades against the revealed bars only;
   - DIRECTION: close after the horizon versus the reference close (unchanged or too few bars → VOID);
   - LEVEL_FIRST: which of ±1 ATR is touched first (both in one bar → VOID; neither → NEITHER);
   - SETUP_PROGRESS: the setup state after the horizon (CONFIRMED_PLAN / INVALIDATED_OR_EXPIRED / STILL_OPEN; unknown → VOID);
   - SKIP → VOID, with the correct answer still shown;
   - score: asked / correct / incorrect / void, accuracy over graded answers, and the analytics sample-size label;
   - the engine view appears only after the first answer.
7. **Limits:** 20 sessions (then 422), idle sessions expire, and an ended session cannot step or answer.
8. **Web trust rules:** the client independently rejects:
   - authority other than EDUCATION_ONLY;
   - unknown mode or timeframe;
   - open candles, or any candle whose bucket ends after the cursor;
   - masking on a non-blind session, or a blind session that shows the symbol, analysis or guidance;
   - a quiz engine view before the first answer;
   - a score that does not match the history, or a last result that is not the last history item;
   - quiz data on non-quiz sessions.

## OUTPUTS
- `GET /api/v1/replay/sessions` → `ReplayStatus` (storage IN_MEMORY, limits, `ReplaySessionRow[]`).
- `POST /api/v1/replay/sessions` → 201 `ReplayState`; `GET /api/v1/replay/sessions/{id}`; `DELETE` → 204.
- `POST .../{id}/step` and `POST .../{id}/answer` → `ReplayState`; `POST .../{id}/end` → `ReplayReveal`.
- `ReplayState` fields:
  - id, mode, timeframe, label, cursor, start;
  - canStepBack, atEnd, ended;
  - candles, analysis (`ReplayAnalysis` with `ReplaySetupView`), guidance (`ReplayGuidance` with `ReplayEvent[]`);
  - quiz (`QuizQuestion`), lastResult, history (`QuizResult[]`), score (`QuizScore`);
  - masked, authority, strategy version.
- Enums: `ReplayMode`, `QuizQuestionType`, `QuizGrade`.
- Contract fields for 14 models: CreateReplayRequest, StepRequest, QuizAnswerRequest, ReplaySetupView, ReplayAnalysis, ReplayEvent, ReplayGuidance, QuizQuestion, QuizResult, QuizScore, ReplayState, ReplayReveal, ReplaySessionRow and ReplayStatus.

## STATES
- Session: active → ended (or expired / deleted). `atEnd` when no newer closed bar exists.
- Quiz grade: CORRECT / INCORRECT / VOID.
- Mode capabilities: MANUAL and GUIDED can step both ways; BLIND and QUIZ are forward only; QUIZ advances only by answers.

## INVALIDATION
- Sessions expire after 6 h idle and on restart.
- A graded quiz result is never changed.
- Nothing from a replay feeds the journal, alerts, analytics or the verdict.

## ERROR STATES
| Condition | Result |
|---|---|
| Start in the future / naive time; timeframe not allowed; step 0 or > 96 | 422 |
| Unknown symbol / session | 404 |
| No closed history at the start | 422 |
| Session limit reached | 422 ("at most 20 replay sessions") |
| Step back in BLIND/QUIZ; step in QUIZ; wrong question id or option; action on an ended session | 422 with the reason |
| History INVALID/DISCONNECTED at the requested range | 422 ("M15 history INVALID: replay is unavailable here"); nothing shown |
| Synthetic data | engine view "not eligible (DATA_SYNTHETIC)"; quiz grading still uses the (synthetic) bars |
| Web: any trust-rule violation | rejected with "Rejected replay: …"; the rejected payload is never rendered (the last verified state, if any, stays with the error above it) |
| Web: API unreachable | "Replay API unreachable" |

## UNIT TESTS
`tests/unit/test_replay_engine.py` (7):
- config and request validation, including a bad blind range in the spec;
- mean true range;
- question rotation using only known data, skipping types that do not apply;
- direction grading (correct, incorrect, unchanged, too few bars, SKIP);
- LEVEL_FIRST grading, including the same-bar ambiguity and ignoring bars beyond the horizon;
- setup-progress grading;
- score with sample label, and masking (scale and whole-week shift keeping the weekday).

## INTEGRATION TESTS
`tests/integration/test_replay_api.py` (5 behaviour tests plus 14 contract cases):
- **manual steps never show the future**: replay candles equal the live history truncated at the cursor, across forward and back steps;
- guided narrative and events limited to the cursor;
- **blind masking**: the symbol is absent from the payload, the scale is consistent, the week shift is whole, stepping back is refused, and the reveal matches the mask, after which the session is unmasked and cannot step;
- **quiz graded only after the cursor moves**: stepping refused, bad id/option refused, grade consistent with the revealed close, engine shown after the first answer, rotation, SKIP → VOID;
- errors, session limit, listing, delete, and at-end capped by the data and clock.

Updated: API surface test (replay POST/DELETE allowlist), shared contract and enums, left-nav test, version strings.

Web `tests/replay.test.tsx` (18):
- trust rules: 4 accepted, 10 rejected;
- request building;
- route calls, error details and a rejected lying response;
- `#replay` hash;
- manual start, engine view and stepping;
- quiz hides stepping, then answer → result and score;
- blind hides the symbol and back-stepping, and reveals on end;
- a rejected response shows the reason and no session.

## UI DISPLAY
- **Left nav Replay** (`#replay`) opens the Replay view:
  - education note;
  - form: market, mode (with a one-line description of each), timeframe, start (local time).
- **Session:**
  - header: label · mode · timeframe · cursor (plus "dates shifted" when masked) · EDUCATION_ONLY;
  - candle chart (Lightweight Charts, render only);
  - controls: ◀ 1 bar (only when allowed); 1 / 4 / 12 bars ▶ (not in QUIZ); End session (and reveal for BLIND); an end-of-history note.
- **Quiz card:** question and option buttons; last result (grade, your answer, correct answer, detail); score with sample-size badge.
- **Engine at cursor:** eligibility and reasons, setup state, current setup and plan levels; New York time, sessions and time quality.
- **Guided:** narrative list. **Blind:** the hidden-information note; after ending, the reveal line (symbol, real times, scale, week shift).

## KNOWN LIMITATIONS
- ⚠️ **Synthetic fixture data.** The engine view is always "not eligible (DATA_SYNTHETIC)" and the fixture history has no confirmed plans. SETUP_PROGRESS questions and plan levels therefore appear only when an open setup exists (on the fixture, an ineligible analysis can still report a setup state). Quiz grades on synthetic bars measure nothing about real markets.
- **Blind is not fully blind:** the user chose the market in the form, and price behaviour (volatility, the daily break) can hint at the instrument. Masking hides labels, levels and dates only.
- The LEVEL_FIRST prompt shows the ATR levels unrounded (e.g. 2029.973571).
- The guided narrative prints the full ISO New York time.
- In-memory sessions: lost on restart; not shared across API workers (single-process deployment assumed).
- The news and macro state as of the cursor are not replayed (their providers have no point-in-time history API yet).
- Each step reruns the setup analysis (about 0.25 s on the fixture feed); large steps are bounded to 96 bars.
- No authentication (inherited).

## STRATEGY VERSION
`0.19.0-phase19` (phase 19, "Replay"). Engines: + `replay`. Verdict authority: **FAIL_SAFE_ONLY**.
