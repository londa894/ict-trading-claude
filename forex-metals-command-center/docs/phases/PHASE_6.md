# Phase 6 — Session & Time

## GOAL
Build the deterministic Session, Time & Kill Zone engine (spec STEP 4), in New York time and DST-aware:
- a time-only session clock;
- session highs, lows, midpoints and ranges;
- daily, New York midnight and weekly opens, plus the previous session's high/low;
- Asian range state, ADR and expansion state, session quality;
- Judas swings.

Session highs/lows also feed the liquidity engine (deferred from Phase 3), and time quality feeds No Wick's SESSION context. The Master Decision gets `sessionState` as context only. **Time never creates a trade by itself.** The verdict stays `FAIL_SAFE_ONLY`.

## SCOPE BOUNDARY
- **PO3, session-target completion and setup expiration** need setups, so they move to the Phase 7 setup state machine.
- **Holidays and early closes** are not modelled (as in Phase 0). A holiday session shows as INCOMPLETE (fail-safe) and is never used.
- **User time zone:** only display formatting exists (a chart axis selector: UTC / New York / London / browser local). There are no user settings yet (no auth).
- **Session quality adds no blocker.** Verdict authority is Phase 8.

## INPUTS
- **Source:** closed M15 candles (`sessions.json` `sourceTimeframe`).
  - 1000 bars (~10 trading days) for the sessions API and decision.
  - 400 bars for the liquidity pools inside the shared pipeline.
- **ADR:** closed New York-close D1 candles.
- **Config:**
  - `packages/strategy-spec/sessions.json` (new);
  - `market_hours.json` (market open/closed);
  - `no_wick.json` (session points);
  - `liquidity.json` (magnet weight `SESSION`).

## RULES
1. **Time base:** everything is stored in UTC.
   - Windows are New York wall-clock times, converted with the IANA database (`zoneinfo`).
   - Trading day D runs from (D−1) 17:00 to D 17:00 New York, the same as D1 candles.
   - A window that starts at or after 17:00 belongs to the next trading day (e.g. Asia 20:00 on Monday evening belongs to Tuesday).
2. **Windows (research defaults, New York time):**

   | Kind | Name | Window |
   |---|---|---|
   | Session | ASIA | 20:00–00:00 |
   | Session | LONDON | 02:00–05:00 |
   | Session | NY_AM | 08:30–12:00 |
   | Session | NY_PM | 13:30–16:00 |
   | Session | LONDON_CLOSE | 10:00–12:00 |
   | Kill zone | LONDON_KZ | 02:00–05:00 |
   | Kill zone | NY_AM_KZ | 07:00–10:00 |
   | Kill zone | NY_PM_KZ | 13:30–16:00 |

   - Windows are `[start, end)`: the start is inclusive, the end exclusive.
   - When the config loads, every boundary must align to M15, no window may be empty, and no window may cross the 17:00 roll. Otherwise startup is refused.
3. **London DST gap weeks:** because windows are in New York time, their London wall time shifts during the gap weeks (tested). This is intended.
4. **Time quality** at an instant:
   - AVOID if the market is not OPEN (weekend, metals daily break);
   - otherwise the best quality of the active windows: LONDON_KZ / NY_AM_KZ are IDEAL; NY_PM_KZ / LONDON_CLOSE are ACCEPTABLE; ASIA is LOW_QUALITY;
   - outside every window, LOW_QUALITY.
5. **Clock** (time only, no market data):
   - New York and London local time, trading day, market status;
   - active sessions and kill zones, time quality;
   - next session start (skipping closed-market windows such as the weekend).
6. **Session instances**, computed as of `asOf` = the latest closed source candle's close:
   - The window uses only candles fully inside it.
   - **Expected candles** are M15 slots in the window while the market is open. A window with no open slots produces no instance.
   - **States:**

     | State | Condition | Levels |
     |---|---|---|
     | NOT_STARTED | asOf ≤ start (only for the asOf trading day) | none |
     | FORMING | start < asOf < end | provisional, from candles closed by asOf |
     | COMPLETE | asOf ≥ end and every expected candle present | `knownAt = end` |
     | INCOMPLETE | asOf ≥ end with missing candles | shown, never used for pools, range state or Judas |

   - Each instance carries high, low, midpoint, range, and the high/low candle times.
7. **Opens** come from the exact expected candle only; a missing candle gives null, never a substitute.
   - Daily open = the first market-open slot of the trading day (18:00 for metals).
   - New York midnight open = the 00:00 candle.
   - Weekly open = the first open slot of the trading week (Sunday 18:00).
   - Daily change = last close − daily open.
