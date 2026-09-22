# BUILD STATUS

| Phase | Name | Status | Strategy version | Date |
|---|---|---|---|---|
| 0 | Repository / Foundation / Data Architecture | ✅ PASSED (gate complete) | 0.0.0-phase0 | 2026-09-13 |
| 1 | XAUUSD Chart Shell | ✅ PASSED (gate complete) | 0.1.0-phase1 | 2026-09-13 |
| 2 | Market Structure | ✅ PASSED (gate complete) | 0.2.0-phase2 | 2026-09-13 |
| 3 | Liquidity | ✅ PASSED (gate complete) | 0.3.0-phase3 | 2026-09-13 |
| 4 | Displacement + FVG/IFVG | ✅ PASSED (gate complete) | 0.4.0-phase4 | 2026-09-13 |
| 5 | No Wick Architecture V1 | ✅ PASSED (gate complete) | 0.5.0-phase5 | 2026-09-13 |
| 6 | Session & Time | ✅ PASSED (gate complete) | 0.6.0-phase6 | 2026-09-13 |
| 7 | Core Setup State Machine | ✅ PASSED (gate complete) | 0.7.0-phase7 | 2026-09-13 |
| 8 | Entry & Verdict | ✅ PASSED (gate complete; authority still FAIL_SAFE_ONLY) | 0.8.0-phase8 | 2026-09-13 |
| 9 | Risk Calculator | ✅ PASSED (gate complete; authority still FAIL_SAFE_ONLY) | 0.9.0-phase9 | 2026-09-14 |
| 10 | Watchlist & Scanner | ✅ PASSED (gate complete; authority still FAIL_SAFE_ONLY) | 0.10.0-phase10 | 2026-09-14 |
| 11 | Alerts V1 | ✅ PASSED (gate complete; authority still FAIL_SAFE_ONLY) | 0.11.0-phase11 | 2026-09-14 |
| 12 | AI Assistant V1 | ✅ PASSED (gate complete; authority still FAIL_SAFE_ONLY) | 0.12.0-phase12 | 2026-09-14 |
| 13 | Economic Calendar | ✅ PASSED (gate complete; all required gates exist; authority still FAIL_SAFE_ONLY) | 0.13.0-phase13 | 2026-09-14 |
| 14 | Basic Macro | ✅ PASSED (gate complete; macro is context, not a gate; authority still FAIL_SAFE_ONLY) | 0.14.0-phase14 | 2026-09-14 |
| 15 | Journal V1 | ✅ PASSED (gate complete; records only, never authorizes; authority still FAIL_SAFE_ONLY) | 0.15.0-phase15 | 2026-09-14 |
| 16 | Paper Trading | ✅ PASSED (gate complete; simulation only, never authorizes; authority still FAIL_SAFE_ONLY) | 0.16.0-phase16 | 2026-09-14 |
| 17 | Analytics V1 | ✅ PASSED (gate complete; descriptive only; authority still FAIL_SAFE_ONLY) | 0.17.0-phase17 | 2026-09-14 |
| 18 | Backtesting | ✅ PASSED (gate complete; research only; authority still FAIL_SAFE_ONLY) | 0.18.0-phase18 | 2026-09-14 |
| 19 | Replay | ✅ PASSED (gate complete; education only; authority still FAIL_SAFE_ONLY) | 0.19.0-phase19 | 2026-09-14 |
| 20+ | (see master spec STEP 15) | ⏸ NOT STARTED — awaiting approval | — | — |

Verdict authority: **FAIL_SAFE_ONLY**. The system can only emit WAIT or UNAVAILABLE. Every required gate (risk Phase 9, news Phase 13) now exists; macro (Phase 14) is context only; the journal (Phase 15) records and paper trading (Phase 16) simulates and analytics (Phase 17) describes and backtesting (Phase 18) replays and replay (Phase 19) teaches; none authorizes. Switching to FULL is an explicit decision that also needs a directional verdict path (not built).

---

## Phase 19 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass (first run failed on E501 lines in the new replay tests; reformatted, then pass) |
| mypy `--strict` (145 files) | ✅ pass |
| pytest (unit + integration) | ✅ 1122 passed (Phase 18: 1096) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 420, shared-types 251 | ✅ 671 passed (Phase 18: 636) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (live API, fixture feed, in-memory sessions) | ✅ MANUAL: 300 candles to the cursor, step +8 / −2, engine view DATA_SYNTHETIC; GUIDED: narrative after a step; BLIND: "Hidden instrument", dates shifted to 2040 (whole weeks), symbol absent, back-step 422, forward 4 bars, reveal (XAUUSD, scale 1.2231, 842 weeks); QUIZ: no engine view before the answer, step 422, three answers graded (CORRECT / INCORRECT / INCORRECT, rotation DIRECTION → LEVEL_FIRST → DIRECTION with no open setup), score 1/3 INSUFFICIENT; listing IN_MEMORY 4 sessions |
| Browser check (1600×900, live servers) | ✅ left-nav Replay (current) opens `#replay`; a start past the fixture history snaps to its last closed bar (2024-04-19 21:00Z); QUIZ: chart rendered, only End session in the controls, no engine view until answered, then result "INCORRECT · answer DOWN", score "0/1 (0%) INSUFFICIENT", engine view shown and Q2 LEVEL_FIRST; BLIND: no XAUUSD anywhere in the session, no back button, 4-bar step across the daily break, End and reveal shows XAUUSD / 659 weeks and the unmasked header with the engine view; no horizontal overflow |

### Acceptance criteria
1. ✅ Manual, Guided, Blind and Quiz modes.
2. ✅ Hide future: every view is computed as of the cursor with the live strategy code (tested against live history truncated at the cursor); the web client independently rejects candles past the cursor.
3. ✅ Blind hides the instrument, levels, dates and engine view until reveal, and cannot step back.
4. ✅ Quiz questions use known data only and are graded only after the bars are revealed; the score carries sample-size labels.
5. ✅ Education only: EDUCATION_ONLY authority; nothing feeds the verdict, journal, alerts or analytics.
6. ✅ `0.19.0-phase19`; all earlier gates pass.

### Scope decisions
- Sessions are in memory (no store/migration). Replay is practice, and persistence was not required by the spec.
- Quiz types are deterministic and gradeable from bars/setup state only (direction, ATR level first, setup progress); no free-form or AI questions.
- News and macro state are not replayed as of the cursor (no point-in-time history for those providers).

### Changes to earlier-phase code
- App wiring (ReplayService), routes, API surface allowlist; shared enums and contract; web nav item (Replay was a Phase 19 placeholder), hash `#replay`, page view.

### Not verified / open items
- ⚠️ Synthetic data only: the engine view is always ineligible, and quiz scores on fixture bars mean nothing about real markets.
- ⚠️ SETUP_PROGRESS questions were exercised by unit tests only; no open setup came up in the smoke quiz sequence.
- Cosmetic: LEVEL_FIRST levels are unrounded in the prompt; the guided narrative shows the full ISO New York time.
- ⚠️ Sessions are lost on restart and not shared across workers; no authentication.

---

## Phase 18 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format (incl. Alembic) | ✅ pass |
| mypy `--strict` (141 files) | ✅ pass |
| pytest (unit + integration, incl. a real replay of the live engine and the Alembic migration test) | ✅ 1096 passed (Phase 17: 1066) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 402, shared-types 234 | ✅ 636 passed (Phase 17: 605) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (live API, fixture feed, SQLite migrated with `alembic upgrade head`) | ✅ 7-day A/B run (STANDARD vs AGGRESSIVE ×2 costs, 3 segments, split): RUNNING → COMPLETED in 239 s, VERIFIED, 460 M15 steps and 1380 M5 bars per variant, 0 plans (all steps ineligible), 9 disclosures incl. SYNTHETIC; second start 409; future end 422; 2-day A/B run shows `ineligibleReasons` DATA_SYNTHETIC 184 and the ineligibility disclosures; assistant BACKTEST answer from the latest run. Probe before building: live engine ≈ 0.25 s per step; 98 samples of the fixture history contained 0 confirmed plans. **Found and fixed:** (1) all steps silently ineligible on synthetic data — the funnel now reports ineligibility reasons and a disclosure explains it; (2) after adding that field, the run stored before it made `GET /api/v1/backtests` fail (500) — the field now has a default and a stored result that no longer parses is reported UNREADABLE instead of failing (test added) |
| Browser check (1600×900, live servers on the smoke DB) | ✅ left-nav Backtest (current); runs table; the earlier 7-day run opens (backward compatible) with RESEARCH_ONLY header, disclosures (SYNTHETIC first) and funnels; a 6-hour run started from the form ran to COMPLETED with the funnel "24 steps (24 ineligible: DATA_SYNTHETIC 24)"; list grew to 3 runs; no overflow |

