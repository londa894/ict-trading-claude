# Architecture (as of Phase 19)

## Non-negotiables enforced in code

| Rule | Where it is enforced |
|---|---|
| No broker connection, no order execution | No such endpoints/interfaces; API is GET-only except the stateless `POST /api/v1/risk/calculate`, ready watches, the assistant question, the user's own journal records (create / append outcome / delete; no update route) and paper simulations (create / simulated close / cancel / delete; nothing is sent anywhere) (`test_api_surface_is_read_only_and_has_no_execution_routes`); CI guardrail script bans order/broker identifiers |
| TradingView is renderer only | `ProviderRegistry` refuses any provider named like TradingView (`ForbiddenProviderError`); Lightweight Charts only receives validated arrays from `apps/web/src/lib/candles.ts` |
| Independent market data | `MarketDataProvider` Protocol (`services/api/app/providers/base.py`) |
| Deterministic logic | Pure functions over normalized candles; thresholds in versioned `packages/strategy-spec` |
| Fail safe | `market_state/gate.py` emits only WAIT/UNAVAILABLE under `FAIL_SAFE_ONLY`; structure enrichment can't alter verdict/blockers (tested); web `failsafe.ts`/`structure.ts` refuse untrusted payloads |
| No lookahead | Swings/pools/key levels usable only after they are known; prefix-equivalence property tests over random series (`test_structure_properties.py`, `test_liquidity_properties.py`, `test_pd_arrays_properties.py`, `test_no_wick_properties.py`, `test_sessions_properties.py`, `test_setup_properties.py`) |
| Never guess instrument specs | `instruments.json` specs are `null` → `POSITION_SIZE_UNVERIFIED` + `INSTRUMENT_SPEC_MISSING`; user specs must pass the tick-value consistency check and a fresh conversion rate, else unverified (`services/risk/engine.py`) |
| Risk has final veto; no martingale | `services/risk`: effective budget = min of all caps, shrinking after losses (property-tested); a lock on a confirmed plan → NO_TRADE; decision gets riskStatus + blockers only |
| Private account data | Manual profile in a git-ignored server-side file; errors list field paths only; calculator stores nothing (web test spies on localStorage) |
| Secrets server-side | `pydantic.SecretStr` settings; non-leak tests; `NEXT_PUBLIC_*` secret guardrail |
| One decision object everywhere | `MasterDecision` + field contract `packages/shared-types/contract/api_fields.json` tested in Python and TS; scanner rows are projections of each symbol's decision (`test_scan_endpoint_ranks_real_decisions`) |
| News blackout is a hard blocker | `services/news`: relevant-currency events → CLEAR/CAUTION/BLACKOUT/POST_NEWS_WAIT/NORMALIZED; UNAVAILABLE (blocks) when the calendar is missing, stale, short or invalid; synthetic calendars add NEWS_DATA_SYNTHETIC; BLACKOUT on a confirmed plan → NO_TRADE; the decision always carries `newsState` |
| Macro is context, never a gate | `services/macro`: DXY/yields/VIX trends × configured relationships → bias and state versus the setup; MACRO factor scored only on available, non-synthetic data for the setup's direction; conflict lowers confidence; no blockers, no verdict change (tested); unavailable → NOT_EVALUATED |
| Journal records, never authorizes | `services/journal`: manual entries with an engine snapshot captured at logging, SHA-256 hashed and re-verified on every read (TAMPERED blocks outcomes); no update path, outcomes are append-only revisions; `unconfigured` store saves nothing; detected rule violations and process class are deterministic; fill prices withheld from the AI unless sharing is enabled; web stores nothing |
| Paper is simulation only | `services/paper`: pure bar-by-bar engine on closed candles of the independent feed (no lookahead, assumed costs, conservative same-bar resolution), `authority: SIMULATION_ONLY`, hashed creation record + append-only hashed events, tampered sims stop advancing; no broker/venue/routing objects (guardrails + API surface test) |
| Statistics describe, never predict | `services/analytics`: verified closed records only (tampered excluded, synthetic opt-in), JOURNAL and PAPER never mixed, spec sample-size labels on every group, no "best" group below LIMITED, `authority: DESCRIPTIVE_ONLY` + disclaimer; web rejects inconsistent or authority-claiming reports |
| Backtests replay, never promise | `services/backtest`: for each closed M15 close the live `SetupService` runs as of that close (no lookahead by construction), plans only on their confirmation step and on decision-eligible data, fills by the paper engine; `authority: RESEARCH_ONLY`, gates-not-applied disclosures, hashed immutable results, one background run at a time |
| Replay never shows the future | `services/replay`: in-memory sessions with a cursor; candles, setup analysis and time context are computed with `now = cursor` through the live services; BLIND masks symbol/prices/dates and hides the engine until reveal, forward only; QUIZ grades a prediction only after the cursor moves past its horizon; `authority: EDUCATION_ONLY`; web rejects any candle past the cursor or a leaking blind state |
| AI explains, never decides | `services/assistant`: read-only tools over the same services; analysis withheld on ineligible data; symbol-scoped tools reject other markets; external answers published only if the grounding guard passes (verdict = decision, numbers > 20 grounded, no trade instruction, no foreign symbols), else deterministic fallback; create_alert is a proposal; account amounts withheld unless `AI_SHARE_ACCOUNT_DATA` |
| Alerts are not signals | `services/alerts`: market alerts only from eligible data, silent baseline, dedupe + cooldown; READY requires a LONG/SHORT decision AND FULL authority (tested unable to fire under FAIL_SAFE_ONLY); web rejects directional wording / READY without authority |
| XAUUSD first; asset contexts isolated | Gate adds `MARKET_NOT_VALIDATED` for non-validated instruments; scanner isolates each symbol (failure/wrong-symbol → that row only); web resets symbol-scoped state on switch and loaders reject other-symbol payloads |

