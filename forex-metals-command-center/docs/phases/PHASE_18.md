# Phase 18 — Backtesting

## GOAL
Build backtesting V1 (spec STEP 10 backtesting):
- sequential historical processing only, no lookahead, candle-close integrity, correct session/DST timing;
- assumed spread, slippage and commission; intrabar ambiguity disclosed;
- A/B variants, out-of-sample split, segment (walk-forward style) stability, seeded Monte Carlo;
- strategy versioning — all using the **same strategy code** as live (spec: "same strategy code for LIVE / PAPER / REPLAY / BACKTEST").

A backtest is a background research job with `authority: RESEARCH_ONLY`. It never authorizes, forecasts or promises anything. Verdict authority stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **No parameter optimisation.** No grid search, no fitting: segments and in/out-of-sample splits show stability of fixed rules.
- **Not applied:** risk locks, the news gate and verdict authority. There is no historical account state or calendar; results describe confirmed plans, not authorized trades.
- **Not built:** replay modes (Phase 19), tick data, partial exits, trailing stops, money P/L, multi-symbol portfolios, concurrent positions.
- **One run at a time, in process**, with no distributed workers.
- **Fixture data finding:** the synthetic fixture history contains **no confirmed plans** (98 samples across Feb 25 – Apr 19 2024 found 0). Backtests on it report 0 trades plus a setup funnel. Nothing is faked.

## INPUTS
- **Request** (`POST /api/v1/backtests`):
  - symbol; `start` and `end` (UTC, end not in the future, ≤ 14 days);
  - 1–2 variants (name, entry mode CONSERVATIVE / STANDARD / AGGRESSIVE, cost multiplier 0–5);
  - segments 1–6; optional `outOfSampleFrom` inside the range.
- **History:** closed setup-timeframe (M15) candles for the step times and closed execution (M5) candles for fills, loaded backwards in ≤ 1000-bar chunks through the CandleService (validated, normalized). INVALID/DISCONNECTED history fails the run.
- **Engine:** `SetupService.run(symbol, now = step close)` with `EntryConfig.from_spec(variant mode)`, the live code path. Paper engine (Phase 16) for fills. Analytics (Phase 17) for statistics and labels.
- **Config (new):** `packages/strategy-spec/backtest.json`: max range 14 days, max 2 variants, max 6 segments, one concurrent position, Monte Carlo 1000 resamples with seed 18, progress every 10 steps, max cost multiplier 5.
- **Settings:** `BACKTEST_STORE` (`unconfigured` default | `database`) and `DATABASE_URL`. Table `backtest_runs` (Alembic `0004_phase18`).

## RULES
1. **Sequential replay:**
   - for every closed M15 candle close t in the range (ascending), advance the open simulation over closed M5 bars that closed by t, then call the live setup analysis **as of t**;
   - nothing after t is visible, and weekend or closed-market times produce no steps.
2. **Taking plans:**
   - a plan counts only on the step where it became confirmed (`confirmedAt + M15 == t`), once per (setup, confirmation);
   - only on data eligible for a decision (ineligible steps are counted);
   - plans first seen later are never traded (no late entries).
3. **Simulation** (paper engine):
   - LIMIT at the plan entry created at t, plan stop, TP1, pending expiry 12 bars;
   - assumed costs × the variant multiplier; spread-side fills; gap handling;
   - same-bar stop+target → stop (ambiguous); a limit fill bar only allows the stop.
4. **One position at a time:**
   - plans during a pending or open position are SKIPPED_OVERLAP;
   - a position still active at the end is OPEN_AT_END (excluded from statistics).
5. **Funnel:** steps, ineligible steps with their analysis ineligibility reasons (e.g. DATA_SYNTHETIC), setups discovered, states reached, plans confirmed, skipped, fills, expired, closed, open at end. Every confirmed plan ends as exactly one of CLOSED / EXPIRED / SKIPPED_OVERLAP / OPEN_AT_END.
6. **Results per variant:**
   - outcome via the journal's `compute_outcome`: result state, R after spread and slippage, net R after commission, MFE/MAE R;
   - analytics group stats with sample-size labels; drawdown; equity curve;
   - breakdowns by direction, entry model, setup type, result and day (no "best" below LIMITED);
   - N equal time segments; IN_SAMPLE vs OUT_OF_SAMPLE when a split is given;
   - ambiguous trade count.