### Acceptance criteria
1. ✅ Sequential historical processing only, same live strategy code, no lookahead by construction (plans only on their confirmation step; late visibility never traded — tested), closed candles only, shared NY session/DST timing.
2. ✅ Assumed spread/slippage/commission (variant multiplier); intrabar ambiguity disclosed and counted.
3. ✅ A/B variants, out-of-sample split, segment stability, optional seeded Monte Carlo, strategy version + config hash.
4. ✅ Research only: RESEARCH_ONLY, disclosures (gates not applied, not a forecast), sample-size labels, no fitting.
5. ✅ Fail safe: synthetic/ineligible data takes no plans; invalid history fails the run; interrupted runs FAILED; tampered/unreadable results withheld.
6. ✅ `0.18.0-phase18`; all earlier gates pass.

### Scope decisions
- Plans are simulated as limits at the plan entry (same as ENGINE_PLAN paper sims) with one position at a time.
- Risk/news/authority gates are not simulated (no historical account state or calendar) and this is disclosed on every run.
- The assistant can read the latest completed run but never start one.

### Changes to earlier-phase code
- Assistant: `run_backtest` tool available (read-only summary), BACKTEST answer; app wiring, routes, settings, Alembic 0004; web nav/view/hash.

### Not verified / open items
- ⚠️ No confirmed plans exist in the available (synthetic) history, so the fill/result path is verified only by unit tests with a scripted plan source, not by a live run producing trades.
- ⚠️ Performance: ~0.25 s per step (a 14-day A/B run ≈ 10+ minutes); single in-process job.
- ⚠️ No authentication; Postgres not run; tamper evidence only.

---

## Phase 17 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (136 files) | ✅ pass |
| pytest (unit + integration) | ✅ 1066 passed (Phase 16: 1036) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 387, shared-types 218 | ✅ 605 passed (Phase 16: 580) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (in-process API, migrated SQLite, NonSynthetic stub feed: 36 journal trades with a deterministic R mix + 3 NO_TRADE + 3 paper sims) | ✅ JOURNAL 36 LIMITED: W/L/BE 16/15/5, win rate 44.4%, expectancy +0.5R, total +18R, PF 2.4754; drawdown 4.6R recovered after 3 records; durations 25 min; breakdowns name no best (single groups or INSUFFICIENT groups); process BAD_PROCESS_WIN 16 / PROCESS_ERROR 20 with NO_CONFIRMED_PLAN 36, MOVED_STOP 8; decision records NO_TRADE 3; PAPER 3 INSUFFICIENT −0.603R net; assistant answer with LIMITED label and disclaimer. **Found and fixed:** LIQUIDITY_EVENT grouped by the timeframe token ("M15") — now the event type ("SWEEP"); DOL accuracy counted DOL levels behind the entry as "reached" (24/24) — now only DOLs ahead of the entry are evaluated (0 evaluable in the smoke data; test added) |
| Browser check (1600×900, live API on the smoke DB) | ✅ `#analytics` opens the Analytics tab; disclaimer; overview with LIMITED label, exclusions, R metrics, drawdown recovery, DOL, alert usefulness UNAVAILABLE, decision records; equity curve; process; breakdown tables with "best: none (reason)"; switching to Paper sims shows the 3-sim INSUFFICIENT report; no overflow. **Found and fixed:** DOL aligned/against and process with/without figures were shown without their sample sizes — counts and labels added |

### Acceptance criteria
1. ✅ Spec analytics: win rate, average/total R, expectancy, profit factor, max drawdown/recovery, duration, best asset/session/setup/timeframe, No Wick and liquidity performance, DOL accuracy, rule violations.
2. ✅ Spec sample-size labels on every figure; no "best" named on INSUFFICIENT samples.
3. ✅ Descriptive only (no probability/guarantee claims; disclaimer; DESCRIPTIVE_ONLY enforced client-side).
4. ✅ Fail safe: tampered excluded, synthetic opt-in, sources never mixed, unavailable stores reported.
5. ✅ `0.17.0-phase17`; all earlier gates pass.

### Scope decisions
- Alert usefulness reported UNAVAILABLE (alerts not persisted/linked) instead of an invented measure.
- Break-even R counts toward profit factor's positive side (standard definition on raw R).
- Analytics lives in the Journal view.

### Changes to earlier-phase code
- Paper service: `export_sims()`; assistant `query_journal` statistics and JOURNAL answer; app wiring and route.

### Not verified / open items
- ⚠️ All statistics exercised on synthetic / hand-made records only; no real track record exists.
- ⚠️ No authentication; Postgres not run; no caching for large journals.
- Authority switch not done; only XAUUSD validated.

---

## Phase 16 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format (incl. Alembic) | ✅ pass |
| mypy `--strict` (132 files) | ✅ pass |
| pytest (unit + integration, incl. Alembic migration test) | ✅ 1036 passed (Phase 15: 1007) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 374, shared-types 206 | ✅ 580 passed (Phase 15: 550) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (in-process API with a controlled clock over the synthetic fixture feed; SQLite migrated with `alembic upgrade head`) | ✅ 4 sims created PENDING / VERIFIED / SIMULATION_ONLY with detected violations and synthetic flag; ENGINE_PLAN without a plan 422; wrong-side levels 422; after 3 h: XAUUSD MARKET filled at the next bar open + costs (2047.54 + 0.15 + 0.05 = 2047.74) and STOP_HIT → FULL_LOSS −1.015R PROCESS_ERROR; XAUUSD LIMIT short filled at the limit and TARGET_HIT → FULL_WIN +1R; EURUSD far LIMIT EXPIRED; wide sim OPEN then manual close at the last bar (costs applied) → MANUAL_EXIT −0.056R; second close 422; paging, status filter, 404 |
| Browser check (1600×900, live API on the smoke DB) | ✅ `#paper` opens the Journal view on the Paper sims tab; table with results, net R, class, VERIFIED/SYNTHETIC badges; detail with SIMULATION_ONLY header, levels, progress, result line and event timeline; a new MARKET sim started through the form → PENDING, data STALE with "waiting for new closed bars" after a read; cancel through the button → CANCELLED event; no browser storage; no overflow. **Found and fixed:** float noise in stored levels (e.g. 2048.3900000000003); levels are now rounded to 10 decimals at creation (older smoke records keep their stored values) |

### Acceptance criteria
1. ✅ Paper trading uses the same engine capture, violation rules and outcome metrics as the journal (spec: same strategy code for LIVE/PAPER).
2. ✅ Deterministic, sequential, closed-bar simulation with no lookahead; spread/slippage/commission disclosed as assumptions; intrabar ambiguity disclosed and resolved conservatively; gaps handled.
3. ✅ Broker-free: no execution surface, nothing sent anywhere, `SIMULATION_ONLY` enforced server- and client-side; guardrails and API surface test pass.
4. ✅ Fail safe: no store → nothing simulated; withheld data → no advance; stale → waiting; tampered → not advanced; no money P/L without specs.
5. ✅ `0.16.0-phase16`; all earlier gates pass.

### Scope decisions
- Stop and target required for every sim (R always defined); single exit (no scaling/trailing) in V1.
- ENGINE_PLAN is an explicit user action (no autonomous loop starting sims).
- Paper lives in the Journal view (the spec nav has no Paper item).

### Changes to earlier-phase code
- Journal snapshot capture extracted to `services/journal/capture.py` (shared).
- DB models + Alembic `0003_phase16`; settings `PAPER_STORE`, `PAPER_MONITOR_ENABLED`; lifespan runs the paper monitor; routes; API surface test.
- Web: Journal view tabs, `#paper` hash.

### Not verified / open items
- ⚠️ Simulation realism is limited (bar-level, mid-price candles, assumed costs); the fixture feed is synthetic and stale in real time.
- ⚠️ No authentication; tamper evidence only; Postgres path not run.
- ⚠️ During the web work `npx prettier` was run without a local install, so npx fetched prettier from the npm registry once; it only reformatted `JournalView.tsx` (checks pass).
- Authority switch not done; only XAUUSD validated.

---

## Phase 15 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (126 files) | ✅ pass |
| pytest (unit + integration, incl. Alembic migration test) | ✅ 1007 passed (Phase 14: 950) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 356, shared-types 194 | ✅ 550 passed (Phase 14: 509) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture market data + SQLite journal created with `alembic upgrade head`) | ✅ status available (database:sqlite); TRADE entry 201 OPEN, snapshot VERIFIED / PRE_ENTRY, detected TRADED_ON_UNAVAILABLE_DECISION + NO_CONFIRMED_PLAN (synthetic data); outcome MANUAL_EXIT +1.5R BAD_PROCESS_WIN with a self-reported violation; corrected revision 2 FULL_WIN +3R, efficiencies 0.889/0.944, revision 1 kept; NO_TRADE entry CLOSED; keyset paging and symbol filter; contradicting MFE 422 without the value; wrong-side stop 422 without the value; export; 404/422 ids; assistant JOURNAL answer without fill prices; delete 204 |
| Browser check (1600×900) | ✅ left-nav Journal (current page); entries table; detail with VERIFIED/PRE_ENTRY/SYNTHETIC snapshot, engine verdict (NOT_AUTHORIZED), fill, detected violations, latest outcome with MFE/MAE and kept revision; outcome form; NO_TRADE recorded through the form and shown first; no browser storage; no horizontal overflow (view 1053/1053, table 1044/1044). **Found and fixed:** candle extremes on data ending before the trade said only "no candles cover the trade"; the reason now names when the data ends (test added) |

