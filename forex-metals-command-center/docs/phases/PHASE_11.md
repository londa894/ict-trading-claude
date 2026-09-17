# Phase 11 — Alerts V1

## GOAL
Monitor meaningful state changes of the deterministic engines and raise categorized, prioritized, deduplicated alerts (spec STEP 9), including the signature **ALERT_ME_WHEN_READY** watch. Alerts appear in an ALERTS view and in the bottom event area.

**Verdict authority stays `FAIL_SAFE_ONLY`.** Alerts describe engine state; they are never trade instructions and carry no LONG/SHORT wording. A READY alert can only exist when the Master Decision itself is LONG/SHORT under FULL authority, which the authority guard makes impossible today. Tests prove a watch cannot fire.

## SCOPE BOUNDARY
Alert types in spec STEP 9 that need later engines are **not** raised:
- macro / DXY / yield / SMT changes (Phase 14 / 20+);
- news countdown (Phase 13);
- TP / SL / thesis invalidation (needs active trades: paper trading, Phase 16);
- order-block touch (advanced PD arrays, Phase 20+);
- spread lock (no quote feed; the risk engine's spread lock already surfaces as RISK_LOCK when a spec is supplied).

The MANAGEMENT category exists but no rule uses it yet.

- **Delivery:** in-app only (feed + event area). No email, push or other outbound messages.
- **Persistence:** alerts and ready watches live in API process memory (bounded) and are lost on restart; a database arrives later.
- **No per-user alert rules** (auth).

## INPUTS
- The Master Decision of each monitored symbol (`MarketStateService.evaluate`).
- The evaluation and the setup run it came from (`EvaluationService.evaluate_with_run`: M15 pipeline structure / liquidity / PD arrays / displacement / no wick, setup events, session clock, risk assessment, entry warnings).
- The latest closed M5 bar (cheap freshness check).
- **Config (new):** `packages/strategy-spec/alerts.json`:
  - `monitoredSymbols` ["XAUUSD"], `maxMonitoredSymbols` 3, `maxReadyWatches` 10;
  - `pollSeconds` 30, `maxQuietSeconds` 300, `bufferSize` 500, `noveltyGraceBars` 1;
  - minimum displacement grade and no-wick strength (STRONG);
  - per-category cooldowns;
  - category + priority for each of the 28 alert types.
- Setting `ALERT_MONITOR_ENABLED` (default true).

## RULES
1. **Monitor loop:**
   - runs in the API process (FastAPI lifespan), every 30 s, over XAUUSD plus the symbols of ready watches (max 3);
   - a symbol is re-evaluated only when a new M5 bar has closed, 300 s have passed without an evaluation (so a dead feed still raises DATA_UNAVAILABLE), or it has an unchecked ready watch;
   - symbols are isolated: an exception raises one `MONITOR_FAILURE` (CRITICAL, cooldown) for that symbol, and the cycle continues.
2. **Baseline:** the first evaluation of a symbol records state silently, so history never floods the feed.
3. **Trust:** market-event alerts come only from data eligible for a decision (verdict not UNAVAILABLE and evaluation eligible). Unusable data raises only `DATA_UNAVAILABLE` (on transition), and `DATA_RESTORED` when usable again.
4. **Novelty:** an analysis event alerts when its id was not seen in the previous cycle AND its time is no earlier than one setup bar before the previous as-of. This drops old events that re-surface because key-level/session pools are windowed (Phase 7 limitation).
5. **Rules** (type → category / priority):

   | Source | Alert |
   |---|---|
   | Liquidity SWEEP | `LIQUIDITY_SWEEP` (WATCH/MEDIUM) |
   | Liquidity RUN/BREAK | `LIQUIDITY_RUN` (WATCH/MEDIUM) |
   | Any event on PDH/PDL/PWH/PWL | `KEY_LEVEL_INTERACTION` (WATCH/MEDIUM) |
   | SWEEP/RUN/BREAK on a session pool | `SESSION_LIQUIDITY_EVENT` (WATCH/MEDIUM) |
   | New EQH/EQL pool | `EQUAL_LEVEL_CREATED` (WATCH/LOW) |
   | Untaken external pool newly APPROACHING | `LIQUIDITY_APPROACHING` (WATCH/LOW) |
   | Confirmed BOS/CHoCH/MSS (POTENTIAL ignored) | `STRUCTURE_BREAK` (SETUP/MEDIUM) |
   | Displacement reaching STRONG+ | `DISPLACEMENT` (WATCH/MEDIUM) |
   | No-wick candle STRONG+ | `NO_WICK_EVENT` (WATCH/LOW) |
   | No-wick zone FULLY_REBALANCED / REACTED / FAILED | `NO_WICK_REBALANCE` (WATCH/LOW) |
   | FVG/IFVG CREATED or IFVG_CONFIRMED / TOUCHED / INVALIDATED, only for the open setup's zones or zones born from STRONG+ displacement | `FVG_CREATED` / `FVG_TOUCHED` / `FVG_INVALIDATED` |
   | Setup SETUP_ARMED | `SETUP_ARMED` (SETUP/MEDIUM) |
   | Setup ENTRY_ZONE_APPROACHING / TOUCHED | `ENTRY_ZONE_APPROACHING` (ENTRY/MEDIUM) / `ENTRY_ZONE_TOUCHED` (ENTRY/HIGH) |
   | Setup BLOCKED (confirmed plan) | `ENTRY_CONFIRMED_PENDING_GATES` (ENTRY/HIGH), "Not authorized: pending the risk check and news gate." |
   | Setup ENTRY_MISSED | `ENTRY_MISSED` (ENTRY/MEDIUM), "… Do not chase." (chase warning) |
   | Setup INVALIDATED / EXPIRED | `SETUP_INVALIDATED` / `SETUP_EXPIRED` |
   | New evaluation warning for the same open setup | `EARLY_ENTRY_WARNING` (ENTRY/MEDIUM) |
   | Active sessions changed | `SESSION_CHANGE` (INFORMATIONAL/LOW) |
   | New / cleared risk lock | `RISK_LOCK` (RISK/HIGH) / `RISK_LOCK_CLEARED` (RISK/LOW) |
   | Verdict becomes UNAVAILABLE / usable again | `DATA_UNAVAILABLE` (CRITICAL) / `DATA_RESTORED` |
   | Monitor exception | `MONITOR_FAILURE` (CRITICAL) |
   | Ready watch fires | `READY` (ENTRY/CRITICAL) |

6. **Dedupe & cooldown:**
   - `dedupeKey = symbol:type:reference` (event id, lock, "decision", watch id…);
   - the same key within its category cooldown (INFORMATIONAL/WATCH/RISK 900 s, SETUP/ENTRY/MANAGEMENT 300 s, CRITICAL 600 s) is suppressed and counted.
   - State memory (verdict, locks, warnings, sessions, seen ids, approaching pools) makes state alerts fire on change only.
7. **ALERT_ME_WHEN_READY:**
   - a watch is symbol + optional direction; creating the same open watch twice returns it;
   - each evaluation rebuilds the checklist: DATA_USABLE, OPEN_SETUP (matching direction), HTF_BIAS … LTF_CONFIRMATION (setup steps), RISK (risk status WITHIN_LIMITS), NEWS_GATE (always missing before Phase 13), VERDICT_AUTHORITY (FULL), VERDICT (LONG for BULLISH, SHORT for BEARISH);
   - on unusable data every setup condition is NOT_EVALUATED;
   - states: UNAVAILABLE (data), WAITING, GATES_PENDING (setup + risk met, gates/authority missing), FIRED;
   - fires once, only when data is usable, authority is FULL and the decision verdict matches.

## OUTPUTS
- `GET /api/v1/alerts?since=&symbol=&category=&minPriority=&limit=` → `AlertFeed` (alerts newest first, `nextCursor`, suppressed counts per type, monitor status, `authority: NOT_AUTHORIZED`).
- `GET /api/v1/alerts/ready-watches` → `ReadyWatch[]`; `POST` (`{symbol, direction?}`) → 201 `ReadyWatch`; `DELETE /api/v1/alerts/ready-watches/{id}` → 204.
- Enums: `AlertCategory`, `AlertPriority`, `AlertType` (28), `ReadyWatchState`, `ConditionStatus`.
- Contract fields: `Alert`, `MonitorStatus`, `AlertFeed`, `ReadyCondition`, `ReadyWatch`, `ReadyWatchRequest`.

## STATES
- Alert categories: INFORMATIONAL / WATCH / SETUP / ENTRY / RISK / MANAGEMENT / CRITICAL.
- Priorities: LOW / MEDIUM / HIGH / CRITICAL.
- Watch: WAITING / GATES_PENDING / UNAVAILABLE / FIRED. Condition: MET / MISSING / NOT_EVALUATED.
- Monitor: enabled, running, cycles, last cycle time and duration, last error, baselined symbols.

## INVALIDATION
- A fired watch stays FIRED (one-shot); remove it to watch again.
- The buffer keeps the newest 500 alerts. Restarting the API clears alerts, watches and state memory (the next cycle is a new silent baseline).

## ERROR STATES
| Condition | Result |
|---|---|
| A symbol's evaluation raises / answers for another symbol | `MONITOR_FAILURE` for that symbol (cooldown 600 s), `lastError`, others unaffected |
| Feed goes stale mid-week | one `DATA_UNAVAILABLE`; no market alerts until data is usable |
| Unknown symbol for a watch | 404; limits (10 watches, 3 monitored symbols) → 422; malformed watch id → 422; unknown watch → 404 |
| Invalid `category` / `minPriority` | 422 |
| Web: feed claims authority, READY alert or FIRED watch without FULL authority, LONG/SHORT/BUY/SELL wording in a non-READY alert, unordered feed | ALERTS UNAVAILABLE with the reason |
| Web: dismissal storage blocked | dismissals last for the page view |

## UNIT TESTS
`tests/unit/test_alert_rules.py` (18):
- silent baseline; DATA_UNAVAILABLE / RESTORED, with no market alerts from untrusted data;
- setup transitions → alerts (chase and not-authorized wording, no LONG/SHORT);
- novelty grace;
- every market-event rule with its filters (POTENTIAL, TOUCH, weak displacement, meaningful no-wick, HALF_FILL, weak FVG ignored);
- weak FVG alerts only for setup zones;
- risk lock new/cleared, session change, warnings on change only;
- store cooldown / suppression / bounded buffer; config completeness;
- ready checklist: cannot fire under FAIL_SAFE_ONLY (even with a forbidden directional verdict), fires only with a matching verdict + FULL, states, risk requirement, unusable data never counts setup steps.

## INTEGRATION TESTS
`tests/integration/test_alerts_api.py` (16), on real engine decisions (non-synthetic fixture stub):
- baseline then alerts over six hours of events: category/priority from config, no LONG/SHORT, no duplicate keys, feed order and cursor;
- skip without a new bar until the quiet period;
- mid-week feed outage → one CRITICAL DATA_UNAVAILABLE, no repeat;
- failing-symbol isolation and failure cooldown;
- a ready watch tracks conditions for hours but never fires; fires exactly once when the decision is forced LONG with FULL authority;
- a new watch is checked on the next cycle;
- watch limits; API endpoints (201/204/404/422);
- lifespan starts and stops the monitor; contract fields.

Updated: the route surface test allows exactly the watch GET/POST/DELETE besides the risk calculator.

Web `tests/alerts.test.tsx` (17): feed trust rules; watches (FIRED only with FULL); create/delete client; incremental loading, merge, event-log entries; filters and dismissal storage; ALERTS view (disclaimer, monitor status, checklist ✓/✗, create/remove/dismiss, fail-safe display).

## UI DISPLAY
- **Left nav:** Alerts enabled (`#alerts`).
- **ALERTS view** (center):
  - disclaimer; monitor status (running, symbols, cycles, last cycle time and ms, duplicates suppressed, last error);
  - **ALERT ME WHEN READY**: symbol + direction selector, watch list with state, next required condition, remove, and the ✓/✗ checklist;
  - **Feed**: filters (category, minimum priority, market), a table (time, priority, category, title + message, dismiss), and "show dismissed".
- **Bottom event area:** new alerts appear as `[PRIORITY CATEGORY] title · message` (CRITICAL = error style, HIGH = warning style), alongside decision and data events. The web polls every 15 s by cursor.

## KNOWN LIMITATIONS
- ⚠️ **In-memory only:** alerts, watches and state memory vanish on API restart, and each API process has its own monitor (run one).
- ⚠️ **The monitor shares the API event loop.** A full evaluation (~0.6–2.6 s, CPU-bound) delays other requests while it runs. It happens only on a new M5 bar (or every 5 min) and only for up to 3 symbols. A worker process is future work.
- Detection latency is up to the poll interval (30 s) after the bar is available, plus the web poll (15 s).
- Alert volume on fixture data after tuning: ~23 alerts per 6 hours for XAUUSD (session changes, liquidity, strong displacement/FVGs, no-wick, setup expiry, warnings). Thresholds are uncalibrated.
- Alerts reflect analyses on M15 (setup timeframe); M5/H1-only events are not monitored.
- Dismissals are per browser. There is no delivery outside the app.

## STRATEGY VERSION
`0.11.0-phase11` (phase 11, "Alerts V1"). Engines: + `alerts`. Verdict authority: **FAIL_SAFE_ONLY**.