7. **Monte Carlo** (≥ 2 closed trades): seeded bootstrap with replacement of trade R (1000 resamples) giving total R p5/p50/p95 and max drawdown p50/p95, labelled by sample size and described as sequence sensitivity, not future results.
8. **Disclosures** are always attached: sequential replay, bar-level fills and ambiguity, assumed costs, gates not applied, session/DST timing, no fitting, one position, not a forecast (plus a SYNTHETIC data warning, and a per-variant note when at least half of the steps were ineligible, with the reasons). Also a config hash and the strategy version.
9. **Jobs:**
   - QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED;
   - progress is stored every 10 steps; a second start while one runs → 409; cancel is checked each step;
   - a run found QUEUED/RUNNING that this process does not own reads as FAILED ("interrupted: the API restarted");
   - the completed result is SHA-256 hashed with the request and never updated (SQL guard). A mismatch → TAMPERED with the result withheld;
   - a verified result that no longer parses (older format) → UNREADABLE, reported instead of failing the request.
10. **Assistant:** `run_backtest` is now available as a **read-only** summary of the latest completed run for the market (variants, funnel counts, labelled stats, disclosures). The BACKTEST answer states it as a research replay with the gates-not-applied caveat and UNKNOWN edge for INSUFFICIENT samples. The assistant never starts runs.

## OUTPUTS
- `GET /api/v1/backtests/status` → `BacktestStoreInfo` (available, backend, reason, running id).
- `GET /api/v1/backtests` → `BacktestListResponse` (`BacktestRunRow`: range, status, pct, variants, closed trades and net total R per variant).
- `POST /api/v1/backtests` → 202 `BacktestRun`; `GET /api/v1/backtests/{id}`; `POST /api/v1/backtests/{id}/cancel`; `DELETE /api/v1/backtests/{id}` → 204.
- `BacktestRun`: status, request, progress, error, result (variants, data, disclosures, config hash), timestamps, integrity, authority RESEARCH_ONLY, strategy version.
- Enums: `BacktestStatus`, `BacktestTradeStatus`.
- Contract fields: `BacktestVariant`, `BacktestRequest`, `BacktestTrade`, `Funnel`, `SegmentStats`, `MonteCarlo`, `VariantResult`, `BacktestData`, `BacktestResult`, `BacktestProgress`, `BacktestRun`, `BacktestRunRow`, `BacktestStoreInfo`, `BacktestListResponse`.

## STATES
- Run: QUEUED / RUNNING / COMPLETED / FAILED / CANCELLED (interrupted runs read FAILED).
- Trade: CLOSED / EXPIRED / SKIPPED_OVERLAP / OPEN_AT_END.
- Result integrity: VERIFIED / TAMPERED / UNREADABLE / NOT_APPLICABLE.

## INVALIDATION
- A completed run is frozen (hash-verified).
- A changed engine or config produces a different strategy version / config hash. Old runs keep theirs and are not recomputed.