### Acceptance criteria
1. ✅ Records every setup decision / manual trade with asset, time/day/session, direction, setup type, timeframe, HTF bias, DOL, liquidity, structure, displacement, PD array, No Wick, macro/news, plan and fill, size, risk %, R:R, strategy version (premium/discount not computed by any engine yet).
2. ✅ Immutable pre-trade snapshot (hashed, verified on read, no update path; tampering detected in a test).
3. ✅ Spec result states, R, MFE, MAE, entry/exit efficiency, duration; VALID_LOSS / PROCESS_ERROR / VALID_WIN / BAD_PROCESS_WIN with detected + reported violations.
4. ✅ Private by default: nothing saved unless configured; export and user deletion; no values echoed; web stores nothing; AI withholds fill prices unless sharing is enabled.
5. ✅ Broker-free: manual fills only; no execution surface (API surface test updated; guardrails pass).
6. ✅ `0.15.0-phase15`; all earlier gates pass.

### Scope decisions
- Store off by default (`JOURNAL_STORE=unconfigured`) rather than silently writing to a database that may not exist.
- Engine authorization recorded as information, not a violation (every trade is NOT_AUTHORIZED under FAIL_SAFE_ONLY).
- Outcomes are append-only revisions instead of edits; analytics left to Phase 17.

### Changes to earlier-phase code
- DB models + Alembic `0002_phase15`; settings `JOURNAL_STORE`; app state/wiring; routes.
- Assistant: `query_journal` available, JOURNAL intent, score-history unknown text; context takes the journal service.
- Web: Journal nav/view, `#journal` hash, `CenterView` + JOURNAL; API surface test allows journal POST/DELETE.

### Not verified / open items
- ⚠️ No authentication: anyone reaching the API can read/delete the journal (keep local).
- ⚠️ Hash is tamper evidence, not tamper proof (no signing key).
- ⚠️ Postgres path not run (SQLite verified, including the migration); Docker/CI still not run.
- Candle MFE/MAE only from the configured provider (synthetic fixture here); rule tolerances uncalibrated; authority switch not done.

---

## Phase 14 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (121 files) | ✅ pass |
| pytest (unit + integration) | ✅ 950 passed (Phase 13: 899) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 335, shared-types 174 | ✅ 509 passed (Phase 13: 480) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture market data + scratchpad macro file, 60 weekday observations) | ✅ XAUUSD BULLISH 0.625 (DXY DOWN +3, real yield DOWN +3, US2Y UP −1, VIX FLAT 0); `direction=BEARISH` → STRONG_CONFLICT; EURUSD BULLISH 0.2, USDJPY BEARISH −0.75, USDCAD BEARISH −0.2, AUDUSD BULLISH 0.4; series endpoint 4 series; BTCUSD 404, `direction=LONG` 422; decision macroState CONTEXT_ONLY with no macro blockers; correlation UNAVAILABLE (fixture candles are 2024, series 2026: no matching days, as expected); assistant MACRO answer |
| Browser check (1600×900) | ✅ status bar Macro BULLISH; MACRO tab macro section (bias/score, correlation, data source, drivers table) above the news gate; no horizontal overflow (tab body 321/321, header 1600/1600). **Found and fixed:** on unusable market data the evaluation still compared macro with the untrusted setup's direction, so the assistant said "versus the open BULLISH setup it is STRONGLY_SUPPORTIVE" while the decision had no state. Direction is now passed only for eligible data; re-verified evaluation, decision and assistant agree (test added) |

### Acceptance criteria
1. ✅ Independent macro providers (unconfigured / file / synthetic fixture) for DXY, US 2Y, US 10Y, real yields and VIX; no scraping; field-path-only errors.
2. ✅ Spec macro states STRONGLY_SUPPORTIVE / SUPPORTIVE / NEUTRAL / CONFLICT / STRONG_CONFLICT / UNAVAILABLE from configured per-market drivers, with fallback series, actual-vs-forecast USD surprise and a DXY correlation regime.
3. ✅ Macro modifies score (MACRO weight 5), confidence (conflict 15/30) and narrative; it never replaces structure, never blocks and never changes the verdict (tested).
4. ✅ Unavailable, stale or synthetic macro → MACRO NOT_EVALUATED (fail safe).
5. ✅ Wired into evaluation, Master Decision (`macroState`), alerts (MACRO_SHIFT), assistant (`get_macro_state`, MACRO answer, glossary), status bar and MACRO tab.
6. ✅ `0.14.0-phase14`; all earlier gates pass.

### Scope decisions
- Macro is context, not a gate: no blocker, no missing-gate entry, no outcome change.
- Surprise look-back set to 6 h (the news assessment lists events only 6 h back; validated in config) instead of the 24 h in the plan.
- Only DXY correlation; D1 closes of the configured market-data provider.

### Changes to earlier-phase code
- Scoring: MACRO factor evaluated from `macro` input, macro conflict items and devil's advocate lines; `DecisionEvaluation.macro`.
- Evaluation service passes the setup direction (eligible data only) and news to macro. Market state sets `macroState` (also on unusable data).
- Alerts memory tracks `macro_bias`; assistant macro tool available; NewsTab text; web evaluation validator checks MACRO scoring; status bar Macro item.

### Not verified / open items
- ⚠️ No real macro feed; defaults leave MACRO NOT_EVALUATED until a maintained series file is configured.
- ⚠️ The scored path with real series and eligible market data is covered by tests (non-synthetic stub), not by a live run: fixture market data is synthetic, so live decisions stay UNAVAILABLE.
- ⚠️ Authority switch not done; only XAUUSD validated; fixture market data only.
- Uncalibrated relationships, weights and thresholds; keyword surprise mapping; end-of-day lag.
- Inherited: in-memory alerts, Docker/Postgres/CI not run, manual risk state, external AI untested live.

---

## Phase 13 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (116 files) | ✅ pass |
| pytest (unit + integration) | ✅ 899 passed (Phase 12: 856) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 319, shared-types 161 | ✅ 480 passed (Phase 12: 451) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture market data + scratchpad calendar file) | ✅ HIGH USD event 10 min ahead → XAUUSD BLACKOUT (window −15/+15 min, event IMMINENT), EURUSD BLACKOUT via USD, EUR EXTREME event not relevant to gold; released MEDIUM event surprise 0.2; calendar filter HIGH+; evaluation missing gates [] with news BLACKOUT and risk.news BLACKOUT; assistant NEWS answer; BTCUSD 404; bad importance 422 |
| Browser check (1600×900) | ✅ MACRO tab news calendar (state, window, next event, calendar coverage, events with surprise); chart News toggle; no horizontal overflow. **Found and fixed:** the status bar showed News UNAVAILABLE while the tab showed BLACKOUT (the decision skipped news on unusable market data) — the decision now always carries the calendar-driven news state; re-verified both show BLACKOUT |

### Acceptance criteria
1. ✅ Spec `EconomicEvent` model; independent calendar providers (unconfigured / file / synthetic fixture); no scraping.
2. ✅ News states CLEAR / CAUTION / BLACKOUT / POST_NEWS_WAIT / NORMALIZED (+ UNAVAILABLE); relevant currencies per market; importance windows; cancelled/delayed handling; actual-vs-forecast surprise.
3. ✅ Strict news blackout is a hard blocker (confirmed plan → NO_TRADE); calendar that cannot prove clear blocks the decision; synthetic calendars never clear.
4. ✅ Gate wired into evaluation (no missing gates), Master Decision (`newsState`), risk, ready watch, alerts (countdown / blackout / cleared) and assistant.
5. ✅ News countdown alerts, BEFORE_MAJOR_NEWS warning, status bar News, MACRO tab calendar, chart markers.
6. ✅ `0.13.0-phase13`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY** even though every gate now exists; a confirmed plan with all gates clear is `CONFIRMED_AWAITING_AUTHORITY` (no direction, no prices).
- News veto applied in the evaluation, not duplicated as a risk lock; risk reports the news state.
- MACRO tab opened for the news calendar only; macro interpretation stays Phase 14.

### Changes to earlier-phase code
- Evaluation: news input/field, missing gates computed per wired gate, new outcome, news advocate lines and warning.
- Market state: `newsState` + news blockers (also on unusable market data); `MasterDecision.newsState`.
- Risk service reports `news`; alerts: news rules + ready-watch NEWS_GATE from the news state; assistant: NEWS intent/tool, blocker texts.
- Gate/setup wording no longer refers to a missing news gate. Web: evaluation/risk validators, status bar, MACRO tab, chart News toggle.