## Data flow (as of Phase 19)

```
MarketDataProvider (unconfigured | fixture:synthetic | future vendor)
  └─ get_historical_bars(symbol, tf, end=now, limit=N) → RawBar[]      (bounded ingestion)
      └─ candles.normalize.build_series        (UTC, alignment incl. NY-close H4/D1, OHLC sanity, order,
          │                                      dedupe, gaps vs DST-aware market hours, spike flags)
          └─ CandleSeries{candles, issues, quality}
              ├─ market_state.gate.evaluate    (M5 execution TF; blockers → WAIT | UNAVAILABLE;
              │   │                                  MARKET_NOT_VALIDATED unless deeply validated)
              │   └─ MasterDecision ──► /market-state ──► web (reconcile / fail safe)
              │   └─ scanner.ScannerService (per symbol, isolated, 60 s row cache, attention ranking)
              │   └─ alerts.AlertService (lifespan monitor: new M5 bar | 5 min quiet → decision + evaluate_with_run
              │       │                   → rules vs state memory → store (dedupe/cooldown) + ready-watch checklist)
              │       └─ /alerts, /alerts/ready-watches ──► web alerts.ts ──► ALERTS view + event area
              │   └─ assistant.AssistantService (intent → read-only tools → deterministic facts/answer;
              │       │                        optional Anthropic tool loop → grounding guard → PASSED | FALLBACK)
              │       └─ /assistant/ask, /assistant/capabilities ──► web assistant.ts ──► AI_ANALYSIS tab
              │       └─ /scanner, /markets ──► web scanner.ts ──► Markets / Watchlist / Scanner views ──► open symbol
              │                     └─► EventBus market_state.decision_changed (on change only)
              ├─ analysis.pipeline.run_pipeline  (ONE path for API/chart/decision:
              │     structure → liquidity (+ NY-close D1 key levels) → DOL → displacement → FVG/IFVG
              │     → structure liquidity + displacement qualifiers; liquidity and PD stages fail independently)
              ├─ structure.engine.analyze_level (closed candles; pivots confirmed after L right candles;
              │   │                                BOS/CHoCH/MSS state machine; INTERNAL + EXTERNAL levels)
              │   └─ /structure, /structure/alignment ──► web structure.ts (validate + sync) ──► chart overlay
              │   └─ eligible only → MasterDecision.htfBias / structureEvent (enrichment, never the verdict)
              ├─ liquidity.engine.analyze_liquidity (sequential pools: swings, EQ clusters, PDH/PDL/PWH/PWL;
              │   │                                    TOUCH/SWEEP/BREAK/RUN/RECLAIM/SWEEP_FAILED; magnet score; DOL)
              │   └─ /liquidity ──► web liquidity.ts (validate + sync) ──► chart overlay
              │   └─ eligible only → MasterDecision.primaryDol / secondaryDol / liquidityEvent, + DOL_UNCLEAR (WAIT-class only)
              ├─ pd_arrays.displacement + pd_arrays.fvg (graded legs with pre-leg ATR; FVG fill/close-through
              │   │                                     invalidation; IFVG POTENTIAL → CONFIRMED | FAILED; quality)
              │   └─ /pd-arrays ──► web pdArrays.ts (validate + sync) ──► chart overlay (zone edges, displacement)
              │   └─ eligible only → MasterDecision.displacement (enrichment, never the verdict)
              ├─ news.NewsService (CalendarProvider: unconfigured | file | fixture(synthetic) → news gate per symbol)
              │   ├─ feeds the evaluation (no missing gates; BLACKOUT → NO_TRADE; CONFIRMED_AWAITING_AUTHORITY when clear),
              │   │  risk.news, alerts (countdown/blackout/cleared), ready watch, assistant get_news_state
              │   ├─ /news/{symbol}, /calendar ──► web news.ts ──► status bar News, MACRO tab, chart News markers
              │   └─ MasterDecision.newsState + NEWS_* blockers (also when market data is unusable)
              ├─ journal.JournalService (JournalStore: unconfigured | database (Postgres / SQLite via Alembic 0002))
              │   ├─ create: MarketState decision + evaluation + data report → immutable hashed snapshot, detected violations
              │   ├─ outcomes: R / MFE-MAE (manual or M5→M15→H1 candles) / result / process class, append-only revisions
              │   ├─ /journal/status, /journal/entries (+ /{id}, /{id}/outcomes, DELETE), /journal/export ──► web journal.ts ──► Journal view
              │   └─ assistant query_journal (symbol-scoped; fill prices withheld unless shared)
              ├─ backtest.BacktestService (BacktestStore: unconfigured | database (Alembic 0004); background job)
              │   ├─ history: CandleService M15 step closes + M5 execution bars (chunked, validated)
              │   ├─ per step: SetupService.run(now = close) → new confirmed plan → paper engine (one position)
              │   ├─ summary: analytics stats + labels, segments, in/out-of-sample, seeded Monte Carlo, disclosures
              │   └─ /backtests (+ /status, /{id}, /{id}/cancel, DELETE) ──► web backtest.ts ──► Backtest view; assistant run_backtest (read-only)
              ├─ replay.ReplayService (in memory: ≤ 20 sessions, 6 h idle TTL; cursor = a closed candle close)
              │   ├─ state as of cursor: CandleService closed bars ≤ cursor, SetupService.run(now = cursor), session clock
              │   ├─ modes MANUAL / GUIDED (tutor narrative + events since last step) / BLIND (mask, forward only, reveal) / QUIZ (graded after reveal)
              │   └─ /replay/sessions (+ /{id}, /{id}/step, /{id}/answer, /{id}/end, DELETE) ──► web replay.ts ──► Replay view
              ├─ analytics.AnalyticsService (reads journal export / paper sims → Sample[] → pure engine: group stats,
              │   │                           drawdown, breakdowns, process, DOL accuracy; labels; DESCRIPTIVE_ONLY)
              │   ├─ /analytics ──► web analytics.ts ──► Journal view Analytics tab
              │   └─ assistant query_journal statistics (label + disclaimer)
              ├─ paper.PaperService (PaperStore: unconfigured | database (Alembic 0003); monitor + advance-on-read)
              │   ├─ create: shared engine capture + reference price + assumed costs → hashed record, CREATED event
              │   ├─ engine.simulate: closed M5 bars after creation → FILLED / STOP_HIT / TARGET_HIT / EXPIRED (append-only)
              │   ├─ close (last closed bar) / cancel / delete; result via journal compute_outcome (+ net R after commission)
              │   └─ /paper/status, /paper/sims (+ /{id}, /{id}/close, /{id}/cancel, DELETE) ──► web paper.ts ──► Journal view Paper tab
              ├─ macro.MacroService (MacroProvider: unconfigured | file | fixture(synthetic) daily DXY/US2Y/US10Y/US10Y_REAL/VIX;
              │   │                 + market D1 closes (DXY correlation regime) + released USD surprises from news)
              │   ├─ feeds the evaluation: MACRO factor (state vs setup direction) + macro conflict; never blockers
              │   ├─ alerts MACRO_SHIFT (bias change), assistant get_macro_state
              │   ├─ /macro/{symbol}?direction, /macro/series ──► web macro.ts ──► status bar Macro, MACRO tab
              │   └─ MasterDecision.macroState (CONTEXT_ONLY; also when market data is unusable)
              ├─ risk.RiskService (server-side manual profile file ─► limits, budget, locks, volatility (M15 of the
              │   │                setup run), sizing of the BLOCKED plan; never guessed; NOT_AUTHORIZED)
              │   ├─ feeds the evaluation: missing gates NEWS only; lock on a confirmed plan → NO_TRADE
              │   ├─ /risk/{symbol} (same run) and stateless POST /risk/calculate ──► web risk.ts ──► RISK tab
              │   └─ MasterDecision.riskStatus + risk blockers (WAIT-class; never a size or prices)
              ├─ scoring.EvaluationService (setup run reused: score / grade / confidence / conflict / warnings /
              │   │                           devil's advocate / outcome; authority NOT_AUTHORIZED)
              │   └─ /evaluation ──► web evaluation.ts (validate) ──► ENTRY tab, status bar Score
              │   └─ eligible only → MasterDecision.setupScore / setupGrade / confidence (capped) + WAIT-class blockers
              ├─ setup_state (+ entry: M15_CLOSE / NO_WICK_REBALANCE / LTF_REFINEMENT (M5) / RETEST / LIMIT_RESEARCH;
              │               plan + chase protection → BLOCKED (pending risk check + news gate) | ENTRY_MISSED)
              ├─ setup_state (M15 pipeline output + H4/H1 external BOS/MSS bias timeline + session opens/ADR:
              │   │           DISCOVERED→…→WAITING_FOR_CONFIRMATION | INVALIDATED | EXPIRED; PO3; never READY)
              │   └─ /setups ──► web setups.ts (validate + sync) ──► OVERVIEW setup progress, Setup overlay
              │   └─ eligible only → MasterDecision.setupState / setupType (authority guard rejects READY)
              ├─ sessions (clock: NY-time windows, kill zones, time quality - time only; levels from closed M15:
              │   │         instances, opens, previous session, Asian range, ADR/expansion, Judas swings)
              │   ├─ COMPLETE session highs/lows ──► liquidity pools (known at window end)
              │   ├─ candle time quality ──► No Wick SESSION component (M5/M15/H1)
              │   └─ /sessions ──► web sessions.ts (validate) ──► status bar, SESSION tab, session segments
              │   └─ eligible only → MasterDecision.sessionState (CONTEXT_ONLY, never the verdict)
              ├─ no_wick.engine (features with prior-candle ATR/medians; 12 classifications; strength;
              │   │               quality / context components / relevance; rebalance zones)
              │   └─ /no-wick ──► web noWick.ts (validate + sync) ──► chart overlay (NW markers, zone edges)
              │   └─ eligible only → MasterDecision.noWickState (CONTEXT_ONLY, never the verdict)
              └─ candles.service.chart_series  (M5/M15/H1 native; H4/D1 = aggregate(H1) in NY-close buckets;
                  │                              INVALID/DISCONNECTED → candles withheld)
                  └─ /candles ──► web candles.ts (validate) ──► Lightweight Charts (render only)
timeframes.core: UTC buckets (M1..H1), New York 17:00 buckets (H4/D1), DST-safe stepping
db: instruments / candles / data_quality_events (closed history never silently rewritten) / journal_entries / journal_outcomes (records never updated) / paper_sims / paper_events (creation record and events never updated) / backtest_runs (completed results never updated)
```

