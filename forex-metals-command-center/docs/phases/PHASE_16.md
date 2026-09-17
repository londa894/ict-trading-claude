# Phase 16 — Paper Trading

## GOAL
Build broker-free paper trading: simulated positions replayed bar by bar on the **independent market data**, using the same engine code as live analysis (spec: "same strategy code for LIVE / PAPER / REPLAY / BACKTEST"; "paper/forward testing").

**A paper sim can be:**
- MANUAL: the user's own levels;
- ENGINE_PLAN: a forward test of the current confirmed plan.

**Each sim carries:**
- the same immutable engine snapshot and detected rule violations as a journal entry;
- simulated fills and exits with assumed spread, slippage and commission;
- an append-only, hashed event timeline;
- on close, the journal's outcome metrics (result state, R, MFE/MAE, efficiencies, process class).

**Nothing is sent anywhere and nothing is authorized.** Every sim carries `authority: SIMULATION_ONLY`. Verdict authority stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **No broker, venue, routing or order objects.** Paper sims exist only in this app's database.
- **Money P/L is not computed.** It would need contract specs, which are never guessed. Results are in price and R only.
- **Not built:**
  - partial exits, scaling, trailing or break-even stop management, time stops;
  - paper alerts;
  - assistant paper tools;
  - analytics across sims (Phase 17);
  - backtesting (18); replay (19).
- **Assumed costs** (`paper.json`) are research assumptions per symbol, not venue specifications.
- No authentication (inherited): keep the API local.

## INPUTS
- **Create** (`POST /api/v1/paper/sims`):
  - MANUAL: symbol, direction, entry type (MARKET | LIMIT with `limitPrice`), **stop and target (both required)**, notes;
  - ENGINE_PLAN: symbol and notes only. Direction, LIMIT at the plan entry, the plan stop and TP1 come from the confirmed plan.
- **Market data:** closed M5 candles from the CandleService (the configured provider), treated as mid prices.
- **Engine state at creation:** the shared capture (MarketStateService decision + evaluation + data report), also used by the journal.
- **Config (new):** `packages/strategy-spec/paper.json`:
  - execution timeframe M5; pending expiry 12 bars; max 1000 bars per advance chunk;
  - monitor every 60 s; max 50 pending/open sims;
  - `assumedCosts` per symbol: spread, slippage, commission per side, in price units. XAUUSD spread 0.30, slippage 0.05, commission 0.
- **Settings:** `PAPER_STORE` (`unconfigured` default | `database`), `PAPER_MONITOR_ENABLED` (default true, only runs with a database store) and `DATABASE_URL`. Tables: Alembic `0003_phase16`.

## RULES
1. **Store:**
   - `unconfigured`: status unavailable, list empty with the reason, writes → 503 ("nothing is simulated or saved");
   - database without tables → "run `alembic upgrade head`".
2. **Creation:**
   - needs usable closed candles (otherwise 422 "no usable market data"), assumed costs for the symbol, and fewer than 50 pending/open sims;
   - reference price = the limit price, or the last closed close for MARKET;
   - the stop must be on the losing side and the target on the winning side of the reference;
   - ENGINE_PLAN without a confirmed plan → 422;
   - detected violations use the journal rules, with the reference price as the entry;
   - the creation record (levels, costs, snapshot, violations) is SHA-256 hashed;
   - event 1 = CREATED.
3. **Simulation** (pure `services/paper/engine.py`, sequential, closed bars only):
   - **No lookahead:** only bars opening at or after creation, never an unclosed bar, never an already processed bar.
   - **Spread:** hs = spread/2. Longs enter at the ask (mid + hs) and exit at the bid; shorts the reverse.
   - **MARKET:** fills at the first eligible bar's open ± hs ± slippage.
   - **LIMIT:** fills when the entry-side price reaches the limit, at the better of the limit and the entry-side open (a gap through fills at the open). It expires after 12 bars without a fill.
   - **Exits on each bar while open:**
     - an open gapped through the stop → filled at that open ∓ slippage ("gapped");
     - an open gapped through the target → that open;
     - stop touched → stop ∓ slippage;
     - target touched → target;
     - **both in one bar → STOP (conservative), `ambiguous: true`**;
     - on the bar where a LIMIT filled intrabar, only the stop can trigger (ambiguous).
   - **MFE/MAE:** favourable and adverse exit-side extremes of every bar while open.
   - Chunked advancing gives the same result as one pass (tested).
