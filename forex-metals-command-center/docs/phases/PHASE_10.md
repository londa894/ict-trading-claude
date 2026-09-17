# Phase 10 — Watchlist & Scanner

## GOAL
Let the user see every catalog market at once and move between them safely (spec STEP 11 left nav: Markets, Watchlist, Scanner; STEP 14 `scanMarkets`):
- **Markets:** the instrument catalog with validation, market-hours and contract-spec status;
- **Watchlist:** the user's chosen subset of markets, with each market's live decision;
- **Scanner:** every market's Master Decision, isolated per symbol and ranked for attention.

Opening a market switches the whole command center to that symbol without mixing contexts.

**Verdict authority stays `FAIL_SAFE_ONLY`.** A scan is never a trade signal. Every row is that market's own Master Decision, with its own verdict and blockers, so the scanner, the command center and any later AI surface cannot disagree (spec STEP 18).

## SCOPE BOUNDARY
- **Selected crosses** (spec STEP 1 "then selected crosses") are not added: the catalog stays at the 9 instruments.
- **Real-time streaming** is not built: the scanner polls (60 s while a scan view is open) and each row is cached for 60 s.
- **Alerts on scanner changes** (Phase 11) and **AI `scanMarkets`** (Phase 12) are not built; the endpoint is ready for them.
- **Server-side, per-user watchlists** need auth and a database (V1 items). The watchlist is a per-browser display preference.
- **TradingView Desktop:** opening a market in the command center does not change the TradingView Desktop symbol (the bridge only switches timeframes), so its connected label can still read XAUUSD.
- **Validating other markets** (tuning ATR multipliers, sessions and entry parameters per market) is not in scope. Non-XAUUSD markets are research only.

## INPUTS
- The Phase 0 instrument catalog (`instruments.json`: asset class, priority, `deeplyValidated`, spec).
- Market hours (`market_hours.json`) at the request time.
- The Master Decision of each symbol from `MarketStateService.evaluate`: the same pipeline, enrichments (structure, liquidity, displacement, no wick, sessions, evaluation, risk) and authority guard as `/market-state`.
- **Config (new):** `packages/strategy-spec/scanner.json`: `cacheSeconds` 60, `maxSymbols` 12, `setupProgress` (the ordered open-setup states, DISCOVERED … WAITING_FOR_CONFIRMATION, BLOCKED).
- Watchlist: browser `localStorage` key `fmcc.watchlist.v1` (symbols only).

## RULES
1. **Research-only markets.** The decision gate adds the WAIT-class blocker `MARKET_NOT_VALIDATED` for every instrument whose `deeplyValidated` is false (all but XAUUSD). Strategy parameters are validated on XAUUSD first; other markets run the same engines but stay blocked and are labelled RESEARCH ONLY in every view.
2. **Scan = decisions.** For each requested symbol the scanner runs the symbol's Master Decision and projects it into a row. It never computes its own verdict, score or risk.
3. **Isolation:**
   - symbols are evaluated one at a time;
   - an exception, or a decision whose symbol differs from the requested one, becomes that row only: `UNAVAILABLE`, `SYSTEM_INTEGRITY_FAILURE`, error "scan failed";
   - rows are never merged, and duplicates in the request collapse.
4. **Cache:** a row is reused for `cacheSeconds` (monotonic clock) and reports `cacheAgeSeconds`. One scan runs at a time, so a concurrent request reuses the fresh rows.
5. **Setup progress:** the 1-based index of the decision's setup state in `setupProgress`. It is 0 for NO_SETUP, NOT_EVALUATED, terminal states and any UNAVAILABLE row. READY/ACTIVE states are not in the list (and are rejected upstream by the authority guard).
6. **Ranking** (attention, never a signal), in order:
   1. usable data first (verdict not UNAVAILABLE);
   2. deeply validated markets first;
   3. further setup progress first;
   4. higher setup score first, no score last;
   5. fewer blockers first;
   6. instrument priority, then symbol.

   Filters are applied after ranking (`minScore`: rows with a score ≥ it; `onlySetups`: progress > 0), then ranks are renumbered 1..n.
7. **Symbol switching (web):**
   - opening a market sets the page symbol and resets the timeframe to M5;
   - every symbol-scoped state (decision, chart, structure, liquidity, PD arrays, no wick, sessions, setups, evaluation) is cleared before loading;
   - in-flight responses from the previous symbol are discarded (effect cleanup), and every loader still rejects payloads for another symbol;
   - the intelligence panel remounts (tab resets); event log entries carry their symbol.
8. **Client trust rules for scans:**
   - `authority` must be NOT_AUTHORIZED;
   - no LONG/SHORT verdict unless the backend reports FULL authority;
   - no READY/ACTIVE/CLOSED setup state;
   - a fail-safe verdict must carry blockers;
   - a research-only market must carry `MARKET_NOT_VALIDATED`;
   - grade must match score; no progress on UNAVAILABLE rows;
   - symbols must be requested and unique; ranks must be 1..n in order.

## OUTPUTS
- `GET /api/v1/markets` → `MarketRow[]` (symbol, asset class, base, quote, priority, deeply validated, market status now, position size status), in priority order.
- `GET /api/v1/scanner?symbols=XAUUSD,EURUSD&minScore=40&onlySetups=true` → `ScanResponse`:
  - `rows: ScanRow[]` (rank, market identity and validation, market status, verdict, data quality, HTF bias, setup state/type/progress, score/grade, confidence, risk status, primary DOL, blockers, next required event, latest closed bar, evaluated at, cache age, error);
  - also: requested symbols, filters, ranking rules, cache seconds, duration, verdict authority, `authority: NOT_AUTHORIZED`, strategy version, scanned at.
