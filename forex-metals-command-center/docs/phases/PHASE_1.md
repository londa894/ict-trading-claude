# Phase 1 — XAUUSD Chart Shell

## GOAL
Show validated XAUUSD candles on M5 / M15 / H1 / H4 / D1 in the STEP 11 desktop layout, with
TradingView Lightweight Charts as a pure renderer. The chart must never draw untrusted prices and
must never influence the verdict. No structure, liquidity, session or other analysis overlays.

## INPUTS
- `MarketDataProvider.get_historical_bars(symbol, timeframe, end=now, limit=N)`. `limit` was added in Phase 1 and means "most recent N bars before `end`"; ingestion is bounded.
- Clock (UTC), instrument registry, `strategy-spec` thresholds and market hours.
- Browser: `GET /api/v1/candles/{symbol}?timeframe=&limit=` and the Phase 0 market-state/status endpoints.

## RULES
1. Chart timeframes: M5, M15, H1 (fetched natively from the provider) and H4, D1 (derived from validated H1). Anything else → HTTP 422.
2. **Canonical higher-timeframe buckets (New York close):** D1 = 17:00→17:00 New York, DST-aware. H4 buckets start at 17, 21, 01, 05, 09 and 13 New York time. Vendor daily candles with other conventions (e.g. UTC midnight) are never used; an H4/D1 bar that isn't aligned this way is `MISALIGNED_OPEN_TIME` → INVALID.
3. Bucket length is asserted: a bucket containing bars must be exactly 4h/24h long, otherwise an `AggregationError` is raised (fail-safe). US DST changes happen on Sundays while the market is closed, so this holds.
4. Staleness and gap detection for H4/D1 use the same market-hours rule as intraday. Only buckets containing open market time count. W1/MN1 are unsupported and fail safe.
5. Ingestion window = `(limit + 1) × ratio + 25` source bars ending at `now`. The extra bucket lets the partial left-edge derived bucket be dropped. The 25 warm-up bars keep spike detection working on the oldest returned candles.
6. INVALID or DISCONNECTED series → `candles: []`, issues only. STALE/DELAYED series are drawn, with a warning banner.
7. The forming bucket is returned with `isClosed=false` and is not an issue. A past bucket with missing constituents → `INCOMPLETE_BUCKET` WARNING.
8. No lookahead: providers get `end=now`, and bars opening after `now` are still rejected by validation (a provider that ignores `end` is caught).
9. The client rejects candle payloads with a symbol/timeframe mismatch, unknown quality, non-ISO-UTC time, non-ascending or duplicate times, non-finite/non-positive prices, impossible OHLC, negative volume, a forming candle that isn't last, or candles attached to non-drawable quality.
10. The chart has no data path of its own: Lightweight Charts only receives validated arrays. The verdict still comes from the M5 Master Decision alone, and switching the chart timeframe never changes it (tested).
11. The fixture provider generates a single M5 path; M15/H1 are aggregated from it so every timeframe agrees. Its window (2024-02-25 → 2024-04-19) spans the US DST change.

## OUTPUTS
- `ChartSeriesResponse {symbol, timeframe, sourceTimeframe, provider, isSynthetic, quality, marketStatus, candles[{time, open, high, low, close, volume, isClosed}], issues, providerError, strategyVersion, generatedAt}`
- Web shell: top status bar, left nav, chart panel, right intelligence panel (10 tabs), bottom session-local event log.

## STATES
- Chart panel: LOADING / READY / UNAVAILABLE (with reason)
- Series quality: CURRENT / DELAYED / STALE (drawn); INVALID / DISCONNECTED (withheld)
- Candle: `isClosed` true/false (forming)
- Verdict: unchanged, `FAIL_SAFE_ONLY` (WAIT / UNAVAILABLE)

## INVALIDATION
No setups exist yet. A series is invalidated by any validation ERROR (withheld), and a client payload by any client-side check in rule 9.