## Service boundaries

Present: `data_quality`, `candles` (normalize + shared series loader + chart series), `timeframes`, `market_state`, `events`, `structure`, `liquidity`, `pd_arrays` (displacement + FVG/IFVG), `no_wick`, `sessions`, `setup_state`, `entry`, `risk`, `scoring`, `scanner`, `alerts`, `assistant`, `news`, `macro`, `journal`, `paper`, `analytics`, `backtest`, `replay`, `analysis` (shared pipeline).
Reserved for later phases: see master spec STEP 15 (not created yet to avoid premature logic).

## Source-of-truth files

- `packages/strategy-spec/*.json`: enums, thresholds, market hours, instruments, risk presets and hard limits, strategy version.
- `packages/strategy-spec/scanner.json`: scan cache, max symbols, setup progress order.
- `packages/strategy-spec/news.json`: relevant currencies, blackout / post-wait / caution windows, calendar trust limits, countdowns.
- `packages/strategy-spec/replay.json`: timeframes, window bars, max step, quiz horizon and ATR period, session limits, blind mask ranges.
- `packages/strategy-spec/backtest.json`: range, variants, segments, Monte Carlo, progress and cost-multiplier limits.
- `packages/strategy-spec/analytics.json`: sample-size label thresholds, best-group minimum label, equity curve size, disclaimer.
- `packages/strategy-spec/paper.json`: execution timeframe, pending expiry, advance limits, assumed costs per symbol (assumptions, not broker specs).
- `packages/strategy-spec/journal.json`: result tolerances, chase tolerance, post-entry snapshot window, candle-extreme limits, list limits.
- `packages/strategy-spec/macro.json`: series trend thresholds, required series, bias/state thresholds, correlation regime, USD surprise keywords, drivers per market.
- `config/macro.example.json`: template for the server-side macro series file (`MACRO_FILE_PATH`; example values only).
- `config/calendar.example.json`: template for the server-side calendar file (`CALENDAR_FILE_PATH`).
- `packages/strategy-spec/alerts.json`: monitored symbols, cadence, thresholds, cooldowns, category/priority per alert type.
- `config/risk_profile.example.json`: template for the private, git-ignored manual account profile (`RISK_PROFILE_PATH`).
- `packages/shared-types/contract/api_fields.json`: wire field names.