8. **Previous session:** the latest COMPLETE instance that ended by asOf.
9. **Asian range state** (COMPLETE ASIA only): ratio = range ÷ mean of the up-to-10 **earlier** COMPLETE Asian ranges, needing at least 5 (otherwise null).
   - TIGHT below 0.6.
   - NORMAL below 1.3.
   - EXPANDED below 2.0.
   - ABNORMALLY_LARGE otherwise.
10. **ADR:**
    - ADR = mean(high − low) of the last 14 closed D1 candles that closed at or before the start of the current trading day. Fewer than 14 means no ADR (never a partial average).
    - Current range = the current trading day's range from closed M15 candles.
    - `pctUsed` = current ÷ ADR × 100.
    - Expansion: CONSOLIDATING below 25, EARLY below 50, ACTIVE below 80, LATE below 110, EXHAUSTED otherwise.
11. **Session quality** = the clock's time quality, downgraded one level when expansion is EXHAUSTED. It is null when data is unusable.
12. **Judas swing**, for each LONDON / NY_AM instance whose trading day has a COMPLETE Asia known before the window opens. Candles in the window are scanned in order:
    - The first candle trading beyond an Asian extreme decides:
      - wick above the high, close ≤ high, low not below the Asian low → CANDIDATE, BEARISH;
      - the mirror case → CANDIDATE, BULLISH;
      - a close beyond, or both sides in one candle → no Judas swing for that instance.
    - On a later candle, a close beyond the sweep extreme → FAILED; a close beyond the Asian midpoint in the Judas direction → CONFIRMED.
    - Still CANDIDATE when the window has ended (as of the data) → FAILED ("window ended").
13. **Liquidity session pools:**
    - The latest 2 COMPLETE instances of ASIA / LONDON / NY_AM / NY_PM become pools: `{ASIA,LONDON,NY_AM,NY_PM}_{HIGH,LOW}` (BSL / SSL, scope EXTERNAL).
    - They are known at window end and activate on the first analysed candle opening at or after it. They use the Phase 3 state machine.
    - Magnet type weight is `SESSION` = 20.
    - If the session source is missing or unusable, liquidity is ineligible with `SESSION_LEVELS_UNAVAILABLE`, and the other stages are unaffected.
14. **No Wick SESSION component** (changes Phase 5 scoring):
    - For M5/M15/H1, points come from the time quality of the candle's open time (IDEAL 10 / ACCEPTABLE 5 / otherwise 0). This is time only, so there is no lookahead.
    - On H4/D1 it stays NOT_EVALUATED, because a 4h or daily candle spans several windows.
    - To keep the evaluated weights at 100, FVG and TREND drop from 15 to 10 points each.
15. **Decision (enrichment only):**
    - Skipped when the gate verdict is UNAVAILABLE.
    - From **eligible** session data, `sessionState` = `{tradingDay, marketStatus, activeSessions, activeKillZones, timeQuality, sessionQuality, asianRangeState, adrPctUsed, expansion, judas, authority: "CONTEXT_ONLY"}`.
    - No blocker is added. Verdict, blockers and quality are unchanged, and the authority guard re-runs.
    - On failure the field is null.
16. **Chart:**
    - A "Sessions" toggle (off by default; M5/M15/H1 only) draws high/low segments for the latest 12 COMPLETE (solid) and FORMING (dashed) ASIA / LONDON / NY_AM / NY_PM instances.
    - Segments snap to drawn candles (first candle opening inside the window → last one before its end). A window before the first drawn candle, or containing no drawn candle, is skipped rather than extrapolated.
    - The "Times" selector changes only the axis and crosshair labels. Candle times stay UTC, and Lightweight Charts still places day ticks by UTC.

## OUTPUTS
- `GET /api/v1/sessions/{symbol}` → `SessionAnalysis {clock, sessionQuality, instances, opens, previousSession, adr, judas, eligibility, …}`.
- Liquidity pools gain 8 session types; No Wick events gain an evaluated SESSION component on M5/M15/H1.
- Master Decision: `sessionState` (typed as `SessionDecisionState` in shared-types).
- System status: phase 6, `sessions` enabled.