## ERROR STATES
| Condition | Result |
|---|---|
| Unsupported timeframe / limit out of 1..1000 | HTTP 422 |
| Unknown symbol | HTTP 404 |
| Provider down / raises / incompatible | 200, `quality=DISCONNECTED`, `candles=[]`, sanitized `providerError` |
| Validation ERROR in source series | 200, `quality=INVALID`, `candles=[]`, issues listed |
| API unreachable / HTTP error / malformed payload (web) | Chart overlay "CHART DATA UNAVAILABLE"; renderer not mounted |

## UNIT TESTS
- `services/api/tests/unit/test_trading_day_buckets.py`: roll time winter/summer, H4 wall-clock hours before and after DST, realignment across the DST weekend, UTC-midnight D1 rejected, D1 expected slots skip the weekend, H1→D1/H4 values, incomplete vs forming buckets, DST-Sunday bucket is 24h.
- `test_quality.py` (D1 market-hours staleness, W1 fail-safe), `test_aggregate.py`, `test_api_field_contract.py` (chart fields).
- `apps/web/tests/candles.test.ts`: payload validation matrix, renderer mapping, loader URL and HTTP failures.
- `apps/web/tests/shell.test.tsx` (jsdom): renderer mounted only for READY, overlay otherwise, synthetic/stale banners, timeframe switching, status bar shows N/A instead of made-up values, unbuilt tabs show no numbers, nav enables only implemented surfaces.
- `packages/shared-types/tests/fields.test.ts`: ChartCandle/ChartSeriesResponse/CHART_TIMEFRAMES contract.

## INTEGRATION TESTS
- `services/api/tests/integration/test_candles.py`: every chart timeframe aligned/ascending/sane/no lookahead; **cross-timeframe consistency** (H4/D1/H1/M15 OHLC equal the M5 bars inside them); limit returns the most recent candles; D1 opens always 17:00 New York across DST; forming candle mid-window; INVALID withheld; STALE drawn; 4 provider-failure modes; request validation; chart timeframe never changes the decision; bounded provider request; incompatible provider fails safe.
- Manual browser check (2026-09-13): API (fixture) + `next start` at 1600×900. Full layout rendered; M5 and D1 charts drawn with synthetic + STALE banners; D1 last close = M5 last close; stopping the API → "CHART DATA UNAVAILABLE", last close UNAVAILABLE, verdict UNAVAILABLE / PROVIDER_UNAVAILABLE.

## UI DISPLAY
- **Top bar:** symbol, last close (selected TF), daily change / spread / session / score / news shown as `N/A · Phase N`, HTF bias, verdict, confidence, risk, data quality (M5), market status.
- **Left nav:** Command Center and Chart enabled; all other surfaces disabled with their phase number.
- **Chart:** TF switcher, quality badge, "Times in UTC", NY-close note for H4/D1, candlesticks + volume.
- **Right panel:** OVERVIEW (why not yet, decision, data, system). STRUCTURE…AI_ANALYSIS show NOT AVAILABLE.
- **Bottom:** session-local decision-change and data-issue log (alerts engine is Phase 11).
- Narrow screens: a basic single-column stack (a full mobile UI is Phase 20+).

## KNOWN LIMITATIONS
- Still no real data vendor, so the chart shows synthetic data until one is chosen and an adapter is written.
- Updates come from polling every 15 s. There is no WebSocket/Redis live stream yet, so intra-bar updates are not pushed.
- Chart times are shown in UTC only. New York / London / user-timezone display is Phase 6.
- No overlays (sessions, structure, liquidity, FVG…). Those arrive with their engines.
- Fixture provider: first M15/H1 request per process takes ~1.3 s while the cache builds. Warm requests are ~40–150 ms.
- D1 ingestion needs `limit × 24` H1 bars; very large D1 limits are proportionally heavier.
- Web tests run files sequentially (`fileParallelism: false`) because jsdom's cold import takes ~14 s on the OneDrive-synced folder.
- Lightweight Charts shows its TradingView attribution logo (required by the library's licence).
- Inherited from Phase 0: Docker/Postgres not run locally, CI not run, holidays not modelled, no auth.

## STRATEGY VERSION
`0.1.0-phase1`, verdict authority `FAIL_SAFE_ONLY`.
