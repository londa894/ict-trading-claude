# Phase 15 — Journal V1

## GOAL
Build the basic journal (spec STEP 10, journal part; STEP 17 privacy).

**What is recorded:**
- the user's own decisions: NO_TRADE and MISSED_ENTRY;
- the user's own **manual** trades, as fills typed in by the user.

**The pre-trade snapshot:** every entry carries an immutable engine snapshot captured by the server at logging time (Master Decision, evaluation, data report). It is SHA-256 hashed and re-verified on every read.

**Outcomes:** append-only revisions. Each one computes:
- R, planned R, MFE/MAE R, entry and exit efficiency, duration;
- the spec result state (11 states);
- the process class (VALID_WIN / BAD_PROCESS_WIN / VALID_LOSS / PROCESS_ERROR).

The process class uses rule violations detected from the snapshot plus violations the user reports.

**The journal never places, sizes or authorizes anything. Verdict authority stays `FAIL_SAFE_ONLY`.**

## SCOPE BOUNDARY
- **Analytics** (Phase 17) is not built: win rate, expectancy, profit factor, drawdown, best asset/session, sample-size labels. The assistant says so.
- **Paper trading** (Phase 16), **backtesting** (18) and **replay** (19) are not built.
- **No broker import and no fills from any platform.** Prices are typed in by the user.
- **Premium/discount** is not recorded: no engine computes it yet.
- **No screenshots, tags or playbook links.**
- **No authentication or multi-user isolation yet.** The journal is single-user, server-side and private by configuration.
- Risk account state (Phase 9 manual profile) is **not** updated from journal outcomes.

## INPUTS
- **Create** (`POST /api/v1/journal/entries`): symbol, kind (TRADE / NO_TRADE / MISSED_ENTRY), notes (≤ 2000).
  - TRADE also needs a fill: direction, entry, stop (optional = no stop), up to 3 targets, volume, risk %, `openedAt` (UTC).
  - The stop must be on the losing side and the targets on the winning side.
- **Outcome** (`POST …/entries/{id}/outcomes`): exit price (average), `exitedAt` (UTC), exit reason, optional MFE/MAE prices, self-reported violations, notes. Exit reasons: TARGET / STOP / BREAK_EVEN_STOP / MANUAL / INVALIDATION / NEWS / TRAILING_STOP.
- **Engine state at logging:** the MarketStateService decision plus an EvaluationService evaluation using the decision's HTF bias and DOL.
- **Candles** for MFE/MAE when they aren't given: M5, else M15, else H1, whichever covers the trade within 1000 bars.
- **Config (new):** `packages/strategy-spec/journal.json`:
  - break-even tolerance 0.1 R, full-win tolerance 10 % of planned R, full-loss tolerance 0.1 R;
  - chase tolerance 0.25 R, post-entry snapshot 5 min;
  - max 30 days for candle extremes, extremes timeframes M5/M15/H1;
  - notes 2000 chars, list default 50 / max 200.
- **Settings:** `JOURNAL_STORE` (`unconfigured` default | `database`) and `DATABASE_URL` (Postgres or `sqlite+pysqlite:///file`). Tables come from Alembic revision `0002_phase15`.

## RULES
1. **Store:**
   - `unconfigured`: status unavailable, list empty with the reason, writes → 503 ("nothing is saved"). It never pretends to save.
   - `database`: missing tables → unavailable ("run `alembic upgrade head`"); unreachable DB → unavailable.
2. **Immutable pre-trade record:**
   - `record` = {trade, notes, detectedViolations, snapshot{capturedAt, timing, decision, evaluation, data}};
   - `record_hash` = SHA-256 of canonical JSON of {id, createdAt, symbol, kind, record, strategyVersion};
   - there is no update path;
   - every read recomputes the hash: VERIFIED or TAMPERED;
   - a TAMPERED entry cannot get outcomes, and the list flags it;
   - outcome revisions are hashed the same way;
   - snapshots are stored as JSON, so records from older strategy versions still read.
3. **Snapshot timing:**
   - PRE_ENTRY when the trade opened ≤ 5 min before capture;
   - POST_ENTRY otherwise (not what the trader saw before entering);
   - NOT_APPLICABLE for non-trades;
   - `openedAt` in the future → 422.
4. **Detected violations** (TRADE, from the snapshot):
   - decision UNAVAILABLE → TRADED_ON_UNAVAILABLE_DECISION;
   - news BLACKOUT → TRADED_DURING_NEWS_BLACKOUT;
   - risk LOCKED → TRADED_WHILE_RISK_LOCKED;
   - no confirmed plan (an unknown evaluation counts as no plan) → NO_CONFIRMED_PLAN;
   - plan in the other direction → AGAINST_PLAN_DIRECTION;
   - entry more than 0.25 × plan risk beyond the plan entry → CHASED_ENTRY;
   - no stop → NO_STOP_PLACED;
   - risk % above the profile's per-trade limit → RISK_ABOVE_LIMIT.

   The engine's authorization (NOT_AUTHORIZED while FAIL_SAFE_ONLY) is recorded as information, not as a violation.
