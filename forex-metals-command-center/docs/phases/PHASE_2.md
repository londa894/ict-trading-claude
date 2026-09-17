# Phase 2 — Market Structure

## GOAL
Deterministically detect market structure (spec STEP 2, structure half): swing highs/lows,
HH/HL/LH/LL/EH/EL, internal vs external structure, protected high/low, BOS / CHoCH / MSS, and states
BULLISH / BEARISH / RANGING / TRANSITIONING / UNCLEAR. Expose it through the API, the chart overlay and the
STRUCTURE tab, and let eligible structure describe the Master Decision's `htfBias` / `structureEvent`
**without** any authority over the verdict.

## INPUTS
- Validated candle series from the shared `CandleService.load_series` (M5/M15/H1 native; H4/D1 NY-close from H1). Only **closed** candles are analysed.
- `packages/strategy-spec/structure.json`: pivot lengths, equal tolerance, ATR period, ranging rules, minimum candles, decision timeframes/limits.

## RULES
1. **Swing (pivot):** a candle at index `j` is a swing HIGH with pivot length `L` if its high is strictly above the `L` highs before it and ≥ the `L` highs after it. Lows mirror this. The first of equal highs/lows wins. Defaults: `L` = 3 (INTERNAL), 10 (EXTERNAL).
2. **No lookahead:** a swing is known only after candle `j+L` closes (`confirmedAt`). At candle `i` the engine uses only swings confirmed at or before `i−1`.
3. **Labels:** each swing is compared with the previous swing of the same kind. HH/LH (highs), HL/LL (lows), EH/EL when `|Δ| ≤ 0.1 × ATR(14)`, with ATR computed from candles up to confirmation. The first swing is `NONE`.
4. **Break targets:** the most recent confirmed, unbroken swing high/low. Older swings are superseded.
5. **Confirmation:** CANDLE_CLOSE by default. A close beyond the level gives a CONFIRMED event. A wick beyond with the close inside gives a **POTENTIAL** event (WICK_ONLY), once per swing; it never changes trend or protected levels.
6. **Classification of confirmed breaks:**
   - **BOS:** a break in the trend direction. The first break with no trend is also a BOS, with `trendBefore = NONE`.
   - **CHoCH:** a counter-trend close through a non-protected target. The state becomes TRANSITIONING and the trend is kept.
   - **MSS:** a counter-trend close through the **protected** swing. The trend flips.
   - A BOS in the trend direction after a CHoCH resolves the transition (the CHoCH failed).
7. **Protected levels:** on a BOS/MSS, the protected opposite swing is set to the latest unbroken opposite swing. If that one is already broken, the previous protected level is kept, provided it's unbroken and the trend continues. The same-side protected level is cleared.
8. **Ambiguous candle guard:** if one candle closes through both sides, both events are flagged `ambiguous` and trend → NONE. With close confirmation this is provably unreachable; the property tests confirm it never fires.
9. **State** (as of the last closed candle), checked in this order:
   - UNCLEAR: fewer than `minCandles` (50), or no trend.
   - TRANSITIONING: after an unresolved CHoCH.
   - RANGING: the latest swing high/low **both formed after the last break** have compression/expansion labels ((LH|EH)+(HL|EL), or HH+LL), or there has been no break for `rangingBarsWithoutBreak` bars (40 internal / 120 external).
   - Otherwise BULLISH/BEARISH.
10. **MSS qualifiers:** `liquidityQualifier` / `displacementQualifier` = `NOT_EVALUATED` on every event. Liquidity (Phase 3) and Displacement (Phase 4) will fill them. MSS is structural only in Phase 2.
11. **Eligibility for the decision:** data CURRENT or DELAYED, not synthetic, and ≥ `minCandles`. INVALID/DISCONNECTED → no analysis at all. STALE/SYNTHETIC → analysis shown (labelled) but not used.
12. **Decision context (enrichment only):**
    - `htfBias` comes from D1+H4 external states: both BULLISH/BEARISH → that; any UNCLEAR → UNCLEAR; any TRANSITIONING → TRANSITIONING; both RANGING → RANGING; otherwise MIXED. Any ineligible input → UNKNOWN.
    - `structureEvent` = the latest confirmed M15 event.
    - It's computed only when the gate verdict isn't UNAVAILABLE. The verdict, blockers and data quality are never modified.
    - If the structure step fails, the decision gets UNKNOWN/null.