- New blocker `MARKET_NOT_VALIDATED`. Contract fields for `MarketRow`, `ScanRow`, `ScanResponse`.

## STATES
Rows carry the existing decision states (verdict WAIT/UNAVAILABLE, setup states, risk statuses). Web center views: `COMMAND_CENTER` (`#command-center`, default), `MARKETS` (`#markets`), `WATCHLIST` (`#watchlist`), `SCANNER` (`#scanner`).

## INVALIDATION
- A cached row expires after 60 s. A row can therefore show a decision up to 60 s (plus the poll interval) older than `/market-state`, and its age is displayed.
- A watchlist entry that is no longer in the catalog is dropped when read; corrupt or blocked storage falls back to the full catalog.

## ERROR STATES
| Condition | Result |
|---|---|
| Unknown symbol in `symbols` | 404 |
| Malformed `symbols`, `minScore` outside 0–100, more than `maxSymbols` | 422 |
| One symbol's evaluation raises or answers for another symbol | that row UNAVAILABLE + SYSTEM_INTEGRITY_FAILURE, error "scan failed"; other rows unaffected and ranked above it |
| Synthetic or stale data | rows UNAVAILABLE with progress 0 and no score |
| Web: scan or markets unreachable, malformed, or breaking a trust rule | SCAN / MARKETS UNAVAILABLE with the reason |
| Web: storage unavailable | watchlist defaults to the catalog; saving silently does nothing |

## UNIT TESTS
Backend (`tests/integration/test_scanner_api.py` also holds the pure tests):
- setup progress order; READY states are not in the progress list;
- deterministic ranking across all six rules;
- the gate adds `MARKET_NOT_VALIDATED` for EURUSD but not XAUUSD, and the verdict stays WAIT;
- `maxSymbols` is enforced.

Web `tests/scanner.test.tsx`:
- scan trust rules: accept, 12 rejections, requested-symbol mismatch, FULL authority;
- markets validation;
- watchlist sanitizing and storage (round trip, corrupt, blocked);
- labels and hash views;
- `loadScan` query building and symbol checking.

## INTEGRATION TESTS
Backend:
- the scan endpoint on non-synthetic fixture decisions: requested-symbol normalization, NOT_AUTHORIZED, ranks, `MARKET_NOT_VALIDATED` exactly on research-only rows, and **each row equals that symbol's `/market-state` decision** (verdict, setup state, score, grade, risk, blockers, HTF bias); no READY tokens;
- default scan of all 9 markets (XAUUSD first) with minScore / onlySetups filters and renumbered ranks;
- 404 / 422; duplicate symbols collapse;
- failure and wrong-symbol isolation plus the 60 s cache (served, then expired);
- `/markets` catalog; a synthetic scan is UNAVAILABLE everywhere; contract fields.

Web:
- scanner, markets and watchlist views; research-only chart banner; active nav item;
- **full page test:** open the scanner, click EURUSD, and the command center loads EURUSD (decision, M5 candles, research-only banner) with the XAUUSD HTF bias gone.

Updated: left nav now enables Markets, Watchlist and Scanner.

## UI DISPLAY
- **Left nav:** Command Center / Chart (`#command-center`), Markets, Watchlist, Scanner, Setups, Risk enabled; the active view is marked.
- **Markets** (center): table of markets with RESEARCH ONLY / VALIDATED badges, class, market hours status, position size status, a "watch" checkbox, and click-to-open.
- **Watchlist** (center): the scan table for the watched markets, with refresh. It says the list is stored in this browser only and is edited in Markets.
- **Scanner** (center):
  - the disclaimer "Attention ranking, not a trade signal", the ranking rules, filters (min score, open setups only) and a scan button;
  - a status line (rows, duration, cache, authority);
  - the scan table: rank, market + badge + hours, verdict (+ error), data, HTF, setup · stage, score (grade), confidence, risk, blocker count (hover lists them), next required event, age.
- **Command center:** a RESEARCH ONLY banner when the decision carries `MARKET_NOT_VALIDATED`. The status bar, intelligence panel and chart follow the opened symbol.

## KNOWN LIMITATIONS
- ⚠️ **Only XAUUSD is validated.** Every other market is research only: same engines, unvalidated parameters (for example, spreads and session behaviour differ for FX).
- ⚠️ **Scan latency:**
  - a cold 9-market scan takes about 15 s (~1.7 s per market, sequential, CPU-bound Python);
  - the web allows 45 s and only scans while a scan view is open;
  - warm scans within 60 s are instant;
  - a background scheduler or worker pool is future work.
- Cached rows can lag the live decision by up to 60 s plus the poll interval.
- Scanning publishes each symbol's `decision_changed` event like any decision evaluation.
- The watchlist is per browser, not per user (no auth yet).
- Ranking weights progress and score equally across markets although scores are uncalibrated (Phase 8 limitation).

## STRATEGY VERSION
`0.10.0-phase10` (phase 10, "Watchlist & Scanner"). Engines: + `scanner`. Verdict authority: **FAIL_SAFE_ONLY**.