5. **Outcome checks** (422, messages without values):
   - exit after open and not in the future;
   - MFE at least as favourable as the entry and exit, MAE at least as adverse;
   - TRADE entries only.
6. **Extremes:**
   - both manual → MANUAL;
   - otherwise candles over [openedAt, exitedAt), widened to the fills, with manual values overriding → CANDLES (synthetic flag from the provider);
   - withheld data, history not reaching the entry, data ending before the trade, or a trade longer than 30 days / 1000 bars → UNAVAILABLE with the reason (MFE/MAE R and efficiencies null).
7. **Metrics:**
   - R = s × (exit − entry) / |entry − stop|;
   - planned R to the farthest target;
   - MFE/MAE R;
   - entry efficiency = s(mfe − entry) / s(mfe − mae);
   - exit efficiency = s(exit − mae) / s(mfe − mae);
   - duration in minutes.
8. **Result state:**
   - MANUAL / INVALIDATION / NEWS / TRAILING_STOP exits → MANUAL_EXIT / INVALIDATION_EXIT / NEWS_EXIT / TRAILING_STOP_EXIT;
   - otherwise |R| ≤ 0.1 → BREAK_EVEN; R ≥ 0.9 × planned → FULL_WIN; R > 0 → PARTIAL_WIN; R ≤ −0.9 → FULL_LOSS; else PARTIAL_LOSS;
   - no stop: sign of P/L only (PARTIAL_WIN / BREAK_EVEN / PARTIAL_LOSS);
   - NO_TRADE / MISSED_ENTRY entries are CLOSED with that result.
9. **Process class:**
   - a win (R > 0.1, or positive P/L without a stop) → VALID_WIN without violations, else BAD_PROCESS_WIN;
   - otherwise (break-even included) → VALID_LOSS without violations, else PROCESS_ERROR.

   Violations = detected + reported.
10. **Revisions:** every outcome POST adds revision n+1. Earlier revisions are kept and shown. The entry shows the latest.
11. **Privacy:**
    - DELETE removes the user's own entry and its outcomes (the user's decision);
    - JSON export of all entries;
    - 422 bodies never echo submitted values;
    - the web client stores nothing in the browser.
12. **Assistant:**
    - `query_journal` (symbol-scoped, latest 10) returns kind, result, R, class, detected violations, timing, synthetic flag and result counts;
    - fill prices are withheld unless `AI_SHARE_ACCOUNT_DATA`;
    - intent JOURNAL (journal, my/last/past trades, trade history, logged);
    - an unconfigured store becomes an UNKNOWN;
    - the SCORE_CHANGE unknown now explains that scores are not recorded over time.

## OUTPUTS
- `GET /api/v1/journal/status` → `JournalStoreInfo`.
- `GET /api/v1/journal/entries?symbol&kind&from&to&limit&cursor` → `JournalListResponse` (`JournalEntryRow`, newest first, keyset cursor).
- `POST /api/v1/journal/entries` → 201 `JournalEntry` (summary, snapshot, outcome, revisions).
- `GET /api/v1/journal/entries/{id}`, `DELETE /api/v1/journal/entries/{id}` → 204.
- `POST /api/v1/journal/entries/{id}/outcomes` → 201 `JournalEntry`.
- `GET /api/v1/journal/export` → `JournalExport`.
- Enums: `JournalEntryKind`, `JournalStatus`, `TradeResult`, `ExitReason`, `ProcessClassification`, `RuleViolation`, `SnapshotIntegrity`, `SnapshotTiming`, `ExtremeSource`; `AssistantIntent` + JOURNAL.
- Contract fields: `JournalTradeFill`, `CreateJournalEntryRequest`, `RecordOutcomeRequest`, `SnapshotSummary`, `JournalSnapshot`, `JournalOutcome`, `JournalEntry`, `JournalEntryRow`, `JournalStoreInfo`, `JournalListResponse`, `JournalExport`.
- DB: `journal_entries`, `journal_outcomes` (Alembic `0002_phase15`).

## STATES
- Entry: OPEN (TRADE without outcome) / CLOSED.
- Result: FULL_WIN / PARTIAL_WIN / BREAK_EVEN / FULL_LOSS / PARTIAL_LOSS / MANUAL_EXIT / INVALIDATION_EXIT / NEWS_EXIT / TRAILING_STOP_EXIT / MISSED_ENTRY / NO_TRADE.
- Process: VALID_WIN / BAD_PROCESS_WIN / VALID_LOSS / PROCESS_ERROR.
- Integrity: VERIFIED / TAMPERED. Timing: PRE_ENTRY / POST_ENTRY / NOT_APPLICABLE. Extremes: MANUAL / CANDLES / UNAVAILABLE.