### Not verified / open items
- ⚠️ No real calendar feed; the default configuration blocks every decision until a maintained calendar file is configured.
- ⚠️ Authority switch not done (explicit decision + directional verdict path needed); only XAUUSD validated; fixture market data only.
- News windows uncalibrated; No Wick does not use the calendar; countdown alerts up to ~5 min late (monitor cadence).
- Inherited: in-memory alerts, Docker/Postgres/CI not run, manual risk state, external AI untested live.

---

## Phase 12 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (111 files) | ✅ pass |
| pytest (unit + integration) | ✅ 856 passed (Phase 11: 790) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 299, shared-types 152 | ✅ 451 passed (Phase 11: 422) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| Deterministic answers on non-synthetic fixture data (16 commands) | ✅ 0.6–1.6 s each; grounded facts; UNKNOWN score history; macro/backtest UNAVAILABLE with phase; create_alert proposed, not executed. Found and fixed: the decision's default next required event still said "Analysis engines (Phase 1+)"; DOL answer now separates the decision's H1 DOL from the M15 setup DOL; "protective level None" wording |
| API smoke (fixture server, `AI_PROVIDER=anthropic` without key) | ✅ startup warning + deterministic provider; synthetic stale data → verdict UNAVAILABLE, analysis tools UNAVAILABLE with unknowns listed; IFVG definition; A+ setups via scan (none); BTCUSD 404; 501-char question 422 |
| Browser check (1600×900) | ✅ AI_ANALYSIS tab enabled; disclaimer + provider line; suggested command answered with facts, unknowns, decision reference; no horizontal overflow (verified from the DOM; the tab click timed out for screenshots) |

### Acceptance criteria
1. ✅ AI reads structured state through deterministic tools (spec STEP 14 functions); unbuilt engines return UNAVAILABLE with their phase.
2. ✅ Commands: Analyze Gold, Why are we waiting?, Why isn't this a buy?, Where is liquidity?, What is DOL?, Why did score drop?, Is this No Wick important?, What invalidates this?, Give trade plan, Show A+ setups, Compare assets, Backtest setup (+ risk, session, macro, alert, definitions).
3. ✅ Uses the current Master Decision; never invents facts (grounding guard with fallback); UNKNOWN / UNAVAILABLE / NOT CONFIRMED returned; asset contexts isolated.
4. ✅ Education levels BEGINNER / INTERMEDIATE / ADVANCED / PROFESSIONAL.
5. ✅ External model optional, key server-side, account data withheld by default; deterministic default works offline.
6. ✅ `0.12.0-phase12`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY**; the assistant is explanation only.
- Deterministic explainer is the default and the fallback; an external model can only replace the narrative, never the facts or the verdict.
- `create_alert` is a proposal requiring a user click.
- Socratic/Quiz/Replay tutor and journal coaching deferred to replay/journal phases.

### Changes to earlier-phase code
- Gate default next-required-event text updated (news gate + authority).
- `httpx` promoted to a runtime dependency (external model client).
- Route surface test allows `POST /api/v1/assistant/ask`; AI_ANALYSIS tab enabled.

### Not verified / open items
- ⚠️ External model not exercised against the live Anthropic API (no key; scripted transport tests only).
- ⚠️ Keyword intent classification in deterministic mode; strict guard makes external answers fall back when they compute new numbers.
- pytest duration grew to ~3.3 min (assistant integration tests run real evaluations).
- Inherited: in-memory alerts, no real vendor, Docker/Postgres/CI not run, manual risk state, only XAUUSD validated.

---

## Phase 11 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (103 files) | ✅ pass |
| pytest (unit + integration) | ✅ 790 passed (Phase 10: 756) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 284, shared-types 138 | ✅ 422 passed (Phase 10: 394) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| Alert volume on non-synthetic fixture data (6 h, XAUUSD) | ✅ 45 alerts before tuning (mostly routine M15 FVGs) → 23 after restricting FVG alerts to setup/STRONG-displacement zones and no-wick zones to full rebalance/reaction/failure |
| API smoke (fixture server, monitor running) | ✅ monitor baselined XAUUSD on start (cycle 1.4–1.8 s); feed 200 NOT_AUTHORIZED; watch POST 201, BTCUSD 404, bad category 422; watch checked on the next cycle (found and fixed: previously waited up to 5 min for a new bar); synthetic stale data → watch UNAVAILABLE; DELETE 204; no LONG/SHORT |
| Browser check (1600×900) | ✅ ALERTS view via left nav; monitor status line; watch created from the UI with ✓/✗ checklist (setup steps NOT_EVALUATED on unusable data — fixed during the check); event area shows decision events; no horizontal overflow |

### Acceptance criteria
1. ✅ Categories INFORMATIONAL…CRITICAL and priorities LOW…CRITICAL; 28 alert types with configured category/priority.
2. ✅ Meaningful state changes only: silent baseline, novelty by id + recency, state memory, per-category cooldowns, dedupe keys.
3. ✅ Liquidity, EQH/EQL, PDH/PDL/PWH/PWL, session liquidity, structure, displacement, no wick, FVG/IFVG, entry zone approach/touch, confirmation, entry missed + chase warning, early-entry warnings, risk locks, session change, setup invalidation/expiry, data unavailable/restored.
4. ✅ ALERT_ME_WHEN_READY monitors every mandatory condition and fires once only on a LONG/SHORT decision under FULL authority; proven unable to fire under FAIL_SAFE_ONLY.
5. ✅ Monitor isolation (MONITOR_FAILURE), stale-feed detection, API, ALERTS view, event area feed; client rejects untrusted feeds.
6. ✅ `0.11.0-phase11`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY**; alerts never carry trade wording.
- Monitor runs inside the API process (no worker infrastructure yet); in-memory feed and watches.
- Macro/news/SMT, TP/SL/thesis, order-block and spread alerts deferred to the phases that own those engines.

### Changes to earlier-phase code
- `EvaluationService.evaluate_with_run` (evaluate delegates to it) so the monitor reuses one setup run.
- App lifespan starts/stops the monitor; CORS allows DELETE; the route surface test allows the watch endpoints.
- Web: `#alerts` view, Alerts nav enabled, event area receives alerts, `getJson`-based alert loaders plus POST/DELETE watch helpers.

### Not verified / open items
- ⚠️ In-memory alerts/watches (lost on restart); one monitor per API process.
- ⚠️ Monitor evaluations run on the API event loop (~0.6–2.6 s per new bar per symbol).
- Alert thresholds uncalibrated; M15 analyses only.
- Inherited: no real vendor, Docker/Postgres/CI not run, manual risk state, only XAUUSD validated.

---

## Phase 10 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (97 files) | ✅ pass |
| pytest (unit + integration) | ✅ 756 passed (Phase 9: 740) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 267, shared-types 127 | ✅ 394 passed (Phase 9: 367) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture server) | ✅ `/markets` 200 in 37 ms: 9 markets, only XAUUSD validated; `/scanner` 200: cold 1.8 s (synthetic data stops early), warm 4 ms with row ages; every row UNAVAILABLE on synthetic stale data with its own blockers and `MARKET_NOT_VALIDATED` on the 8 research-only markets; filters; BTCUSD 404; minScore 150 → 422; no LONG/SHORT verdicts |
| Non-synthetic scan (fixture stub via TestClient) | ✅ cold 16.2 s / warm 7 ms; ranking XAUUSD (validated, WATCH 24) first, then USDCHF (SETUP_FORMING), AUDUSD/GBPUSD (24), EURUSD (19), then no-setup markets |
| Browser check (1600×900) | ✅ Scanner view (disclaimer, rules, badges, table); clicking EURUSD switched the command center (EURUSD candles, research-only banner, EURUSD decision); no horizontal overflow; one cosmetic fix (badge wrapping) |

### Acceptance criteria
1. ✅ Markets view: catalog with validation, market hours and position size status.
2. ✅ Watchlist: per-browser subset, sanitized against the catalog, scanned on demand.
3. ✅ Scanner: each symbol's own Master Decision, isolated, cached 60 s, ranked deterministically for attention; filters.
4. ✅ Non-validated markets blocked by the WAIT-class `MARKET_NOT_VALIDATED` and labelled RESEARCH ONLY.
5. ✅ Symbol switching clears every symbol-scoped state (page test); loaders reject other-symbol payloads.
6. ✅ Scan rows equal `/market-state` decisions (integration test); client rejects authority claims, READY states and unflagged research markets.
7. ✅ `0.10.0-phase10`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY**; the scan outcome is an attention ranking only.
- Research-only markets get a decision blocker, not just a UI label, so every surface (and later alerts/AI) treats them the same way.
- The watchlist lives in browser storage (a display preference; no account data) until auth and a database exist.

