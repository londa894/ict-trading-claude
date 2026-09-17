# Phase 0 — Repository / Foundation / Data Architecture

## GOAL
Create a trustworthy foundation: repository layout, provider abstraction, normalized candle model,
data validation and quality classification, a fail-safe Master Decision gate, persistence schema,
a web shell that cannot display an untrusted decision, tests, and CI. No trading analysis.

## INPUTS
- Raw bars (`RawBar`) and quotes from a `MarketDataProvider`.
- Wall clock (`now`, UTC; injectable for tests).
- `packages/strategy-spec`: enums, `data_quality.json`, `market_hours.json`, `instruments.json`, `strategy_version.json`.
- Env settings (`MARKET_DATA_PROVIDER`, `MARKET_DATA_API_KEY`, `DATABASE_URL`, …).

## RULES
1. All timestamps are UTC. Naive timestamps are rejected (their zone can't be known). Aware non-UTC timestamps are converted to UTC.
2. Fixed intraday bars (M1–H1) must start on UTC-aligned boundaries.
3. OHLC sanity: prices > 0, finite, and high ≥ max(open, close), low ≤ min(open, close). Violations → ERROR, and the bar is excluded.
4. Zero-range bar → WARNING.
5. Out-of-order input → WARNING, then re-sorted.
6. Identical duplicate → WARNING, dropped. Conflicting duplicates → ERROR, all copies excluded.
7. Missing bars are counted only when the market is open (DST-aware New York hours: FX Sun 17:00–Fri 17:00; metals Sun 18:00–Fri 17:00 with a 17:00–18:00 daily break). They are **never filled**.
8. Range spike > 15× the median of the previous 20 closed ranges (min 10 samples) → `SUSPECT_BAD_TICK` WARNING. Only past candles are used (no lookahead).
9. Quotes: crossed quote → ERROR. Jump beyond the per-asset-class % from the reference → ERROR. Wide spread → WARNING. Two providers disagreeing beyond the threshold → ERROR.
10. Series quality: any ERROR → INVALID. Otherwise count the market-open bars missing after the last closed candle (with a 15 s grace): 0 → CURRENT, ≤2 → DELAYED, more → STALE. H4+ uses raw elapsed periods (fail-safe). Historical candles are at most CURRENT; LIVE is reserved for streaming quotes.
11. Aggregation uses closed candles only. A bucket is closed only when all market-open constituents are present and closed.
12. Decision gate (authority `FAIL_SAFE_ONLY`): any unusable-data blocker → UNAVAILABLE, otherwise WAIT. `ANALYSIS_GATES_NOT_IMPLEMENTED` is always present. Any LONG/SHORT/NO_TRADE is converted to UNAVAILABLE + `SYSTEM_INTEGRITY_FAILURE`.
13. Synthetic data (`DATA_SYNTHETIC`) always gives UNAVAILABLE.
14. Persistence: a closed candle is never silently rewritten. A revision is rejected and logged as a `DUPLICATE_CONFLICT` event.
15. The registry rejects TradingView as a data provider.

## OUTPUTS
- `CandleSeries {candles, issues, quality}`
- `MasterDecision` (spec STEP 18 shape) + `DataReport`
- Domain event `market_state.decision_changed`, emitted only when the verdict, quality or blockers change
- API: `GET /health`, `/api/v1/system/status`, `/api/v1/instruments`, `/api/v1/providers/health`, `/api/v1/market-state/{symbol}`

## STATES
- DataQuality: LIVE / CURRENT / DELAYED / STALE / DISCONNECTED / INVALID
- MarketStatus: OPEN / CLOSED / DAILY_BREAK / UNKNOWN
- Verdict emitted in Phase 0: WAIT / UNAVAILABLE only
- PositionSizeStatus: POSITION_SIZE_UNVERIFIED (every instrument)

## INVALIDATION
No setups exist in Phase 0. At the data level, a series is invalidated by any ERROR issue, and a decision is invalidated whenever its inputs change (re-evaluated on each request).

## ERROR STATES
| Condition | Result |
|---|---|
| Provider unconfigured/down/raises | UNAVAILABLE, `PROVIDER_UNAVAILABLE`, `DATA_DISCONNECTED` |
| Unknown symbol | UNAVAILABLE, `UNKNOWN_SYMBOL` |
| Empty series | UNAVAILABLE, `NO_DATA` |
| ERROR issue in series | UNAVAILABLE, `DATA_INVALID` |
| Stale data | UNAVAILABLE, `DATA_STALE` |
| Synthetic provider | UNAVAILABLE, `DATA_SYNTHETIC` |
| Delayed / gap / spike / market closed | WAIT with the matching blocker |
| Unhandled server exception | HTTP 500 `{verdict: UNAVAILABLE, blockers:[SYSTEM_INTEGRITY_FAILURE]}` with no internals leaked |
| Web: API unreachable / malformed / symbol mismatch / forbidden verdict | UNAVAILABLE rendered, payload marked untrusted |

## UNIT TESTS
`services/api/tests/unit/`: contracts, market hours (DST), models, validation, normalization, quality, aggregation, gate (exhaustive no-directional-verdict sweep), providers/registry, event bus, API field contract.
`packages/shared-types/tests/`: enum contract, field contract (compile-time exhaustive + runtime).
`apps/web/tests/failsafe.test.ts`: reconcile + loader fail-safe behaviour.

## INTEGRATION TESTS
`services/api/tests/integration/`: HTTP API (no secret leakage, read-only surface, fail-safe errors), market-state service (publish-on-change, isolated symbols), persistence (SQLite upsert rules, UTC round-trip, Alembic upgrade/downgrade matches models).
Manual smoke (2026-09-13): uvicorn with the fixture provider plus `next start`. The UI showed UNAVAILABLE with synthetic/stale/market-closed blockers, and switched to UNAVAILABLE/PROVIDER_UNAVAILABLE after the API was stopped.

## UI DISPLAY
Single foundation page (`apps/web/src/app/page.tsx`): top status bar (symbol, verdict, confidence, data quality, market status, HTF bias, risk, strategy version), synthetic-data banner, "Why not yet" blockers + next required event, data report with issues, and system panel (phase, verdict authority, broker NONE, execution NONE). There is no chart yet; that is Phase 1.

## KNOWN LIMITATIONS
- No real market-data vendor yet. Only `unconfigured` (fails safe) and `fixture` (synthetic) exist.
- Holidays and provider-specific early closes are not modelled. They show up as `MISSING_BARS` warnings (the fail-safe direction).
- ~~H4/D1 bucket boundaries not modelled~~ — superseded in Phase 1 (New York 17:00 buckets). W1/MN1 remain unsupported.
- Redis event bus not wired. The in-memory bus implements the same interface.
- Persistence layer is not wired into the request path yet. The schema and repository are tested on SQLite; PostgreSQL has not been run locally (Docker unavailable on the build machine).
- Docker images and compose file are written but have not been built or run locally.
- No auth yet (V1 must-have; scheduled with user data features).
- Bad-tick thresholds are research defaults, not calibrated on real XAUUSD data.

## STRATEGY VERSION
`0.0.0-phase0`, verdict authority `FAIL_SAFE_ONLY`.
