# Phase 4 — Displacement + FVG/IFVG

## GOAL
Deterministically grade displacement (WEAK / MODERATE / STRONG / EXCEPTIONAL) and detect Fair Value Gaps with
CE, fill %, quality, wick mitigation vs close-through invalidation, and the Inverse FVG lifecycle
POTENTIAL_IFVG / CONFIRMED_IFVG / FAILED_IFVG (spec STEP 2 displacement, STEP 3 FVG/IFVG). Fill the structure
events' `displacementQualifier` and the Master Decision's `displacement`, with no authority over the verdict.

## SCOPE BOUNDARY
- Order Block, Breaker, Mitigation Block, BPR and Volume Imbalance are **Phase 20+ "Advanced PD Arrays"** (spec STEP 15).
- The PD array priority engine, EntryZoneScore, confluence stacking and price-delivery phase depend on those arrays and on entry logic, so they are deferred. Premium/discount and OTE (which need dealing ranges) are deferred with them.
- `MasterDecision.pdArray` stays null until Phase 8 selects an entry PD array.

## INPUTS
- Closed candles of the analysed timeframe (same validated series as chart/structure/liquidity), plus the external structure trend (for quality only).
- `packages/strategy-spec/pd_arrays.json`; ATR period from `structure.json`.

## RULES
1. **Pipeline order:** structure → liquidity → DOL → displacement → FVG/IFVG → structure qualifiers (`services/analysis/pipeline.py`). Liquidity and PD arrays are independent stages: a failure in one withholds only that analysis.
2. **Qualifying candle:** range > 0 and body/range ≥ `minBodyPct` (0.5); BULLISH if close > open, BEARISH if close < open.
3. **Leg:** the run of consecutive qualifying same-direction candles ending at candle i, capped to the last `maxLegCandles` (3).
4. **Magnitude:** `|close[i] − open[leg start]| / ATR_before(leg start)`, where ATR_before averages the 14 true ranges *before* the leg, so a move never inflates its own yardstick. With no prior candle there is no grade.
5. **Grades:** WEAK ≥ 1.0, MODERATE ≥ 1.5, STRONG ≥ 2.0, EXCEPTIONAL ≥ 3.0 ATR. STRONG+ also needs an average leg body ≥ 65%; otherwise the grade is capped at MODERATE.
6. **Displacement events:** emitted at the candle where the current run first reaches a grade, or a higher grade than already emitted in that run. Events never change.
7. **FVG:** bullish when `low[i] > high[i−2]` → zone `[high[i−2], low[i]]`; bearish when `high[i] < low[i−2]` → zone `[high[i], low[i−2]]`. Known at the close of candle i.
   - Gaps smaller than 0.1 × ATR_before(i−2) are ignored (tiny rejection).
   - CE = midpoint.
   - `displacementGrade` = the strongest same-direction displacement on candles i−2..i.
8. **Mitigation** (from the candle after creation; penetration measured by the wick from the entry edge):
   - `fill% = max(penetration / size × 100)`.
   - TOUCHED: the edge is reached within 0.02 ATR, fill < 5%.
   - PARTIAL: fill ≥ 5%. HALF: fill ≥ 50%. FULL: fill ≥ 100%.
   - A wick may fully mitigate: FULL stays valid but is no longer "active".
9. **Invalidation (`CLOSE_THROUGH`, the only implemented rule):** a close beyond the far edge → INVALIDATED (terminal). Any other configured rule is refused at startup.
10. **IFVG:** no automatic conversion. An invalidated FVG spawns POTENTIAL_IFVG in the opposite direction on the same zone. Within 5 bars it becomes:
    - **CONFIRMED_IFVG** if either (a) a displacement of grade ≥ MODERATE in the IFVG direction exists on candles `j−(maxLeg−1)..k` (the inversion move), or (b) 2 consecutive closes beyond the zone (counting the invalidation candle), the latest ≥ 0.25 ATR beyond the edge. The inversion candle itself can confirm.
    - **FAILED_IFVG** on a close back through the far edge ("reclaimed"), or when the window expires ("expired").
    - A potential IFVG is not mitigated. A confirmed IFVG is mitigated and invalidated like an FVG from its own side. IFVGs never invert again.