### Changes to earlier-phase code
- Decision gate: `MARKET_NOT_VALIDATED` for non-deeply-validated instruments.
- Web page: the symbol is state (was the constant XAUUSD); symbol-scoped states reset on change; the event log carries the symbol; the intelligence panel remounts per symbol.
- Left nav: hash views, Markets / Watchlist / Scanner enabled, active view marked. ChartPanel: research-only banner. `getJson` takes a timeout.

### Not verified / open items
- ⚠️ Only XAUUSD is validated; the other 8 markets are research only.
- ⚠️ A cold 9-market scan takes ~16 s (sequential, CPU-bound); no background scan worker.
- Opening a market does not change the TradingView Desktop symbol (the bridge label can still show XAUUSD).
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, manual risk state.

---

## Phase 9 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (94 files) | ✅ pass |
| pytest (unit + integration) | ✅ 740 passed (Phase 8: 602) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 243, shared-types 124 | ✅ 367 passed (Phase 8: 316) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture + a scratchpad profile file) | ✅ `/risk` 200: CLEAR, STANDARD budget 100 / 300 / 600 / 200, M15 volatility 1.01×, NOT_AUTHORIZED, news NOT_EVALUATED; evaluation missing gates only NEWS; `/risk/calculate` 200 in ~7 ms: 0.28 volume, 98.00 USD, 320 points, 32 pips, margin 568.96; no spec → SIZE_UNVERIFIED; CUSTOM without limits 422 (values not echoed after a fix); BTCUSD 404; no LONG/SHORT tokens; synthetic stale decision UNAVAILABLE with no prices |
| Browser check (1600×900) | ✅ RISK tab from the left-nav `#risk` link; live CLEAR assessment and budget; the calculator's cross-origin POST returned WITHIN_LIMITS 0.28; localStorage empty; no horizontal overflow |

### Acceptance criteria
1. ✅ Manual account profile (spec `AccountProfile`) + risk profiles CONSERVATIVE / STANDARD / AGGRESSIVE / CUSTOM, with hard limits.
2. ✅ Locks: daily loss, weekly loss, max open risk, max positions, max trades/day, consecutive losses, volatility, optional prop rules, stale account state; trade locks for invalid stop, adding to a loser, unsafe spread, margin, minimum volume. News NOT_EVALUATED (Phase 13).
3. ✅ Position sizing from entry, structural stop, contract/tick details and currency conversion; price distance, points, platform pips, dollar risk, risk with/without spread.
4. ✅ Unknown, inconsistent or unconverted spec → POSITION_SIZE_UNVERIFIED; never guessed.
5. ✅ No martingale / no adding to losers / no revenge sizing: property-tested (a further loss never increases the budget or volume).
6. ✅ Risk has final veto: a lock on a confirmed plan → evaluation NO_TRADE; the decision gets riskStatus and risk blockers, never a size or prices (integration test with an A+ plan).
7. ✅ `/risk/{symbol}`, stateless `/risk/calculate`, RISK tab, Risk nav; client rejects inconsistent risk payloads.
8. ✅ `0.9.0-phase9`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY**: the news blackout gate (Phase 13) doesn't exist. Switching authority later must be an explicit decision.
- The account profile is a **server-side, git-ignored JSON file** (`RISK_PROFILE_PATH`), read-only for the API, so every surface uses the same risk state (spec STEP 18) without adding account-write endpoints. The only POST is a stateless calculator.
- A user-entered contract spec is labelled `SIZED_FROM_USER_SPEC`, not `VERIFIED` (no verified spec source exists).
- Validation errors (422) now return field paths and messages only, so posted balances are never echoed.

### Changes to earlier-phase code
- Evaluation: `risk` field; missing gates = NEWS only when risk is wired; RISK_RR needs no risk lock; NO_TRADE on a risk veto; devil's advocate risk lines.
- Market state: riskStatus + risk blockers; `INSTRUMENT_SPEC_MISSING` dropped when a user spec exists; risk status in the change fingerprint.
- Setup run exposes its closed M15 candles; BLOCKED next event and plan detail text now say "risk check and news gate".
- API: CORS allows POST; read-only route test allows exactly `POST /api/v1/risk/calculate`.
- Web: evaluation validation understands risk (NO_TRADE plans only with a risk lock); RISK tab and Risk nav enabled.

### Not verified / open items
- ⚠️ Account state is manual: wrong numbers typed today are trusted (journal/paper trading: Phases 15–16).
- ⚠️ Presets, hard limits, volatility ratio and spread share are uncalibrated.
- ⚠️ The confirmed-plan sizing path is proven by unit, property and hand-built integration tests only; fixture data has not produced a confirmed plan.
- The balance is readable by anyone who can reach the local API (no auth yet).
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, decision latency ~1 s (the risk endpoint reuses the evaluation run).

---

## Phase 8 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (89 files) | ✅ pass |
| pytest (unit + integration) | ✅ 602 passed (Phase 7: 545) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 208, shared-types 108 | ✅ 316 passed (Phase 7: 287) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture) | ✅ evaluation 200 (~0.7 s): synthetic → UNAVAILABLE / NOT_AUTHORIZED, both missing gates; no LONG/SHORT_READY anywhere; BTCUSD 404; non-synthetic decision WAIT, setup WATCH, score 24 / D, confidence LOW, ~1.0 s warm / ~1.9 s cold |
| Browser check (1600×900) | ✅ ENTRY tab NOT AUTHORIZED banner + fail-safe content; status bar Score; one cosmetic fix (empty Evidence heading) |

### Acceptance criteria
1. ✅ Entry models M15_CLOSE (+ IFVG label), NO_WICK_REBALANCE, LTF_REFINEMENT (M5), CONSERVATIVE_RETEST, LIMIT_RESEARCH; modes CONSERVATIVE / STANDARD / AGGRESSIVE; BREAKER refused.
2. ✅ Plan: buffered structural stop, DOL TP1, distinct TP2/TP3, R:R; chase protection → ENTRY_MISSED at confirmation and after it.
3. ✅ Confirmed plans stay BLOCKED (pending risk/news gates); no READY state is ever emitted (hand-built scenarios plus properties in two modes).
4. ✅ Scoring with spec weights, correlated-evidence and counter-trend adjustments, grades, ConflictScore, DataQualityScore, confidence capped at MODERATE, warnings, devil's advocate, evidence, hard blockers, outcomes.
5. ✅ **The strongest possible evaluation (A+, confirmed plan) cannot change the verdict or publish prices** (integration test).
6. ✅ Endpoint; failure isolation; decision enrichment verdict-neutral.
7. ✅ ENTRY tab, status bar Score, not-authorized plan lines; client rejects authority / confidence above cap / inconsistent payloads.
8. ✅ `0.8.0-phase8`; all earlier gates pass.

### Scope decisions
- **Verdict authority remains FAIL_SAFE_ONLY**: required hard-blocker gates (risk locks Phase 9, news blackout Phase 13) don't exist. Switching authority later must be an explicit decision.
- BREAKER entry model unavailable (Phase 20+); BEFORE_CLOSE / BEFORE_MAJOR_NEWS warnings never emitted (closed candles; no calendar).
- RISK_RR scored on R:R only (sizing Phase 9); MACRO NOT_EVALUATED (Phase 14).

### Test corrections during this phase
- Phase 7 happy path: candle 29 now confirms by 15M close, so the scenario uses a non-confirming candle 29 to keep testing WAITING_FOR_CONFIRMATION; new Phase 8 tests cover the confirmation.
- One plan-maths assertion was wrong (R:R from 101 is 1.67, a chase), not the engine.

### Changes to earlier-phase code
- Setup engine: BLOCKED / ENTRY_MISSED transitions, confirmed IFVG leg zones, entry inputs (no-wick, M5); `Setup.entryPlan`; LTF_CONFIRMATION step evaluated; "retracement zone" wording.
- `SetupService.run()` exposes the pipeline and session for reuse. `MarketStateService` prefers the evaluation service (which also sets setupState / setupType).
- Web setup payload rules: BLOCKED (with a plan) and ENTRY_MISSED are valid; ineligible data must be BLOCKED. The ENTRY tab is enabled.

### Not verified / open items
- ⚠️ No real market data yet: the confirmed-plan path is only exercised by hand-built scenarios and random-walk properties; on synthetic data every touch failed chase protection.
- ⚠️ All entry and scoring parameters uncalibrated.
- Decision latency ~1.0 s warm / ~1.9 s cold; no caching.
- Inherited: no real vendor, Docker/Postgres/CI not run, polling.

---

## Phase 7 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (82 files) | ✅ pass |
| pytest (unit + integration) | ✅ 545 passed (Phase 6: 484) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 189, shared-types 98 | ✅ 287 passed (Phase 6: 254) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture) | ✅ setups 200 (~0.5 s warm): BLOCKED (synthetic), bias BULLISH, 8 setups / 41 events with reasons, 0 forbidden states; BTCUSD 404; synthetic decision `setupState` NOT_EVALUATED |
| Browser check (1600×900, M15) | ✅ setup milestones + DOL line; OVERVIEW setup progress panel (state, missing next, checklist, last reason); nav "Setups" link |