13. **MTF alignment** (external D1/H4/H1/M15/M5): ALIGNED_BULLISH / ALIGNED_BEARISH if all agree; UNCLEAR if any is missing or UNCLEAR; otherwise MIXED. A numeric score is deferred to scoring (Phase 8).
14. **Chart overlay:**
    - Drawn only if chart and structure are both READY for the same timeframe **and every anchor time exists among the drawn candles**. Otherwise it's hidden with a reason.
    - Contents: external shown by default, internal optional (lower-case labels, dashed segments).
    - Up to 40 confirmed-event segments per level. Protected levels are drawn as price lines.

## OUTPUTS
- `GET /api/v1/structure/{symbol}?timeframe=&limit=` → `StructureAnalysis`: internal/external `LevelStructure` (state, trend, swings, events, protected high/low, bars since break), a chronological event log, eligibility.
- `GET /api/v1/structure/{symbol}/alignment` → `MtfStructureResponse`.
- Master Decision: `htfBias` (HtfBias enum value), `structureEvent` (string or null).

## STATES
- StructureState: BULLISH / BEARISH / RANGING / TRANSITIONING / UNCLEAR
- TrendDirection: BULLISH / BEARISH / NONE
- Event: type BOS/CHOCH/MSS × direction × status CONFIRMED/POTENTIAL
- MtfAlignment: ALIGNED_BULLISH / ALIGNED_BEARISH / MIXED / UNCLEAR
- HtfBias: BULLISH / BEARISH / RANGING / TRANSITIONING / MIXED / UNCLEAR / UNKNOWN

## INVALIDATION
- A swing is invalidated (broken) exactly once, by the confirmed event that closes through it (`brokenAt` / `brokenBy`).
- A trend is invalidated by an MSS through its protected swing.
- A CHoCH is invalidated by a BOS in the original trend direction.
- Analysis is invalidated by ineligible data (see rule 11).

## ERROR STATES
| Condition | Result |
|---|---|
| Unsupported timeframe / limit | HTTP 422 |
| Unknown symbol | HTTP 404 (structure and alignment) |
| INVALID / DISCONNECTED data | 200, `internal=null`, `external=null`, `events=[]`, reason listed |
| STALE / synthetic / insufficient candles | analysis returned, `eligibleForDecision=false` with reasons |
| Structure step throws during decision | decision unchanged; `htfBias=UNKNOWN`, `structureEvent=null` |
| Web: malformed / mismatched / out-of-sync structure | overlay hidden with note; STRUCTURE tab shows STRUCTURE UNAVAILABLE |

## UNIT TESTS
- `test_structure_engine.py` (15 hand-built scenarios with exact expected events): pivot confirmation, equal highs, EH tolerance, BOS→BOS→CHoCH→MSS, TRANSITIONING, failed CHoCH resumption (protected fallback), direct MSS, wick-only POTENTIAL (once, no trend change), potential MSS, one break per swing, UNCLEAR/RANGING rules, determinism, bearish mirror.
- `test_structure_properties.py` (41): **no-lookahead proof** (every prefix of 5 random series × 3 configs × 2 levels agrees with full history), invariants (a confirmed close really is beyond the level, swings known before use, one break per swing, protected levels unbroken, ambiguity never fires), determinism.
- Field contracts for all structure models (Python + TypeScript), 13 new enums contract-tested.
- Web `structure.test.ts`: payload rejection matrix, overlay sync, overlay construction, overlay resolution, loaders. `shell.test.tsx`: STRUCTURE tab content/eligibility, fail-safe message, hidden overlay note.