## STATES
- SessionName: ASIA / LONDON / NY_AM / NY_PM / LONDON_CLOSE.
- KillZone: LONDON_KZ / NY_AM_KZ / NY_PM_KZ.
- SessionInstanceState: NOT_STARTED / FORMING / COMPLETE / INCOMPLETE.
- AsianRangeState: TIGHT / NORMAL / EXPANDED / ABNORMALLY_LARGE.
- SessionQuality: IDEAL / ACCEPTABLE / LOW_QUALITY / AVOID.
- ExpansionState: CONSOLIDATING / EARLY / ACTIVE / LATE / EXHAUSTED.
- JudasStatus: CANDIDATE / CONFIRMED / FAILED.
- LiquidityPoolType gains ASIA_HIGH / ASIA_LOW / LONDON_HIGH / LONDON_LOW / NY_AM_HIGH / NY_AM_LOW / NY_PM_HIGH / NY_PM_LOW.
- AnalysisIneligibility gains SESSION_LEVELS_UNAVAILABLE and SESSION_ANALYSIS_FAILED.

## INVALIDATION
- A FORMING instance's levels are provisional until its window ends. INCOMPLETE instances are never promoted.
- A Judas CANDIDATE is invalidated (FAILED) by a close beyond the sweep extreme or by the window ending.
- Session liquidity pools are taken by the Phase 3 rules (SWEEP / BREAK / RUN / RECLAIM).
- Quality and expansion are as-of values, recomputed on every request; they are never stored as facts.

## ERROR STATES
| Condition | Result |
|---|---|
| Unknown symbol | HTTP 404 |
| Misaligned / empty / roll-crossing window in `sessions.json` | configuration load fails (refuses to start) |
| INVALID / DISCONNECTED session source | 200: clock present (time only); no instances, opens, ADR or Judas; `sessionQuality` null; reasons listed |
| Synthetic / stale source | analysis shown, ineligible; decision `sessionState` null |
| Fewer than 14 closed D1 candles, or unusable D1 | `adr` null (session quality = time quality) |
| Session engine throws | `SESSION_ANALYSIS_FAILED`; clock still present |
| Session source missing in the pipeline | liquidity `SESSION_LEVELS_UNAVAILABLE`; structure, PD arrays, No Wick unaffected |
| Session step throws during decision | decision unchanged except `sessionState=null` |
| Web: malformed / inconsistent payload | overlay hidden with a note; SESSION tab shows SESSION DATA UNAVAILABLE; status bar Session and Daily change show UNAVAILABLE |
| Web: unexpected `sessionState` shape or authority ≠ CONTEXT_ONLY | treated as absent ("—") |

Examples of inconsistent payloads the web rejects: levels on a NOT_STARTED instance, COMPLETE without all candles or `knownAt ≠ end`, Asian range state on a non-Asian or non-complete session, a Judas swing whose resolution doesn't match its status or whose midpoint lies outside the range.

## UNIT TESTS
- `test_sessions_clock.py` (25):
  - window bounds across US DST start and end;
  - trading-day roll at 17:00 in EST and EDT;
  - London time in the DST gap weeks vs after;
  - active sessions, kill zones and time quality at 8 instants (including the daily break and Saturday);
  - `[start, end)` boundaries; next session skipping the weekend;
  - quality helpers;
  - config refusal of misaligned, roll-crossing and empty windows.
- `test_sessions_levels.py` (26), hand-built:
  - Asia FORMING → COMPLETE with exact levels and `knownAt`;
  - a missing candle makes it INCOMPLETE and excluded from pools;
  - latest-2 pool selection;
  - an ASIA_HIGH BSL pool not active inside its own window, then swept in London;
  - exact opens, and null when the candle is missing;
  - previous session;
  - Asian range state using only earlier sessions (4 prior → null, TIGHT, ABNORMALLY_LARGE) and thresholds;
  - ADR ignoring a future D1 candle and requiring the full period; expansion thresholds;
  - Judas bearish and bullish CONFIRMED, FAILED by extreme, FAILED by window end, CANDIDATE before the end;
  - no Judas on a breakout, a two-sided candle, or an INCOMPLETE Asia;
  - session quality downgraded on EXHAUSTED.
- `test_sessions_properties.py` (13):
  - **No-lookahead** on every 37th prefix of 5 random 2-week walks: passing all candles with an earlier asOf equals passing the prefix; COMPLETE/INCOMPLETE instances equal the full run; prefix Judas swings agree (a candidate resolves after asOf); pool levels are known by asOf.
  - Instance and Judas invariants.
  - Random data produces CONFIRMED and FAILED Judas swings.
  - ADR is unchanged by D1 candles after asOf.
  - No Wick's SESSION component only reads candle times.
- **Contracts:** 7 new enums plus pool types and ineligibility values checked from Python and TypeScript; wire fields of 7 models in `api_fields.json` checked from both sides (shared-types 83 tests, was 69).
- **Web `sessions.test.tsx` (24):**
  - rejection matrix (13);
  - overlay (solid/dashed, snapping, no extrapolation, off by default, hidden on H4, unavailable);
  - time-zone formatting (New York / London / UTC in the gap week);
  - loader;
  - `sessionState` validation;
  - status bar Session and Daily change;
  - SESSION tab content, including stale data showing the latest data trading day;
  - fail-safe.