11. **Active zone:** a valid FVG, or a CONFIRMED IFVG, that isn't FULL or INVALIDATED.
12. **Quality (active zones only; a ranking, not a probability; 0–100):**
    - Size: up to 40, full points at 1.5 ATR.
    - Creating displacement: NONE 0 / WEAK 10 / MODERATE 20 / STRONG 25 / EXCEPTIONAL 30.
    - Freshness: FRESH 20 / TOUCHED 15 / PARTIAL 10 / HALF 5.
    - External trend alignment: 10.
13. **Displacement qualifier:** a structure event is PRESENT if a same-direction MODERATE+ displacement was emitted in the 3 bars up to and including the break candle; otherwise ABSENT. If the PD stage fails, it stays NOT_EVALUATED.
14. **Decision (enrichment only):**
    - Skipped when the gate verdict is UNAVAILABLE.
    - From eligible M15 data, `displacement` = the latest MODERATE+ displacement. No blocker is added; verdict, blockers and quality are unchanged, and the authority guard re-runs.
    - On failure the field is null.
15. **Chart overlay** (hidden with a reason unless chart and PD analysis are READY for the same timeframe with every zone/displacement anchor on the drawn candles):
    - Top and bottom edge segments of the 10 most recent active zones (FVG green/red solid, IFVG purple dashed), drawn from the first zone candle to the last drawn candle.
    - STRONG/EXCEPTIONAL displacement arrows.

## OUTPUTS
- `GET /api/v1/pd-arrays/{symbol}?timeframe=&limit=` → `PdArrayAnalysis {displacements, zones, events, eligibility, …}`.
- Structure events: `displacementQualifier` is PRESENT or ABSENT.
- Master Decision: `displacement`.

## STATES
- DisplacementGrade: WEAK / MODERATE / STRONG / EXCEPTIONAL
- PdArrayType: FVG / IFVG. PdArrayState: FRESH / TOUCHED / PARTIAL / HALF / FULL / INVALIDATED
- IfvgStatus: POTENTIAL_IFVG / CONFIRMED_IFVG / FAILED_IFVG
- PdArrayEventType: CREATED / TOUCHED / PARTIAL_FILL / HALF_FILL / FULL_FILL / INVALIDATED / IFVG_POTENTIAL / IFVG_CONFIRMED / IFVG_FAILED
- The spec's `FAILED` PD-array state is not used by FVGs; IFVG failure is expressed by `FAILED_IFVG`.

## INVALIDATION
- FVG/IFVG: a close beyond the far edge. A wick through only fills.
- Potential IFVG: a close back through the far edge, or the window expiring.
- Displacement events are never invalidated (they are historical facts at their candle).

## ERROR STATES
| Condition | Result |
|---|---|
| Unsupported timeframe | HTTP 422 |
| Unknown symbol | HTTP 404 |
| INVALID / DISCONNECTED data | 200, empty zones/displacements/events, reasons listed |
| FVG/displacement engine throws | PD analysis withheld (`PD_ARRAY_ANALYSIS_FAILED`); structure keeps liquidity qualifiers, displacement NOT_EVALUATED; liquidity unaffected |
| Displacement step throws during decision | decision unchanged except `displacement=null` |
| Unknown invalidation rule in spec | configuration load fails (refuses to start) |
| Web: malformed / inconsistent payload | overlay hidden with note; PD_ARRAYS tab shows PD ARRAYS UNAVAILABLE |

## UNIT TESTS
- `test_pd_arrays_engine.py` (25 hand-built scenarios on a flat base where ATR = 1.0, so grades and fills are hand-computed):
  - all 4 grades plus the below-WEAK case, the body-quality cap, multi-candle leg emit-and-upgrade, a small body breaking the run, bearish mirror, no grade without history;
  - bullish/bearish FVG, tiny rejection, TOUCHED/PARTIAL/HALF/FULL/untouched, wick-full stays valid, close-through → potential only;
  - IFVG confirmed by displacement, confirmed by acceptance, failed by reclaim, failed by expiry, confirmed IFVG mitigated then invalidated (and never re-inverted);
  - trend affects quality only; displacement qualifier PRESENT/ABSENT/grade/lookback/no-lookahead.