## INTEGRATION TESTS
- `test_structure_api.py` (14): structure on all chart timeframes with every anchor present in `/candles`; INVALID withheld; STALE ineligible; 422/404; alignment endpoint; synthetic decision has no context; **eligible structure enriches the decision while verdict/blockers/quality stay identical**; insufficient HTF candles → UNKNOWN; structure failure fails safe; UNAVAILABLE decisions skip structure entirely.
- Manual browser check (2026-09-13, 1600×900, fixture provider): M15 overlay showed swing labels, BOS/CHoCH markers + segments, and a "Protected Low 2022.29" line that matched the STRUCTURE tab. Tab showed "NOT used by the decision: DATA_SYNTHETIC, DATA_STALE", both levels, recent events, MTF table (D1 UNCLEAR due to insufficient candles). Decision htfBias UNKNOWN.

## UI DISPLAY
- **Chart toolbar:** "Structure" (external) and "Internal" toggles, plus an overlay status note when the overlay is hidden.
- **Overlay:** swing labels, BOS/CHoCH/MSS arrows (POTENTIAL in grey with "?"), segments from broken swing to break candle, protected high/low price lines.
- **STRUCTURE tab:** eligibility line, per-level state/trend/protected/bars since break, the last 8 events, MTF alignment table, what the decision actually uses.

## KNOWN LIMITATIONS
- MSS isn't yet qualified by a liquidity sweep or displacement (Phases 3–4). Treat Phase 2 MSS as a structural shift only.
- Pivot lengths, tolerance and ranging thresholds are research defaults, not calibrated on real XAUUSD data (no vendor yet).
- The fixture provides ~40 D1 candles (< minCandles 50), so D1 structure is UNCLEAR and the decision's htfBias can't be exercised with the default config on fixture data; tests use an adjusted config.
- No numeric MTF alignment score (Phase 8 scoring).
- Structure is recomputed on every request (no caching or incremental streaming yet). Measured warm: decision ~120 ms, alignment ~270 ms, single structure 20–60 ms on the fixture. D1 decision/alignment windows are capped at 120 candles.
- Overlay segments are separate line series (≤40 per level). Very dense internal overlays can clutter the chart.
- Inherited: no real vendor, Docker/Postgres/CI not run, polling instead of streaming, UTC-only display.

## STRATEGY VERSION
`0.2.0-phase2`, verdict authority `FAIL_SAFE_ONLY`.

## ADDENDUM — TradingView Desktop timeframe switcher (user request, 2026-09-13)
Not a spec phase. A **control-only** integration with the user's own TradingView Desktop app. Prices are never read from TradingView, so the "TradingView is never a market-data source" rule holds.
- **UI:** a second chart toolbar row, "TradingView", with 19 timeframes in TradingView's toolbar order (1m … M). It reuses the existing `.chart-toolbar` / `.tf-group` / `.tf` / `.badge` styles. It's independent of the in-app M5–D1 switcher, which still drives the engine.
- **Flow:** browser → `GET|POST /api/tradingview/timeframe` (Next.js route handler, Node runtime) → `@modelcontextprotocol/sdk` stdio client → tradingview-mcp bridge (`chart_set_timeframe`, `chart_get_state`) → CDP :9222.
  - The FastAPI backend stays GET-only.
  - The route accepts only localhost requests and allow-listed timeframe codes (422 otherwise).
- **Active timeframe:** always read back from TradingView (`chart_get_state`) after a switch and on the 15 s poll, so changes made inside TradingView appear too. `1D`/`1W`/`1M` are normalised to `D`/`W`/`M`. A mismatch after a switch is shown as an error.
- **Config:** `TRADINGVIEW_MCP_SERVER_PATH` (server-only env; in `apps/web/.env.local` locally). If unset or the bridge fails → NOT CONNECTED, and the buttons are disabled.
- **Tests:**
  - `tradingview.test.tsx`: catalogue, normalisation, loaders, UI states, poll sync.
  - `tradingview-route.test.ts`: allow-list, local-only.
  - `tradingview-bridge.test.ts`: real stdio MCP round-trip against a fake bridge (ok / stuck / error / unconfigured).
- **Live-verified:** against the user's TradingView Desktop (FOREXCOM:XAUUSD): 30→240→D→2D→960→30 via the route, a UI click on 1h, and a TradingView-side change to W reflected by the poll.
- **Limitation:** works only while the web server runs on the same machine as TradingView Desktop. Each call takes ~1.3–1.6 s (the bridge waits for the chart to be ready).