### Acceptance criteria
1. ✅ Setup lifecycle DISCOVERED → … → WAITING_FOR_CONFIRMATION with INVALIDATED / EXPIRED, bullish and bearish, from HTF bias, DOL, liquidity event, displacement MSS, leg FVG and retracement.
2. ✅ Hand-built scenarios for every transition, invalidation and expiry (including trading-day-end expiry, deferred from Phase 6); PO3 phases.
3. ✅ **No-lookahead** prefix property plus an ALLOWED transition table enforced on random data; authority states never emitted.
4. ✅ Endpoint; INVALID → no setups; ineligible → BLOCKED; engine failure isolated.
5. ✅ Decision `setupState` / `setupType` enrichment verdict-neutral and fail-safe; the authority guard rejects READY/ACTIVE/CLOSED.
6. ✅ OVERVIEW setup progress (missing-confirmation panel), Setup chart overlay, nav link.
7. ✅ `0.7.0-phase7`; all earlier gates pass.

### Scope decisions
- LTF confirmation, entry models, modes, warnings, chase protection, ENTRY_MISSED and READY states → Phase 8; stops/targets/R:R → Phase 9; ACTIVE/CLOSED → Phase 16.
- Setup bias = event-based H4+H1 as-of trend (replayable), distinct from the decision's state-based D1+H4 `htfBias`.
- One setup model (LIQUIDITY_SWEEP_MSS) on M15; retracement zones = the displacement leg's FVGs.

### Test-data corrections during this phase
- Property test: upstream key levels and session pools are windowed as of the analysis time (latest N), so a longer history drops older pools that a prefix still has. The engine property test now uses prefix-stable pools (swings/EQ), and the limitation is documented (see below).
- Scenario helpers: a few scenarios referenced candle indexes beyond their candle count; fixed in the tests, not the engine.

### Changes to earlier-phase code
- `enforce_verdict_authority` also rejects READY/ACTIVE/CLOSED `setupState` (sets it to BLOCKED).
- `MarketStateService` accepts a `SetupService`. TS `MasterDecision` unchanged (`setupState` stays a string, since `NO_SETUP` / `NOT_EVALUATED` are not lifecycle states).
- Left nav "Setups" enabled; the SESSION tab text updated (PO3/expiration now implemented).

### Not verified / open items
- ⚠️ Historical setup replay uses the as-of pool universe (latest 5 PDH/PDL, 2 PWH/PWL, 2 per session); backtest/replay must rebuild inputs per step (Phases 18–19). The current setup is unaffected.
- ⚠️ All setup thresholds uncalibrated; on synthetic data most setups end "liquidity ran" or "no confirmation break" and none reached a zone touch.
- Latency grows again (setups ≈ M15 pipeline + H4/H1 structure + sessions, also inside the decision); still no caching.
- Inherited: no real vendor, Docker/Postgres/CI not run, polling.

---

## Phase 6 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (76 files) | ✅ pass |
| pytest (unit + integration) | ✅ 484 passed (Phase 5: 402) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 171, shared-types 83 | ✅ 254 passed (Phase 5: 216) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture) | ✅ sessions 200 (~125 ms warm): 55 instances, opens, ADR 99.5% LATE, 8 Judas; 16 session pools per timeframe; No Wick SESSION evaluated M5/M15/H1, NOT_EVALUATED H4; BTCUSD 404; synthetic `sessionState` null |
| Browser check (1600×900, M15, New York time) | ✅ session segments, New York axis, status bar Session + Daily change, SESSION tab matches API (one display defect found and fixed) |

### Acceptance criteria
1. ✅ DST-aware clock (US start/end, London gap weeks, trading-day roll), active sessions and kill zones, time quality, next session.
2. ✅ Asia / London / NY AM / NY PM / London Close instances with high/low/midpoint/range; daily / New York midnight / weekly opens; previous session.
3. ✅ Asian range state, ADR and expansion, session quality, Judas swing: hand-built scenarios.
4. ✅ **No-lookahead:** prefix property tests for instances, Judas swings, pools and ADR.
5. ✅ Session highs/lows as liquidity pools known at window end; a missing source only makes liquidity ineligible.
6. ✅ No Wick SESSION context evaluated on intraday timeframes.
7. ✅ Endpoint; INVALID data keeps the clock without levels; `sessionState` enrichment verdict-neutral and fail-safe.
8. ✅ SESSION tab, sessions overlay, chart time-zone selector, status bar Session and Daily change.
9. ✅ `0.6.0-phase6`; all earlier gates pass.

### Scope decisions
- PO3, session-target completion and setup expiration move to Phase 7 (they need setups).
- Session quality adds no blocker (verdict authority is Phase 8). Holidays are not modelled (INCOMPLETE, fail-safe).
- The time-zone selector is display formatting only (no user settings without auth).

### Defects found and fixed during this phase
- **Web (browser check):** with stale data, the SESSION tab filtered sessions by the clock's trading day and showed an empty table. It now lists the latest trading day in the data and labels it.
- **No Wick scoring:** an early wiring scored SESSION on H4/D1 by candle open time (a D1 candle opens at 17:00, so always AVOID). Now NOT_EVALUATED above H1, with a regression test.

### Changes to earlier-phase code
- `load_inputs` returns `(series, d1, sessions)`; `run_pipeline` accepts `sessions=`. Liquidity gains session key levels and `SESSION_LEVELS_UNAVAILABLE`.
- Liquidity magnet type weight `SESSION`: 20.
- No Wick context weights: FVG 15→10, TREND 15→10, SESSION 0/5/10 (see PHASE_6 rule 14). `NoWickInputs.session_quality` added.
- `MarketStateService` accepts a `SessionService`. TS `MasterDecision.sessionState` is typed. SESSION tab enabled.

### Not verified / open items
- ⚠️ Windows, kill zones and every threshold are uncalibrated research defaults (no real vendor).
- ⚠️ Holidays/early closes not modelled (sessions INCOMPLETE; ADR still counts holiday D1 candles).
- Decision latency not re-measured; more M15 loading per request, still no caching.
- Inherited: no real vendor, Docker/Postgres/CI not run, polling.

---

## Phase 5 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (68 files) | ✅ pass |
| pytest (unit + integration) | ✅ 402 passed (Phase 4: 340) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 147, shared-types 69 | ✅ 216 passed (Phase 4: 184) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture, M5/M15/H1/H4) | ✅ M15: 53 events (27 insignificant, 17 meaningful, 7 strong, 2 exceptional), 26 zones, all 8 zone event types; W1 422, BTCUSD 404; synthetic decision `noWickState` null |
| Browser check (1600×900, M5 + H1) | ✅ "NW M/S/X" markers drawn; NO_WICK tab matches API (components with n/e); overview row "—" |

### Acceptance criteria
1. ✅ Candle features and the 12 spec classifications, with strength; `NEWS_DRIVEN_NO_WICK` is never assigned.
2. ✅ CandleQualityScore, ContextScore (per-factor components, NOT_EVALUATED when a source is missing) and NoWickRelevanceScore are kept separate.
3. ✅ Rebalance references (close, 25/50/75, open, origin, FVG overlap, OB overlap NOT_EVALUATED) and all 9 zone states.
4. ✅ Hand-built scenarios per spec STEP 16: true/near, one-sided, tiny rejection, inside bar, news, bad data, rebalance, invalidation.
5. ✅ **No-lookahead:** a later FVG cannot improve a historical event (scenario), and every prefix gives identical events, scores and components (property). Zone grammar enforced.
6. ✅ Endpoint; INVALID withheld; No Wick failure isolated; a PD failure degrades components to NOT_EVALUATED.
7. ✅ `noWickState` enrichment is verdict-neutral, CONTEXT_ONLY; fails safe.
8. ✅ Overlay, NO_WICK tab and overview row, with payload validation and anchor-sync hiding.
9. ✅ `0.5.0-phase5`; all earlier gates pass.

### Scope decisions
- SESSION (Phase 6) and NEWS (Phase 13) context are NOT_EVALUATED. OB overlap waits for advanced PD arrays (Phase 20+). Adaptive No Wick is not built.
- One-sided shapes require body ≥ 50% (research default; the spec gives no floor).
- "HTF alignment" is scored as the analysed timeframe's external trend as of the candle; weighted MTF scoring is Phase 8.

### Test-data corrections during this phase
- A test assumed the second candle of a series has no ATR. ATR averages the prior true ranges available (the same rule as displacement since Phase 4), so the test now asserts that only the first candle is unclassified.

### Changes to earlier-phase code
- `run_pipeline` gained a No Wick stage and `nw_cfg`, and returns `no_wick`. Liquidity/PD failures now also pass `None` inputs to No Wick.
- `MarketStateService` accepts a `NoWickService`. TypeScript `MasterDecision.noWickState` is typed as `NoWickDecisionState`.
- NO_WICK panel tab enabled.