- `test_pd_arrays_properties.py` (11):
  - **No-lookahead:** every prefix of 5 random series gives the same displacements, events and known zones.
  - Lifecycle grammar per zone: FVG `C T? P? H? F? I?`, IFVG `O (X T? P? H? F? I? | Z)?`.
  - Price invariants (invalidation closes beyond, FULL wick reaches the far edge, FVG gap condition holds, 0 ≤ fill ≤ 100, quality iff active).
  - Coverage check: the random data exercises every event type.
- Field contracts (Python + TypeScript) for 4 models, 5 new enums and `PD_ARRAY_ANALYSIS_FAILED`. Config refuses a non-CLOSE_THROUGH rule.
- Web `pdArrays.test.tsx` (17): rejection matrix (10), overlay content (active only, IFVG styling, STRONG+ markers), zone cap, sync/hide, loader, PD_ARRAYS tab content/ordering/fail-safe, overview displacement.

## INTEGRATION TESTS
`test_pd_arrays_api.py` (15):
- Endpoint on M5/M15/H1 consistent with `/candles` (all anchors present, zone and event references valid).
- Structure events carry both qualifiers (some PRESENT).
- INVALID withheld; 422/404.
- A PD failure is isolated from structure and liquidity.
- **Displacement enrichment is verdict-neutral** (verdict, blockers and quality identical; `pdArray` stays null).
- Decision-time failure fails safe; a synthetic decision gets no displacement; field contracts; config rule refusal.

**Manual browser check** (2026-09-13, fixture provider, 1600×900, M15, structure/liquidity overlays toggled off):
- FVG/IFVG edge lines and "Disp S/X" markers drawn.
- The PD_ARRAYS tab listed 11 active zones ordered by quality, with states/fill %, 0 potential IFVGs and recent MODERATE+ displacement, marked "NOT used by the decision: DATA_SYNTHETIC, DATA_STALE".
- The TradingView Desktop row still showed CONNECTED.

## UI DISPLAY
- **Chart toolbar:** "FVG" toggle (on by default).
- **Overlay:** active FVG edges (green bullish / red bearish, solid), confirmed IFVG edges (purple, dashed), "Disp S" / "Disp X" arrows for STRONG / EXCEPTIONAL displacement.
- **PD_ARRAYS tab:** eligibility; active FVG/IFVG (top 8 by quality: direction, type, bounds, IFVG status, state, fill %, quality); potential IFVGs awaiting confirmation; recent MODERATE+ displacement; what the decision uses.
- **OVERVIEW:** the decision's displacement. **STRUCTURE tab:** events marked "with displacement".

## KNOWN LIMITATIONS
- **Uncalibrated thresholds:** all thresholds and quality weights are research defaults, not calibrated on real XAUUSD (still no vendor). On the synthetic fixture (M15, 300 candles) 44 of 52 FVGs were invalidated and 30 of 44 potential IFVGs confirmed (68%). That high confirmation rate suggests the acceptance rule (2 closes, 0.25 ATR) may be too permissive and must be reviewed against real data before any IFVG entry model (Phase 8) relies on it.
- **Zones are drawn as edge lines,** not filled rectangles (Lightweight Charts has no rectangle primitive; a custom series primitive could be added later).
- **Candle-resolution only:** mitigation uses candle extremes and closes; intrabar sequence is unknown. Displacement ignores gaps between candles (no open-vs-previous-close component).
- **No cross-timeframe FVG priority, confluence stacking or entry-zone scoring** (later phases).
- **Decision latency** on the fixture: warm ~0.4 s, cold ~1.5 s (multi-timeframe pipeline, still no caching). A PD-arrays request is ~70 ms warm, ~1.1 s on a cold process.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling, UTC-only display.

## STRATEGY VERSION
`0.4.0-phase4`, verdict authority `FAIL_SAFE_ONLY`.