4. **Advancing:**
   - on every read (list/get) and by the background monitor;
   - under a lock; the progress marker is compare-and-set on status and events are unique per sequence;
   - data INVALID/DISCONNECTED → no advance, with the reason; STALE → "waiting for new closed bars";
   - synthetic data is flagged;
   - a TAMPERED creation record is never simulated further;
   - CLOSED / EXPIRED / CANCELLED sims never change.
5. **User actions:**
   - Close (OPEN only): exit at the last closed bar's close on the exit side, paying slippage. Only when simulation has reached that bar; otherwise 422.
   - Cancel: PENDING only.
   - Delete: user-controlled and permanent.
6. **Result** (CLOSED):
   - computed by the journal's `compute_outcome` with the actual fill (or the reference entry if the fill gapped beyond the stop) and the exit;
   - gives result state (TARGET → FULL/PARTIAL_WIN by R vs planned; STOP → loss states; manual → MANUAL_EXIT), R after spread and slippage, and net R after commission;
   - also planned R, MFE/MAE R, entry/exit efficiency, duration, violations and process class.
7. **Integrity:**
   - the creation record and every event are hashed and verified on read;
   - any mismatch → TAMPERED (list and detail);
   - only `status` and `progress` (simulation state, data quality note) are mutable.

## OUTPUTS
- `GET /api/v1/paper/status` → `PaperStoreInfo`.
- `GET /api/v1/paper/sims?symbol&status&limit&cursor` → `PaperListResponse` (`PaperSimRow`, newest first).
- `POST /api/v1/paper/sims` → 201 `PaperSim`; `GET /api/v1/paper/sims/{id}`.
- `POST /api/v1/paper/sims/{id}/close`, `POST /api/v1/paper/sims/{id}/cancel` → `PaperSim`; `DELETE /api/v1/paper/sims/{id}` → 204.
- Enums: `PaperEntryType`, `PaperSource`, `PaperStatus`, `PaperEventType`.
- Contract fields: `AssumedCosts`, `CreatePaperSimRequest`, `PaperEvent`, `PaperResult`, `PaperSim`, `PaperSimRow`, `PaperStoreInfo`, `PaperListResponse`.
- DB: `paper_sims`, `paper_events` (Alembic `0003_phase16`).
- Shared: `services/journal/capture.py` (engine snapshot for the journal and paper).

## STATES
- Sim: PENDING → OPEN → CLOSED; PENDING → EXPIRED | CANCELLED.
- Events: CREATED, FILLED, STOP_HIT, TARGET_HIT, CLOSED_MANUALLY, EXPIRED, CANCELLED.
- Result states, process classes and integrity as in Phase 15.

## INVALIDATION
- A pending LIMIT expires after 12 bars. Stop, target, manual close or cancel end a sim. Terminal sims are frozen.
- Changed stored records read as TAMPERED and stop advancing.

