# Phase 17 — Analytics V1

## GOAL
Describe recorded history (spec STEP 10 analytics):
- win rate, average and total R, expectancy, profit factor;
- max drawdown and recovery, duration;
- performance by asset, session, setup, timeframe and day;
- No Wick and liquidity performance, DOL accuracy, rule violations;
- spec sample-size labels on every figure.

Analytics reads only **verified closed** journal trades or paper sims. It is deterministic and read-only, with `authority: DESCRIPTIVE_ONLY`, and it never produces a verdict, probability, forecast, guarantee or trade signal (spec STEP 17). Verdict authority stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **Alert usefulness is not measured.** Alerts are in memory and not linked to records, so it is reported as UNAVAILABLE with the reason.
- **Backtesting** (18), **replay** (19), A/B, walk-forward and Monte Carlo are not built.
- **No cross-source mixing:** JOURNAL and PAPER are separate reports. There are no money figures (R only) and no calibration of engine parameters from these statistics.
- **Premium/discount** performance is not available (no engine computes it).

## INPUTS
- **Journal (Phase 15):** TRADE entries with a latest outcome; snapshot summary fields (sessions, setup type, execution timeframe, day, No Wick, liquidity event, DOL); fill direction and entry; outcome R, result, class, violations, duration, MFE/MAE, efficiencies. NO_TRADE / MISSED_ENTRY counted as decision records.
- **Paper (Phase 16):** CLOSED sims; net R after assumed costs; the same summary fields; MFE price derived from MFE R, entry and stop.
- **Filters:** `source` (JOURNAL default | PAPER), `symbol`, `from`/`to` (by close time), `includeSynthetic` (default false), `strategyVersion`.
- **Config (new):** `packages/strategy-spec/analytics.json`: sample-size thresholds (LIMITED 30, MODERATE 100, STRONGER_EVIDENCE 300), best-group minimum label LIMITED, equity curve max 500 points, the disclaimer text.

## RULES
1. **Eligibility:**
   - TAMPERED records (snapshot, any outcome revision, sim record or event) are always excluded and counted;
   - open trades and pending/expired/cancelled sims are excluded as `notClosed`;
   - synthetic records are excluded unless `includeSynthetic` (then `includesSynthetic` is true);
   - filtered records are counted.
2. **Win / break-even / loss:**
   - win = class VALID_WIN or BAD_PROCESS_WIN (R above the break-even tolerance);
   - break-even = a non-win with |R| ≤ 0.1 or result BREAK_EVEN;
   - loss = the rest.
3. **R metrics** (records with defined R):
   - average and total R; average win R and average non-win R;
   - expectancy = p × avg win R + (1 − p) × avg non-win R (equals average R);
   - profit factor = Σ positive R / |Σ negative R|, null without negative R.
4. **Sample-size label** (spec) on every group: < 30 INSUFFICIENT, 30–99 LIMITED, 100–299 MODERATE, 300+ STRONGER_EVIDENCE.
5. **Drawdown:**
   - cumulative R from 0 in close order;
   - the largest peak-to-trough fall with peak and trough times;
   - recovery time and number of records until the peak was regained (null if not recovered);
   - equity curve downsampled to at most 500 points.
6. **Breakdowns:** ASSET, SESSION (active sessions joined, NONE), SETUP_TYPE, TIMEFRAME, DAY_OF_WEEK, NO_WICK (context summary or NONE), LIQUIDITY_EVENT (event type token such as SWEEP), DIRECTION, RESULT.
   - A **best** group (highest expectancy) is named only among groups with a LIMITED+ sample and when at least two groups exist.
   - Otherwise best is null with the reason. It never names a "best" on an insufficient sample.
7. **Process:** class counts, violation frequency, and group stats with vs without violations.
8. **DOL accuracy:**
   - parses the snapshot DOL "… BSL|SSL … @ price";
   - aligned = BSL for longs / SSL for shorts;
   - for aligned records whose DOL lies **ahead of the entry**, reached = the most favourable price got to the DOL price;
   - reports evaluated, reached, rate and label; aligned vs against group stats; unknown DOL count.
9. **Assistant:**
   - the `query_journal` payload adds `statistics` (closed trades, sample label, win rate, expectancy R, total R, disclaimer) for the conversation's market;
   - the JOURNAL answer states them with the label and disclaimer, and adds "Performance edge: UNKNOWN" when INSUFFICIENT.