## INTEGRATION TESTS
`test_sessions_api.py` (18):
- The endpoint on the fixture returns a closed-market clock (AVOID, next ASIA), all 5 sessions with consistent COMPLETE instances, opens, ADR and previous session; 404.
- INVALID data keeps the clock with no levels.
- Engine failure is reported, not raised.
- **Liquidity gets all 8 session pool types** (M5, H1), all known by the last close.
- No Wick SESSION is EVALUATED on M5/H1 and NOT_EVALUATED on H4.
- A missing session source makes only liquidity ineligible.
- **Enrichment is verdict-neutral** (verdict, blockers and quality identical; CONTEXT_ONLY).
- Failure fails safe; a synthetic decision gets no `sessionState`; field contracts.

Earlier tests updated: pipeline tests now pass the M15 session source; `phase == 6`; version literals; status-bar N/A count (Session and Daily change are now real fields).

**API smoke** (2026-09-13, fixture provider, real clock):
- Sessions: 200, ~225 ms first call, ~125 ms warm.
- Clock (Sunday 20:53 New York): ASIA, LOW_QUALITY, next LONDON.
- As of 2024-04-19 17:00 New York: 55 instances (54 COMPLETE, 1 INCOMPLETE Asia at the window start), 5 Asian range states.
- Opens 2041.5 / 2039.63 / 2049.36, daily change +12.26.
- ADR 19.05, 99.5% used, LATE; 8 Judas swings (3 CONFIRMED, 5 FAILED).
- Liquidity on M5/M15/H1/H4: 16 session pools each (12 taken on M5/M15/H1, 10 on H4).
- No Wick SESSION evaluated on M5/M15/H1 (0/5/10 points), NOT_EVALUATED on H4.
- BTCUSD → 404; synthetic decision UNAVAILABLE with `sessionState` null; status phase 6, `sessions` enabled.

**Manual browser check** (1600×900, M15, other overlays off, Times = New York):
- Session high/low segments drawn and the axis labelled in New York time.
- Status bar: "ASIA · LOW_QUALITY", daily change "+12.26 (+0.60%)".
- The SESSION tab matched the API.
- **Defect found and fixed:** with stale data, the sessions table filtered on the clock's trading day and rendered empty. It now shows the latest trading day in the data, marked "(latest in data)" (regression test added).

## UI DISPLAY
- **Status bar:** "Session (NY)" from the time-only clock (active windows · time quality); "Daily change (M15)" from the daily open. UNAVAILABLE when the payload is not trusted.
- **Chart toolbar:** "Sessions" toggle (off by default) and a "Times" selector (UTC / New York / London / Local).
- **SESSION tab:**
  - clock (New York / London time, trading day, market, active windows, time quality, next session);
  - eligibility;
  - the latest data trading day's sessions (state, low–high, range, Asian range state);
  - session quality, opens, previous session, ADR and expansion;
  - recent Judas swings;
  - decision context and the disclaimer that time never creates a trade.
- **OVERVIEW:** "Session (context only)" row from the validated `sessionState`.
- **NO_WICK tab:** SESSION points now shown (time quality); only NEWS remains n/e.

## KNOWN LIMITATIONS
- **Uncalibrated:** window times, kill zones, quality mapping, Asian range thresholds, ADR period and expansion thresholds are research defaults, not calibrated on real XAUUSD (still no vendor). NY_AM and LONDON_CLOSE overlap by design.
- **Holidays / early closes not modelled:** an affected session is INCOMPLETE and unused. A holiday D1 candle still counts in ADR.
- **Judas swing is a simple, explicit rule** (Asia sweep plus midpoint confirmation), not a discretionary read. On synthetic data 5 of 8 failed.
- **M15 resolution:** sessions cannot start or end between 15-minute marks; intrabar order is unknown.
- **Opens use closed candles:** the daily open appears once the first candle of the day has closed.
- **Chart time zone is display-only:** tick placement follows UTC days, and there is no persisted user preference (no auth).
- **Latency:**
  - Every pipeline run now also loads 400 M15 bars for session pools.
  - The decision adds a sessions pass (1000 M15 bars plus D1).
  - Still no caching; decision latency not re-measured this phase.
- **Inherited:** no real vendor, Docker/Postgres/CI not run, polling.

## STRATEGY VERSION
`0.6.0-phase6`, verdict authority `FAIL_SAFE_ONLY`. No Wick and liquidity scoring changed (rules 13–14), which is why this version bump covers them.