### Not verified / open items
- ⚠️ About half of the synthetic zones end REACTED (0.5 ATR within 5 bars). The reaction rule may be too loose; review with real XAUUSD data.
- ⚠️ No Wick thresholds, weights and zone rules are uncalibrated (no real vendor).
- Decision latency not re-measured; the decision now runs one extra M15 pipeline pass without caching.
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, UTC-only display.

---

## Phase 4 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (61 files) | ✅ pass |
| pytest (unit + integration) | ✅ 340 passed (Phase 3: 289) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 127, shared-types 57 | ✅ 184 passed (Phase 3: 158) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture, M15) | ✅ 96 zones (52 FVG, 44 IFVG), 11 active, 54 displacements (all 4 grades), all 9 event types; ~66 ms warm |
| Browser check (1600×900, M15) | ✅ zone edges and STRONG/EXCEPTIONAL markers drawn; PD_ARRAYS tab matches API |

### Acceptance criteria
1. ✅ Hand-built scenarios with exact results: bullish/bearish FVG, tiny rejection, touched/partial/half/full fill, wick-full vs close-through, IFVG confirmed by displacement, confirmed by acceptance, failed by reclaim, failed by expiry, all 4 grades plus the body-quality cap.
2. ✅ **No-lookahead property test** on every prefix, plus lifecycle grammar and price invariants, over data exercising every event type.
3. ✅ PD arrays endpoint; INVALID/DISCONNECTED → no analysis.
4. ✅ Structure events carry `displacementQualifier`; a PD failure leaves NOT_EVALUATED without affecting liquidity.
5. ✅ Decision enrichment is verdict-neutral (verdict, blockers and quality identical; `pdArray` stays null).
6. ✅ Overlay and PD_ARRAYS tab, with anchor-sync hiding.
7. ✅ `0.4.0-phase4`; all earlier gates pass.

### Scope decisions
- OB / Breaker / Mitigation Block / BPR / Volume Imbalance, PD array priority, EntryZoneScore, price-delivery phase, premium/discount and OTE are deferred (spec STEP 15 puts advanced PD arrays in Phase 20+; entry-zone selection is Phase 8).

### Test-data corrections during this phase (engine behaviour confirmed correct)
- One scenario row had low > open and was correctly rejected by data validation (IMPOSSIBLE_OHLC).
- A retrace candle legitimately formed a second FVG, so the test now selects the IFVG by parent.
- A "weak" close-through candle was actually a MODERATE displacement (~1.85 ATR) and correctly confirmed the IFVG at once, so the test now uses a non-displacement candle.

### Changes to earlier-phase code
- `run_pipeline` now has a PD-arrays stage (independent failure isolation) and returns `pd_arrays`. `MarketStateService` accepts a `PdArrayService`.
- One Phase 3 test assertion updated: `displacementQualifier` is now PRESENT/ABSENT instead of NOT_EVALUATED.

### Not verified / open items
- ⚠️ On synthetic data 30 of 44 potential IFVGs confirmed (68%). The acceptance rule may be too permissive; review with real XAUUSD data before IFVG entry models (Phase 8).
- ⚠️ Displacement/FVG thresholds and quality weights are uncalibrated research defaults (no real vendor yet).
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, UTC-only display, no caching (decision ~0.4 s warm / ~1.5 s cold).

---

## Phase 3 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (55 files) | ✅ pass |
| pytest (unit + integration) | ✅ 289 passed (Phase 2: 237) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 110, shared-types 48 | ✅ 158 passed (Phase 2: 103; includes the TradingView switcher tests) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture, H1) | ✅ 81 pools (62 swings, 5 EQ, PDH/PDL ×5, PWH/PWL ×2), 112 events of all 6 types, DOL HIGH (margin 31.3) on PWH; ~60 ms |
| Browser check (1600×900, H1) | ✅ DOL/DOL2 + key-level lines and sweep/run markers drawn with structure; LIQUIDITY tab matches API |

### Acceptance criteria
1. ✅ Hand-built scenarios with exact events: EQH/EQL, sweep, run, false sweep, break→reclaim, break stays broken, touches, PDH/PDL/PWH/PWL (including a holiday week), DOL, two-sided unclear.
2. ✅ **No-lookahead property test**: every prefix of 5 random series gives the same events and known pools. Plus transition grammar and price invariants, over data that exercises every pool and event type.
3. ✅ Liquidity endpoint; INVALID/DISCONNECTED → no analysis.
4. ✅ Structure events carry `liquidityQualifier` PRESENT/ABSENT; engine failure leaves NOT_EVALUATED.
5. ✅ Decision enrichment is verdict-neutral: the only possible blocker difference is `DOL_UNCLEAR`, present exactly when context is UNCLEAR. Failures fail safe.
6. ✅ Chart overlay and LIQUIDITY tab, with anchor-sync hiding.
7. ✅ `0.3.0-phase3`; all earlier gates pass.

### Scope decisions
- Asian/London/New York session highs/lows deferred to Phase 6 (Session & Time engine owns session windows).
- Numeric MTF alignment score still deferred to Phase 8.

### Defects found and fixed during this phase
- **Engine:** pools known exactly at the latest close (a swing confirmed by the last candle, a PDH known at that close) were dropped from the snapshot. Now included (caught by the first scenario test).
- **Web overlay:** an older PDH/PDL/PWH/PWL could be labelled as the current one when the latest was taken or already drawn as DOL. Now the latest per type is chosen first and never substituted.

### Changes to earlier-phase code
- Structure helpers moved to `structure/analysis.py`. `StructureService.analyze` and its M15 decision context now go through the shared pipeline, so events carry qualifiers. Alignment still uses states only.
- `gate._ordered` → `ordered_blockers` (reused by enrichment). Blocker enum gained `DOL_UNCLEAR`; AnalysisIneligibility gained `KEY_LEVELS_UNAVAILABLE` and `LIQUIDITY_ANALYSIS_FAILED`.

### Not verified / open items
- ⚠️ Liquidity thresholds and magnet weights are uncalibrated research defaults (no real vendor yet).
- ⚠️ Decision latency grew to ~0.5 s warm / ~1.9 s cold (multi-timeframe pipeline, no caching).
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, UTC-only display, OneDrive slowness.

---

## Phase 2 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (45 files) | ✅ pass |
| pytest (unit + integration) | ✅ 237 passed (Phase 1: 161) |
| TypeScript `tsc --noEmit` | ✅ pass |
| vitest: web 66, shared-types 37 | ✅ 103 passed (Phase 1: 64) |
| `next build` | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture) | ✅ structure + alignment 200. M15 external BULLISH (protected low 2022.29), 20 swings / 12 events; alignment UNCLEAR (D1 insufficient candles); htfBias UNKNOWN (synthetic) |
| Browser check (1600×900) | ✅ M15 overlay (swing labels, BOS/CHoCH markers + segments, protected-low line) matches the STRUCTURE tab; tab states "NOT used by the decision: DATA_SYNTHETIC, DATA_STALE" |

### Acceptance criteria
1. ✅ Swings known only after their confirming candles close. **No-lookahead property test**: every prefix of 5 random series × 3 configs × 2 levels matches full history (swings, labels, events, break links).
2. ✅ Hand-built scenarios with exact expected events: HH/HL/LH/LL/EH/EL, BOS, wick-only POTENTIAL, CHoCH → failure → BOS resumption, CHoCH → MSS, direct MSS, protected updates/fallback, one break per swing, all 5 states, bearish mirror.
3. ✅ Structure endpoint: internal + external levels, chronological log, protected levels. INVALID/DISCONNECTED → no analysis.
4. ✅ MTF alignment endpoint (D1/H4/H1/M15/M5).
5. ✅ Decision `htfBias`/`structureEvent` only from eligible data. Verdict, blockers and quality are identical with and without structure (tested); structure failure → UNKNOWN; UNAVAILABLE decisions skip structure.
6. ✅ Overlay only when every anchor is on the drawn candles, otherwise hidden with a reason. STRUCTURE tab live.
7. ✅ Strategy version `0.2.0-phase2`; all earlier gates pass.

### Scope decisions (per "do not implement later-phase logic early")
- MSS is **structural only**. Liquidity/displacement qualifiers are `NOT_EVALUATED` until Phases 3–4.
- MTF alignment is categorical. The spec's numeric alignment *score* is deferred to scoring (Phase 8).

### Changes to earlier-phase code
- `CandleService.load_series` is now the shared validated-series loader (chart, structure, decision). Chart behaviour is unchanged.
- `MarketStateService` accepts an optional `StructureService` for enrichment; the gate is unchanged.
- Vitest config gained the `@/` path alias.