## INVALIDATION
- Entries are never updated; a correction is a new outcome revision. A changed stored record reads as TAMPERED.
- Deleting is permanent and user-initiated.

## ERROR STATES
| Condition | Result |
|---|---|
| `JOURNAL_STORE=unconfigured` (default) | status unavailable; list empty + reason; create/export 503; nothing saved |
| Database unreachable / tables missing | unavailable with the reason (run `alembic upgrade head`) |
| Invalid fill (sides, UTC, lengths), trade details on non-trades, future `openedAt` | 422 without values |
| Outcome before open / in the future / MFE-MAE contradicting fills / non-trade entry / tampered entry | 422 with a safe message |
| Unknown symbol or entry; malformed id; bad cursor | 404; 422; 422 |
| Candle extremes impossible | extremes UNAVAILABLE (metrics that need them null) |
| Web: status/result/R/class inconsistent with the trade and revisions, bad hash, detected violations missing from the outcome, entries from an unavailable store | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_journal_engine.py` (35):
- config guard; fill side, UTC and notes validation; trade details only for TRADE;
- canonical hash; snapshot timing; summary field extraction;
- clean trade has no violations; 8 detected-violation cases; chase tolerance and the short side; unknown plan;
- 10 result-state cases with R/planned R/duration; short trade R, MFE/MAE R and efficiencies; no-stop results;
- 5 process-class cases; outcome contradiction checks without values; candle extremes widening.

## INTEGRATION TESTS
`tests/integration/test_journal_api.py` (22):
- unconfigured store saves nothing and says so; missing tables;
- a trade entry captures a VERIFIED immutable snapshot equal to the live decision and detects NO_CONFIRMED_PLAN;
- **tampering in the database is detected and blocks outcomes**;
- append-only revisions (manual extremes, then a corrected STOP outcome with candle extremes and a reported violation);
- outcome validation, non-trade entries, 404/422 without values;
- POST_ENTRY labelling; list filters, keyset paging, export and delete;
- synthetic market data flagged; the assistant reads the journal and withholds fill prices;
- candle extremes report when the market data ends before the trade;
- 11 contract-field cases.

Updated: API surface test (journal POST/DELETE allowed; no update route), Alembic migration test (new tables), assistant score-history text, version strings.

Web `tests/journal.test.tsx` (21):
- record trust (3 accepted, 10 rejected); list refusal for an unavailable store; R parity with the server;
- form-to-request building and errors; safe server error detail; no browser storage;
- view: `#journal` hash, unconfigured store explanation, list → detail → corrected outcome, create with validation, tampered record without an outcome form and delete requiring confirmation.

Updated: left-nav test (Journal enabled).

## UI DISPLAY
- **Left nav Journal** (`#journal`) opens the Journal view:
  - a privacy note;
  - the store status (with setup instructions when not saving);
  - a record form (market, record kind, direction, opened local time, entry, stop, targets, volume / risk %, notes; the button says it captures the engine snapshot now);
  - an entries table (recorded time, market, record, result or status, R, process class, violation count, integrity / POST_ENTRY / SYNTHETIC badges).
- **Entry detail:**
  - snapshot integrity and timing, engine verdict with authorization, setup, plan, context (DOL, news, macro, risk);
  - my trade, notes, detected violations;
  - latest outcome (result, R vs planned R, class, exit), MFE/MAE R, efficiencies, extremes source, duration, violations;
  - earlier revisions;
  - the outcome form (self-reported violations checkboxes; "Record corrected outcome (new revision)" once an outcome exists);
  - delete with confirmation;
  - tampered entries show "INTEGRITY CHECK FAILED" and no outcome form.

## KNOWN LIMITATIONS
- ⚠️ **Off by default:** `JOURNAL_STORE=unconfigured` saves nothing. The user must configure `DATABASE_URL` and run the migration.
- ⚠️ **No authentication:** anyone who can reach the API can read or delete the journal. Keep the API local or behind your own access control until auth exists.
- ⚠️ **Tamper evidence, not tamper proof:** someone with database access can rewrite a record *and* its hash (no server-side signing key). The check catches accidental or naive edits, not a determined attacker.
- **Engine snapshot at logging time:** a trade logged later than 5 min after entry is POST_ENTRY; the engine's past state is not reconstructed.
- **Candle MFE/MAE** use the configured market-data provider (synthetic with the fixture), bar highs/lows (no ticks or spread) and at most 1000 bars.
- Rule thresholds (tolerances, chase) are research defaults. Violations such as moved stops or revenge trades are self-reported only.
- No analytics, sample-size labels, tags, screenshots or broker import.

## STRATEGY VERSION
`0.15.0-phase15` (phase 15, "Journal V1"). Engines: + `journal`. Verdict authority: **FAIL_SAFE_ONLY**.