## ERROR STATES
| Condition | Result |
|---|---|
| `BACKTEST_STORE=unconfigured` (default) | status unavailable; list empty; start 503 |
| Tables missing / DB unreachable | unavailable with the reason |
| End in the future; range > 14 days; bad split, variants or segments | 422 |
| Unknown symbol / run; malformed id | 404; 422 |
| Another run active | 409 |
| History INVALID/DISCONNECTED or none in range | FAILED with a safe reason |
| Unexpected exception | FAILED ("backtest failed (ErrorType)") |
| Cancel on a finished run; delete while running | 422 |
| Web: authority other than RESEARCH_ONLY, result on an unfinished run, completed without result, missing disclosures, funnel not accounting for every plan, stats count ≠ closed trades, label ≠ count, Monte Carlo percentiles out of order, runs from an unavailable store | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_backtest_engine.py` (11), with a scripted plan source:
- request validation (range, split, unique names, max variants and segments, cost multiplier); config guard;
- a plan is taken only on its confirmation step and filled by later bars (sequential steps);
- **late visibility is never traded**; ineligible steps take no plans;
- one position at a time with SKIPPED_OVERLAP, EXPIRED and OPEN_AT_END;
- cost multiplier scaling, commission in net R and ambiguity flag;
- progress callbacks and cancellation;
- summary with segments, in/out-of-sample split, deterministic seeded Monte Carlo and breakdown dimensions;
- ineligibility reasons counted.

## INTEGRATION TESTS
`tests/integration/test_backtest_api.py` (19):
- unconfigured store;
- **real replay of the live engine** over 8 hours with two variants (409 for a second run; COMPLETED, VERIFIED, 100%, funnel accounting, INSUFFICIENT labels, segments, in/out-of-sample, disclosures, list row);
- validation (future end, range too long, unknown symbol), cancel → CANCELLED, **tampered result withheld**, delete;
- interrupted runs reported FAILED after a restart;
- **a stored result in an older format is reported UNREADABLE and the list keeps working**;
- 14 contract-field cases.

Updated: API surface test (backtest POST/DELETE), Alembic migration test, assistant capabilities (run_backtest available) and BACKTEST answer, left-nav test, version strings.

Web `tests/backtest.test.tsx` (15): trust rules (3 accepted, 9 rejected); list refusal and request building (A/B, segments, split errors); `#backtest` unconfigured explanation; completed run detail (research label, synthetic disclosure, funnel, Monte Carlo, trades); start → polling progress → completion; cancel while running; delete requiring confirmation.

## UI DISPLAY
- **Left nav Backtest** (`#backtest`) opens the Backtest view:
  - research-replay note;
  - store status with setup instructions;
  - form: market, variant A entry mode, from/to local time, segments, optional out-of-sample start, "Compare with variant B" (entry mode, cost multiplier); start is disabled while a run is active;
  - runs table: created, market, range, status/pct, variants, closed / total R per variant.
- **Run detail:**
  - header with status and RESEARCH_ONLY; progress (polled) while active; error; tampered warning;
  - disclosures (SYNTHETIC first when applicable); data line (provider, bars, config hash, version);
  - per variant: funnel, states reached, labelled statistics, drawdown, ambiguous count, in/out-of-sample, Monte Carlo percentiles, segment stability table, trades table (confirmed, plan, status, fill → exit, result, net R);
  - Cancel (active) / Delete (with confirmation).

## KNOWN LIMITATIONS
- ⚠️ **No evidence of an edge.** The live fixture feed is synthetic, so every step is ineligible (DATA_SYNTHETIC) and no plans are taken. Even the non-synthetic test stub over the same history yields no confirmed plans. Backtests currently report funnels, not trades. Any real conclusion needs a licensed, validated historical feed and 30+ (ideally 300+) closed trades.
- ⚠️ **Bar-level simulation with assumed costs.** No ticks, no spreads from history, no requotes or liquidity limits. Same-bar ambiguity resolves conservatively; limit fills at touch may be optimistic in fast markets.
- ⚠️ **Gates not applied.** Risk locks and news blackouts would have blocked some plans live.
- **Performance:** about 0.25 s per step with a warm cache (the fixture feed). A 14-day run is roughly 1,300 M15 steps per variant (several minutes). One run at a time, in process; a restart interrupts the run.
- The provider's history is re-requested per step through the CandleService (correct but not optimised). Real vendors may rate-limit.
- No authentication (inherited); tamper evidence only; Postgres not run.

## STRATEGY VERSION
`0.18.0-phase18` (phase 18, "Backtesting"). Engines: + `backtest`. Verdict authority: **FAIL_SAFE_ONLY**.