## ERROR STATES
| Condition | Result |
|---|---|
| `PAPER_STORE=unconfigured` (default) | status unavailable; list empty + reason; create 503 |
| Tables missing / DB unreachable | unavailable with the reason |
| No usable candles, no assumed costs, too many active sims, levels on the wrong side, ENGINE_PLAN without a plan | 422 with a safe message |
| Close when not OPEN / no current closed bar; cancel when not PENDING | 422 |
| Invalid request shape (missing stop/target, limit rules, ENGINE_PLAN with levels) | 422 without values |
| Unknown symbol / sim; malformed id | 404; 422 |
| Data withheld / stale during advancing | no advance / waiting note |
| Web: authority other than SIMULATION_ONLY, levels on the wrong side, event order/type inconsistent with status, fill/exit/result inconsistent with status, class vs violations, net R above gross R, sims from an unavailable store | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_paper_engine.py` (12):
- config and assumed costs; request shapes;
- MARKET fill after creation with costs (long and short);
- LIMIT fill at the limit, better gap fill, spread-adjusted touch, expiry;
- target and stop with costs; target needs the bid; same-bar stop+target → stop (ambiguous); LIMIT fill bar checks only the stop;
- gaps through stop and target; short side uses the ask;
- no lookahead (unclosed bars, bars before creation, processed bars), idempotence and chunked equality;
- extremes and the manual close price.

## INTEGRATION TESTS
`tests/integration/test_paper_api.py` (17):
- unconfigured store; missing tables;
- MARKET sim fills on the first bar after creation at open + costs, then manual close (MANUAL_EXIT, frozen afterwards);
- a tight stop closes by simulation with a result, sequential verified events and the status filter;
- LIMIT expiry after 12 bars; cancel pending; close/cancel state errors;
- validation (wrong side, ENGINE_PLAN without a plan, 404/422);
- ENGINE_PLAN takes the confirmed plan (direction, limit, stop, TP1; no violations);
- **a tampered creation record is flagged and not advanced**;
- delete and the active-sim limit;
- 8 contract-field cases.

Updated: API surface test (paper POST/DELETE; no update route), Alembic migration test (new tables), version strings.

Web `tests/paper.test.tsx` (18): paper trust rules (3 accepted, 10 rejected); list refusal for an unavailable store; request building; Journal view paper tab from `#paper`; unconfigured explanation; tab switch → timeline → close; form validation and server error; cancel only for pending; tampered flag.

## UI DISPLAY
- **Journal view tabs:** "Records" (Phase 15) and "Paper sims" (`#paper` opens it).
- **Paper sims tab:**
  - a simulation-only note;
  - store status with setup instructions;
  - start form (market; source MANUAL or ENGINE_PLAN; direction; MARKET or LIMIT with limit price; stop; target; notes);
  - table (created, market, sim, status or result, fill → exit, net R, process class, VERIFIED/TAMPERED and SYNTHETIC badges).
- **Sim detail:**
  - header with source, entry type, direction, status and `SIMULATION_ONLY`;
  - levels; assumed costs labelled "assumptions, not broker specs";
  - simulation progress (fill, exit, simulated-through, data quality and note, synthetic);
  - engine state at start; detected violations;
  - result line (result, R / net R vs planned, class, MFE/MAE, duration);
  - event timeline with AMBIGUOUS BAR marks;
  - Close (OPEN) / Cancel (PENDING) / Delete (with confirmation); tampered warning.

## KNOWN LIMITATIONS
- ⚠️ **Assumed costs, mid-price candles and bar-level simulation.**
  - Intrabar order is unknown: same-bar stop and target resolve to the stop (conservative).
  - Gaps fill at the open. There are no ticks, no requotes and no liquidity limits.
  - Results are optimistic or pessimistic relative to any real venue.
- ⚠️ **Fixture market data is synthetic.** In real time it is stale, so live paper sims wait for bars that never arrive. The smoke run used a controlled clock.
- ⚠️ **No authentication.** Anyone reaching the API can create or delete sims.
- **Money P/L** needs instrument specs (not guessed); results are in R and price only.
- The monitor processes at most 50 active sims per cycle, 1000 bars per chunk and 20 chunks per advance. Long outages are caught up over several cycles.
- Tamper evidence, not tamper proof (no signing key). Postgres path not run (SQLite verified with the migration).

## STRATEGY VERSION
`0.16.0-phase16` (phase 16, "Paper Trading"). Engines: + `paper`. Verdict authority: **FAIL_SAFE_ONLY**.