### Not verified / open items
- ⚠️ Structure thresholds (pivot 3/10, 0.1 ATR tolerance, ranging bars) are uncalibrated research defaults; no real XAUUSD data yet.
- ⚠️ Fixture has ~40 D1 candles (< minCandles 50), so D1 structure is UNCLEAR on fixture data (by design, not a bug).
- Inherited: no real vendor, Docker/Postgres/CI not run, polling, UTC-only display, OneDrive slowness.

---

## Phase 1 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (40 files) | ✅ pass |
| pytest (unit + integration) | ✅ 161 passed (Phase 0: 120) |
| TypeScript `tsc --noEmit` (web, shared-types) | ✅ pass |
| vitest: web 46 (incl. 10 jsdom UI tests), shared-types 18 | ✅ 64 passed |
| `next build` (production) | ✅ pass |
| Guardrail scan | ✅ pass |
| API smoke (fixture) | ✅ M5/M15/H1/H4/D1 → 200; W1 → 422; BTCUSD → 404. Warm latency ~40–150 ms (first M15/H1 ~1.3 s while the fixture cache builds) |
| Browser check (1600×900) | ✅ STEP 11 layout; M5 and D1 charts drawn with SYNTHETIC + STALE banners; D1 last close = M5 last close; API stopped → "CHART DATA UNAVAILABLE", verdict UNAVAILABLE |

### Acceptance criteria
1. ✅ `GET /api/v1/candles/{symbol}?timeframe={M5,M15,H1,H4,D1}&limit=1..1000` returns validated candles + quality, issues, provider, synthetic flag, strategy version.
2. ✅ H4/D1 = New York 17:00 buckets (DST-aware), derived from H1. Vendor conventions are never trusted; a misaligned H4/D1 bar is INVALID.
3. ✅ Cross-timeframe consistency tested: H4/D1/H1/M15 OHLC equal the contained M5 bars.
4. ✅ INVALID/DISCONNECTED → no candles. STALE/DELAYED drawn with a warning.
5. ✅ No lookahead: `end=now` passed to providers AND bars after `now` rejected. Forming candle `isClosed=false`.
6. ✅ 422 for unsupported timeframe/limit, 404 for unknown symbol, DISCONNECTED for all provider failures (including an incompatible provider).
7. ✅ STEP 11 layout. Unbuilt fields/tabs/nav items are marked N/A / NOT AVAILABLE with their phase; UI tests confirm no made-up values.
8. ✅ Lightweight Charts 5.2.1 renders validated arrays only. UTC axis, TF switcher, synthetic banner, client-side payload validation (11 rejection cases tested).
9. ✅ Verdict still from the M5 Master Decision only; switching chart timeframe never changes it (tested).
10. ✅ Strategy version `0.1.0-phase1`, authority `FAIL_SAFE_ONLY`.
11. ✅ All Phase 0 gates still pass.

### Intentional rule changes vs Phase 0 (tests updated accordingly)
- H4/D1 now use New York 17:00 alignment, gap detection and staleness, replacing Phase 0's "raw elapsed periods" fallback. UTC-midnight D1 bars are now INVALID.
- `aggregate()` now supports H4/D1 targets. A still-forming bucket is no longer reported as `INCOMPLETE_BUCKET` (only past buckets with missing constituents are).
- `MarketDataProvider.get_historical_bars` gained optional `limit`. Market state ingests at most 500 M5 bars ending at `now`.
- Fixture provider: one M5 path (M15/H1 aggregated from it) over 2024-02-25 → 2024-04-19, so the window spans DST.

### Not verified / open items
- ⚠️ **No real data vendor.** The chart renders synthetic data only. Choosing a vendor (and checking its licensing) is the user's decision and is needed to validate XAUUSD against real prices.
- ⚠️ Docker/PostgreSQL and GitHub Actions still not run (Docker not installed; not a git repo).
- Live updates are 15 s polling. No WebSocket/Redis stream yet.
- Chart times in UTC only (NY/London/user timezone display is Phase 6). No analysis overlays yet.
- The project lives in OneDrive: jsdom's cold import measured ~14 s, so web test files run sequentially. Moving the repo outside OneDrive would speed up installs, tests and builds.

---

## Phase 0 — results

### Checks run (`scripts/check-all.ps1`, Windows 11, Python 3.12.13 via uv, Node 24.16)
| Gate | Result |
|---|---|
| ruff lint + format | ✅ pass |
| mypy `--strict` (39 files) | ✅ pass |
| pytest (unit + integration) | ✅ 120 passed |
| TypeScript `tsc --noEmit` (web, shared-types) | ✅ pass |
| vitest (web 15, shared-types 15) | ✅ 30 passed |
| `next build` (production) | ✅ pass |
| Guardrail scan (no order/broker/TradingView-feed/public secrets) | ✅ pass (and verified it fails on a planted `place_order`) |
| Backend smoke (uvicorn + fixture provider) | ✅ UNAVAILABLE `[DATA_STALE, DATA_SYNTHETIC, MARKET_CLOSED, INSTRUMENT_SPEC_MISSING, ANALYSIS_GATES_NOT_IMPLEMENTED]`; secret not leaked; POST → 405 |
| Frontend smoke (`next start` in browser) | ✅ renders backend decision; API stopped → UNAVAILABLE / PROVIDER_UNAVAILABLE, payload untrusted |

### Acceptance criteria
1. ✅ Repository layout matches spec STEP 19. Fresh build; no prior code reused.
2. ✅ `MarketDataProvider` Protocol (7 methods) + registry. TradingView rejected by name and by instance.
3. ✅ Normalized `Candle`/`Quote`: UTC-only timestamps; close_time = open_time + timeframe.
4. ✅ Validation: impossible OHLC, non-positive, NaN, zero range, negative volume, duplicates (identical/conflict), out-of-order, naive/future timestamps, misalignment, DST-aware gaps (weekend and metals daily break excluded), range spikes (no lookahead), crossed quote, bad-tick jump, wide spread, provider disagreement. No bar fabrication.
5. ✅ Quality classification LIVE/CURRENT/DELAYED/STALE/DISCONNECTED/INVALID, market-hours aware, thresholds from strategy-spec.
6. ✅ Closed-candle aggregation M1→H1 with incomplete/forming buckets flagged.
7. ✅ Fail-safe gate: exhaustive input sweep proves no LONG/SHORT. Authority guard converts any directional verdict to UNAVAILABLE + SYSTEM_INTEGRITY_FAILURE.
8. ✅ Instrument registry (XAUUSD primary + 8 secondaries). Specs null → POSITION_SIZE_UNVERIFIED.
9. ✅ Read-only API (5 GET endpoints). No execution routes (tested). No secret leakage (tested).
10. ✅ SQLAlchemy models + Alembic 0001 (upgrade/downgrade tested). Closed history is never silently rewritten.
11. ✅ Enum contract and API field contract tested in both Python and TypeScript.
12. ✅ Web shell builds and fails safe on unreachable/malformed/mismatched/forbidden payloads.
13. ✅ CI workflow, docker-compose, Dockerfiles, `.env.example`, `docs/phases/PHASE_0.md`, this file.

### Not verified / open items (carried forward)
- ⚠️ **Docker not installed on the build machine.** `docker-compose.yml` and both Dockerfiles have not been built or run. PostgreSQL has not been exercised; migrations were tested on SQLite only.
- ⚠️ GitHub Actions workflow written but not run (the project is not a git repository yet).
- No real independent data vendor configured. Vendor selection and licensing is a user decision needed before Phase 1 can show real XAUUSD data.
- Holidays / early closes not modelled. H4+ trading-day boundaries deferred to Phase 6.
- Persistence not yet wired into the request path. Redis bus not wired (in-memory bus behind the same interface).
- Auth not implemented (V1 must-have, later phase).
- Pytest shows 2 third-party deprecation warnings (Starlette TestClient/httpx, anyio alias). They are harmless.

### Next phase gate (Phase 0)
Approved and completed — see Phases 1–2 above.

---

## Playbook Revamp (per REVERSAL_SETUP_SPEC.md + build spec) — in progress

### Phase A — foundations (backtest-gated, live model untouched)
- **Step 1: IMR (Immediate Rebalance) PD-array type — DONE.** New `PdArrayType.IMR` + `PdArrayEventType.IMR_CREATED`;
  detector `pd_arrays/imr.py` (displacement on the middle candle b, candle c overlaps candle a = no FVG gap,
  expansion-gated via `imr.minGrade`); merged additively into the pd-array pipeline (FVG path untouched).
  Tests: `tests/unit/test_imr_engine.py` (bullish, bearish mirror, FVG-gap-not-IMR, expansion gate) — 4/4 PASS.
  Regression: test_pd_arrays_engine/properties, test_setup_engine, test_models — 65/65 PASS. Live: IMR zones
  flow through `/pd-arrays` (verified 5 on H1); continuation model unaffected.
- Next: Step 2 REVERSAL_FVG sub-state · Step 3 IFVG two-candle CONFIRMED mechanic (spec §4) · Step 4 W1
  aggregation + no-wick on 1M/30M/W1 · Step 5 forming-HTF-candle exposure.