## OUTPUTS
- `GET /api/v1/analytics?source&symbol&from&to&includeSynthetic&strategyVersion` → `AnalyticsReport`:
  - filters, available/reason;
  - overall `GroupStats`, drawdown, durations, MFE/MAE, efficiencies;
  - breakdowns, process, DOL, alert usefulness (unavailable), decision records;
  - excluded counts, includesSynthetic, strategy versions, equity curve, disclaimer, authority.
- Enums: `SampleSizeLabel`, `AnalyticsSource`.
- Contract fields: `GroupStats`, `Breakdown`, `Drawdown`, `EquityPoint`, `ProcessStats`, `DolAccuracy`, `Unavailable`, `AnalyticsFilters`, `ExcludedCounts`, `AnalyticsReport`.

## STATES
- Sample-size label: INSUFFICIENT / LIMITED / MODERATE / STRONGER_EVIDENCE.
- Report available / unavailable (store not configured or unreachable).

## INVALIDATION
- Computed on request from the stored records. A new outcome revision replaces that trade's contribution (latest revision). A tampered record drops out.

## ERROR STATES
| Condition | Result |
|---|---|
| Journal / paper store unconfigured or unreachable | `available: false` with the reason; empty statistics |
| No eligible records | counts 0, INSUFFICIENT, nulls |
| Invalid query (symbol pattern, dates, source) | 422 |
| Web: authority other than DESCRIPTIVE_ONLY, missing disclaimer, label not matching count, counts not adding up, win rate not matching counts, negative profit factor, a best group below LIMITED, statistics from an unavailable store | rejected (UNAVAILABLE with the reason) |

## UNIT TESTS
`tests/unit/test_analytics_engine.py` (14):
- 7 spec label boundaries; threshold guard;
- group stats (W/L/BE, win rate, total and average R, expectancy = average R, profit factor incl. break-even R, no-loss and empty cases);
- drawdown peak/trough/recovery, unrecovered, empty;
- best-group gating (small samples, LIMITED vs higher-expectancy INSUFFICIENT, single group);
- process split; DOL parsing and accuracy (aligned only, DOL behind entry not evaluated);
- durations and downsampling.

## INTEGRATION TESTS
`tests/integration/test_analytics_api.py` (16):
- unconfigured stores unavailable;
- journal statistics from 4 closed trades (counts, R, profit factor, drawdown recovery, equity curve, decision records, open trade excluded, alert usefulness unavailable, no best on 4 records, process counts, disclaimer, sources not mixed, symbol and date filters);
- **tampered outcome excluded**;
- paper net R of closed sims (expired excluded);
- synthetic excluded unless requested;
- assistant journal answer with labelled statistics;
- 10 contract-field cases.

Web `tests/analytics.test.tsx` (13): label mirror; consistent report accepted; 8 rejections; Analytics tab from `#analytics` with disclaimer, labels, drawdown, breakdown reason and equity curve; source, symbol and synthetic reloads; unavailable and rejected reports.

## UI DISPLAY
- **Journal view tabs:** Records · Paper sims · **Analytics** (`#analytics`).
- **Controls:** source (Journal trades / Paper sims), current market only, include synthetic data.
- **Disclaimer** at the top.
- **Overview:**
  - closed records with label and exclusions;
  - W/L/BE and win rate;
  - total/average/expectancy R, average win/non-win R, profit factor;
  - max drawdown and recovery;
  - durations, MFE/MAE, efficiencies;
  - DOL accuracy with labels (aligned vs against, with counts and labels);
  - alert usefulness UNAVAILABLE; decision records.
- **Equity curve** (cumulative R, zero line).
- **Process:** classes, with vs without violations (counts and labels), violation frequency.
- **Breakdown tables:** group, records with label, W/L/BE, win rate, expectancy, total R, PF; caption with best group or the reason none is named.

## KNOWN LIMITATIONS
- ⚠️ **Descriptive only.** Past records do not predict results, and all smoke/test data is synthetic or hand-made. No conclusion should be drawn below LIMITED, and even STRONGER_EVIDENCE is not a guarantee.
- ⚠️ **Journal quality depends on the user.** Manual fills and outcomes are self-reported. Snapshots logged POST_ENTRY are included (their timing is visible per record, not filtered).
- **Paper R depends on assumed costs** and bar-level simulation (Phase 16).
- **Alert usefulness unavailable** until alerts are persisted and linked. Premium/discount not available.
- **Session / No Wick / liquidity groupings** use the snapshot summary strings (coarse). DOL accuracy only covers aligned records with a parseable DOL ahead of the entry.
- **Everything is computed on each request** (no caching); very large journals will be slow.

## STRATEGY VERSION
`0.17.0-phase17` (phase 17, "Analytics V1"). Engines: + `analytics`. Verdict authority: **FAIL_SAFE_ONLY**.
