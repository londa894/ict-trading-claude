# CODEX MASTER BUILD SPECIFICATION
## Forex & Metals ICT/SMC Trading Command Center
### Rebuild From Scratch — Do Not Reuse Prior Generated Code

This file is the source of truth for Codex. Build a new repository from an empty directory. Do not import, copy, or depend on any previously generated Phase 0–5 code.

## Global non-negotiables

```text
PRODUCT: AI-assisted Forex & Metals ICT/SMC Trading Intelligence Command Center
PRIMARY MARKET: XAUUSD
SECONDARY: XAGUSD, EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD, then selected crosses
VERDICTS: LONG / SHORT / WAIT / NO TRADE / UNAVAILABLE
BROKER CONNECTION: NONE
ORDER EXECUTION: NONE
MARKET DATA: Independent provider behind provider abstraction
TRADINGVIEW: Renderer only; never market-data source
TRADING LOGIC: Deterministic, testable code
AI ROLE: Explain, summarize, query, teach, interpret structured outputs
FAIL-SAFE: Critical uncertainty -> WAIT / BLOCKED / UNAVAILABLE
BUILD ORDER: trustworthy data -> deterministic analysis -> state engine -> AI explanation
```

# STEP 1 — Master System Identity / Markets / Methodology

Build an institutional-style decision-support platform. It must answer:

```text
1. What is the HTF narrative?
2. Where is price located?
3. Where is liquidity?
4. What is the DOL?
5. Has liquidity been taken?
6. Did price displace?
7. Did structure confirm?
8. Which PD Array matters?
9. Did a meaningful No Wick event occur?
10. Is time/session supportive?
11. Is macro supportive/conflicting?
12. Has price retraced to valid location?
13. Has entry confirmed?
14. Is R:R acceptable?
15. Does risk approve?
16. Are there blockers?
17. LONG / SHORT / WAIT / NO TRADE?
```

Timeframe hierarchy:

```text
Monthly/Weekly/Daily = macro narrative
Daily/4H/1H = HTF structure/location
30M/15M = confirmation
5M = primary execution
3M = optional refinement
1M = advanced refinement only; never narrative authority alone
```

XAUUSD default: 15M confirmation + 5M execution.

Core concepts: HH/HL/LH/LL, BOS, CHoCH, MSS, protected high/low, BSL/SSL, internal/external liquidity, EQH/EQL, PDH/PDL/PWH/PWL, session highs/lows, premium/discount/equilibrium, FVG/IFVG, OB, breaker, mitigation block, BPR, volume imbalance, OTE, PO3, Judas Swing, kill zones, DOL, SMT, No Wick Architecture.

# STEP 2 — Market Structure & Liquidity Intelligence Engine

Deterministically detect:

```text
Swing High / Swing Low
HH / HL / LH / LL / EH / EL
Internal / External Structure
Protected High / Protected Low
BOS / CHoCH / MSS
States: BULLISH / BEARISH / RANGING / TRANSITIONING / UNCLEAR
```

Rules:

```text
BOS = continuation break
CHoCH = early behavioral shift
MSS = stronger shift, preferably after liquidity + displacement
Default = candle-close confirmation
Wick-only = POTENTIAL, not confirmed by default
```

Displacement grades: WEAK / MODERATE / STRONG / EXCEPTIONAL.

Liquidity detect:

```text
BSL / SSL
Internal / External Liquidity
EQH / EQL
PDH / PDL / PWH / PWL
Asian / London / New York High-Low
```

Liquidity states: FRESH / APPROACHING / TOUCHED / SWEPT / RUN / BROKEN / RECLAIMED.

Sweep = trades through liquidity then fails to accept beyond and closes back inside according to configured rule.
Run = closes/accepts beyond with continuation.

Add LiquidityMagnetScore 0–100, PrimaryDOL, SecondaryDOL, DOL confidence, chronological event log, multi-timeframe structure alignment score.
If DOL unclear -> DOL_UNCLEAR + WAIT.

# STEP 3 — PD Array & Price Delivery Engine

Implement/rank:

```text
FVG / IFVG / Order Block / Breaker / Mitigation Block / BPR / Volume Imbalance
Premium / Discount / OTE / No Wick Rebalance Zone
```

Suggested object:

```typescript
type PDArray = {
  id: string;
  symbol: string;
  timeframe: string;
  type: "FVG"|"IFVG"|"ORDER_BLOCK"|"BREAKER"|"MITIGATION_BLOCK"|"BPR"|"VOLUME_IMBALANCE"|"NO_WICK";
  direction: "BULLISH"|"BEARISH";
  high: number;
  low: number;
  midpoint: number;
  createdAt: string;
  freshness: number;
  mitigationPct: number;
  state: "FRESH"|"TOUCHED"|"PARTIAL"|"HALF"|"FULL"|"FAILED"|"INVALIDATED";
  qualityScore: number;
  timeframeWeight: number;
};
```

FVG: classic 3-candle detection, store high/low/CE/fill%/quality. Wick may fully mitigate; structural invalidation requires configured close-through rule.
IFVG: do not auto-convert every failed FVG. Require meaningful acceptance/inversion. States POTENTIAL_IFVG / CONFIRMED_IFVG / FAILED_IFVG.
Premium/discount: meaningful dealing range with range high/premium/equilibrium/discount/range low. Support nested ranges.
OTE research defaults 62%, 70.5%, 79%; supporting confluence only.
Add PD Array priority engine, EntryZoneScore 0–100, confluence zone stacking, and price-delivery phase: ACCUMULATION / RAID / DISPLACEMENT / RETRACEMENT / MITIGATION / EXPANSION / TARGETING_LIQUIDITY / DISTRIBUTION / RANGING / UNCLEAR.

# STEP 4 — Session, Time & Kill Zone Intelligence Engine

Use UTC internally. Convert using proper timezone DB to America/New_York, Europe/London, and user timezone. Must be DST-aware.

Track:

```text
Asia / London / New York AM / New York PM / London Close / Kill Zones
Asian High/Low/Midpoint/Range
London High/Low
NY High/Low
Daily Open
NY Midnight Open
Weekly Open
Previous Session High/Low
```

Asian range states: TIGHT / NORMAL / EXPANDED / ABNORMALLY_LARGE.
Session quality: IDEAL / ACCEPTABLE / LOW_QUALITY / AVOID.
ADR: Average Daily Range, current range, ADR% used. Expansion: CONSOLIDATING / EARLY / ACTIVE / LATE / EXHAUSTED.
Support Judas Swing, PO3, session expansion, session target completion, setup expiration. Time never creates a trade by itself.

# STEP 5 — Entry Confirmation & Execution Engine

Lifecycle:

```text
DISCOVERED
WATCH
SETUP_FORMING
LIQUIDITY_EVENT
WAITING_FOR_MSS
SETUP_ARMED
WAITING_FOR_RETRACEMENT
ENTRY_ZONE_APPROACHING
ENTRY_ZONE_TOUCHED
WAITING_FOR_CONFIRMATION
LONG_READY
SHORT_READY
ENTRY_MISSED
INVALIDATED
EXPIRED
BLOCKED
ACTIVE
CLOSED
```

Sequence: HTF bias -> DOL -> liquidity -> displacement -> MSS -> PD Array -> retracement -> LTF confirmation -> risk -> ready.
Modes: CONSERVATIVE / STANDARD / AGGRESSIVE. Default XAUUSD = 15M context/confirmation + 5M execution.
Support candle-close confirmation, optional 30M confirmation, optional second-candle confirmation, micro MSS, acceptance/rejection, confirmation-entry default, limit-entry research mode.
Entry models: Conservative Retest; 15M Close; LTF Refinement; No Wick Rebalance; Breaker; IFVG.
Warn for early entry before MSS, during sweep, inside displacement, before close, before retracement, weak FVG, against HTF, before major news.
Chase protection: missed original entry + collapsed R:R or TP1 too close -> ENTRY_MISSED / DO_NOT_CHASE.
Stops use structural invalidation + configured buffer. Targets use DOL/liquidity. TP1/TP2/TP3.

# STEP 6 — Risk Management & Position Sizing Engine

Broker-free. Manual account profile:

```typescript
type AccountProfile = {
  balance: number;
  currency: string;
  riskPerTradePct: number;
  dailyRiskLimitPct: number;
  weeklyRiskLimitPct: number;
  maxOpenRiskPct: number;
  maxTradesPerDay: number;
  leverage?: number;
};
```

Instrument spec:

```typescript
type InstrumentSpec = {
  symbol: string;
  contractSize: number;
  tickSize: number;
  tickValue: number;
  minVolume: number;
  volumeStep: number;
  quoteCurrency: string;
  typicalSpread?: number;
};
```

Unknown spec -> POSITION_SIZE_UNVERIFIED. Never guess.
Position sizing inputs: account value, risk %, entry, structural stop, contract/tick details, currency conversion if needed.
For XAUUSD show price distance, point distance, platform pip distance if configured, dollar risk.
Risk profiles: CONSERVATIVE / STANDARD / AGGRESSIVE / CUSTOM.
Locks: daily loss, weekly loss, max open risk, max positions, max trades/day, consecutive loss, volatility, news, optional prop rules.
No martingale, no adding to losers, no revenge-trading logic. Risk has final veto.

# STEP 7 — Macro, News & Intermarket Intelligence Engine

Economic-event model:

```typescript
type EconomicEvent = {
  id: string;
  country: string;
  currency: string;
  name: string;
  scheduledTime: string;
  importance: "LOW"|"MEDIUM"|"HIGH"|"EXTREME";
  actual?: number|string;
  forecast?: number|string;
  previous?: number|string;
  revisedPrevious?: number|string;
  status: "UPCOMING"|"IMMINENT"|"RELEASED"|"REVISED"|"COMPLETED"|"CANCELLED"|"DELAYED";
};
```

News states: CLEAR / CAUTION / BLACKOUT / POST_NEWS_WAIT / NORMALIZED.
For Gold prioritize DXY, US 2Y, US 10Y, real yields when available, Fed policy/rate expectations, inflation, employment, growth, risk sentiment, geopolitics, safe-haven flows. For Silver also industrial-growth sensitivity.
Macro states: STRONGLY_SUPPORTIVE / SUPPORTIVE / NEUTRAL / CONFLICT / STRONG_CONFLICT / UNAVAILABLE.
Support actual-vs-forecast surprise, revisions, reaction divergence, DXY/yield structure, risk-on/off, currency strength, SMT, dynamic correlation regime, geopolitical source confidence.
Macro modifies score/confidence/narrative. It does not replace technical structure.

# STEP 8 — Scoring, AI Decision & Final Verdict Engine

Authority order:

```text
1 System integrity
2 Data quality
3 Risk locks
4 News block
5 HTF context
6 Liquidity / DOL
7 Structure
8 Displacement
9 PD Array
10 Entry confirmation
11 Session
12 Macro
13 No Wick
14 Supporting signals
```

Initial score weights:

```text
HTF 15
Liquidity 15
Structure 15
Displacement 10
PD Array 10
No Wick 5
Session 10
Macro 5
Entry 10
Risk/R:R 5
TOTAL 100
```

Grades: A+ 90–100, A 80–89, B 70–79, C 60–69, D <60.
Score cannot override hard blockers.
Hard blockers: stale/invalid data, system-integrity failure, risk lock, strict news blackout, invalid structure, entry missed, insufficient R:R, missing required instrument spec, unsafe spread/slippage if known.
Add DecisionConfidence LOW/MODERATE/HIGH/VERY_HIGH, ConflictScore, DataQualityScore, correlated-evidence adjustment, counter-trend penalty, Devil's Advocate. Never treat score as win probability.

# STEP 9 — Alerts, Monitoring & Trade-Watch Engine

Monitor meaningful state changes, not every candle.
Categories: INFORMATIONAL / WATCH / SETUP / ENTRY / RISK / MANAGEMENT / CRITICAL.
Priority: LOW / MEDIUM / HIGH / CRITICAL.

Alert types include:

```text
Liquidity approaching/sweep/run
EQH/EQL creation
PDH/PDL/PWH/PWL interaction
Session liquidity events
MSS / BOS / structure failure
Displacement
No Wick event/rebalance
FVG create/touch/invalidate
OB touch
PD Array confluence
Entry zone approach/touch
Entry confirmation
Entry missed
Early-entry warning
Chase warning
Macro/DXY/yield/SMT changes
News countdown
Risk/spread/volatility locks
TP/SL/thesis invalidation
Session end/setup expiration
```

Signature feature: ALERT_ME_WHEN_READY. It monitors all mandatory setup conditions and alerts only once ready.
Use dedupeKey, cooldown, and state memory to avoid spam.

# STEP 10 — Journal, Replay, Backtesting & Analytics Engine

Record every setup/trade with asset, time/day/session, direction, setup type, timeframes, HTF bias, DOL, liquidity, MSS/BOS/CHoCH, displacement, PD Array, No Wick, premium/discount, macro/DXY/yields/news, entry/stop/targets, position size, risk %, R:R, result, R, MFE, MAE, duration, exit reason, violations, strategy version.

Preserve immutable pre-trade snapshot.
Result states: FULL_WIN / PARTIAL_WIN / BREAK_EVEN / FULL_LOSS / PARTIAL_LOSS / MANUAL_EXIT / INVALIDATION_EXIT / NEWS_EXIT / TRAILING_STOP_EXIT / MISSED_ENTRY / NO_TRADE.
Track R, MFE, MAE, entry efficiency, exit efficiency.
Classify VALID_LOSS / PROCESS_ERROR / VALID_WIN / BAD_PROCESS_WIN.

Analytics: win rate, average/total R, expectancy, profit factor, max drawdown/recovery, duration, best asset/session/setup/timeframe, No Wick performance, liquidity performance, DOL accuracy, alert usefulness, rule violations.
Sample-size labels: <30 INSUFFICIENT; 30–99 LIMITED; 100–299 MODERATE; 300+ STRONGER_EVIDENCE.

Backtesting: sequential historical processing only, no lookahead, realistic spread/slippage/commission, correct session/DST timing, candle-close integrity, disclose tick/intrabar ambiguity. Support A/B, out-of-sample, walk-forward, robustness, optional Monte Carlo, paper/forward testing, strategy versioning.
Replay: Manual / Guided / Blind / Quiz, hide future.

# STEP 11 — Dashboard, Charting & User Experience Engine

Desktop layout:

```text
TOP STATUS BAR
LEFT NAV
CENTER CHART
RIGHT INTELLIGENCE PANEL
BOTTOM ALERT/EVENT AREA
```

Top: symbol, price, daily change, spread, session, HTF bias, verdict, score, confidence, news, risk, data quality.
Left nav: Command Center, Markets, Watchlist, Scanner, Chart, Setups, Alerts, Macro, Risk, Journal, Backtest, Replay, Playbook, Settings.
Chart: TradingView Lightweight Charts or equivalent; backend supplies market data.
Overlays: HH/HL/LH/LL, BOS/MSS/CHoCH, protected levels, BSL/SSL, EQH/EQL, PDH/PDL/PWH/PWL, sessions, FVG/IFVG, OB/Breaker/Mitigation/BPR, No Wick, premium/discount, OTE, entry/SL/TP, DOL, news.
Views: SMART_VIEW / DEEP_VIEW / FOCUS_MODE / BEGINNER_MODE / ADVANCED_MODE.
Signature UI: WHY_NOT_YET, bullish-vs-bearish evidence, setup progress, missing-confirmation panel, devil's advocate, score breakdown, decision history, market timeline.
Right-panel tabs: OVERVIEW / STRUCTURE / LIQUIDITY / PD_ARRAYS / NO_WICK / SESSION / MACRO / ENTRY / RISK / AI_ANALYSIS.
Mobile: HOME / WATCHLIST / CHART / ALERTS / TRADES / AI.
No live broker mode.

# STEP 12 — Data, Integrations & Technical Architecture Engine

Recommended stack unless a strong reason exists:

```text
Frontend: Next.js + TypeScript
Chart: TradingView Lightweight Charts
Backend: FastAPI + Python
Database: PostgreSQL
Cache/realtime: Redis
Tests: pytest + frontend framework
Containers: Docker / Docker Compose
CI: GitHub Actions
```

Flow:

```text
Market + Macro Providers
-> Ingestion
-> Normalization
-> Candle Engine
-> Event Bus
-> Structure/Liquidity/Displacement/PD Arrays/No Wick/Session/Macro
-> Setup State
-> Risk
-> Scoring/Verdict
-> Alerts/API/UI/AI/Journal
```

Provider interface:

```python
class MarketDataProvider(Protocol):
    async def get_latest_quote(self, symbol: str): ...
    async def get_historical_bars(self, symbol: str, timeframe: str, start=None, end=None): ...
    async def subscribe_quotes(self, symbols: list[str]): ...
    async def subscribe_bars(self, symbols: list[str], timeframes: list[str]): ...
    async def get_instrument_metadata(self, symbol: str): ...
    async def get_market_status(self, symbol: str): ...
    async def health_check(self): ...
```

Normalized candle:

```python
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    source: str
    is_closed: bool
    data_quality: str
```

Data quality: LIVE / CURRENT / DELAYED / STALE / DISCONNECTED / INVALID.
Handle missing bars, duplicates, out-of-order bars, impossible OHLC, bad ticks, provider disagreement, source changes, gap repair.
API secrets server-side only. Shared backend stream fans normalized data to clients. Store instruments, candles, quotes, sessions, economic events, macro series, analysis events, setups, alerts, trade plans, paper/manual trades, journal, backtests, strategy versions, user settings.
Use same strategy code for LIVE / PAPER / REPLAY / BACKTEST whenever possible.

# STEP 13 — No Wick Architecture Deep Spec

For every completed candle calculate total range, body, upper/lower wick, body %, wick %, Body/ATR, Range/ATR, body/recent median, range/recent median, close-location %.

Initial research thresholds only:

```text
TRUE MARUBOZU: Body >= 90%, each wick <= 5%
NEAR MARUBOZU: Body >= 80%, each wick <= 10%
ONE-SIDED NO WICK: origin-side wick <= 5%
MIN MEANINGFUL BODY: ~0.8 ATR
STRONG: ~1.2 ATR
EXCEPTIONAL: ~1.5 ATR
```

Classifications:

```text
TRUE_BULLISH_MARUBOZU
TRUE_BEARISH_MARUBOZU
NEAR_BULLISH_MARUBOZU
NEAR_BEARISH_MARUBOZU
BULLISH_NO_LOWER_WICK
BEARISH_NO_UPPER_WICK
BULLISH_NO_UPPER_WICK
BEARISH_NO_LOWER_WICK
NO_ORIGIN_SIDE_WICK
NO_DESTINATION_SIDE_WICK
NEWS_DRIVEN_NO_WICK
INSIGNIFICANT_NO_WICK
```

Score context: wick quality, body strength, relative size, displacement, structure consequence, liquidity event, FVG creation/overlap, session, HTF alignment, news.
Keep CandleQualityScore, ContextScore, NoWickRelevanceScore separate.
Rebalance references: open, origin side, 25%, 50%, 75%, full body, FVG overlap, OB overlap.
Zone states: FRESH / APPROACHING / TOUCHED / PARTIAL / HALF_REBALANCED / FULLY_REBALANCED / REACTED / FAILED / INVALIDATED.
Critical no-lookahead rule: no future candle or later FVG may retroactively improve a historical No Wick event.
No Wick never independently authorizes LONG/SHORT.

# STEP 14 — AI Assistant, Natural-Language Command & Education Engine

AI reads structured state and calls deterministic services. It never becomes the price engine.
Expose functions such as getMarketState, getStructure, getLiquidity, getPDArrayState, getNoWickEvents, getSessionState, getMacroState, getTradePlan, getRiskCalculation, scanMarkets, createAlert, queryJournal, runBacktest.

Commands include: Analyze Gold; Why are we waiting?; Why isn't this a buy?; Where is liquidity?; What is DOL?; Why did score drop?; Is this No Wick important?; What invalidates this?; Give trade plan; Show A+ setups; Compare assets; Backtest setup.

Rules:

```text
Use current Master Decision Object
Never invent missing market facts
Return UNKNOWN / UNAVAILABLE / NOT CONFIRMED when necessary
Keep asset contexts isolated
```

Education modes: BEGINNER / INTERMEDIATE / ADVANCED / PROFESSIONAL. Support chart-object explain, Learning Mode, Socratic Mode, Quiz Mode, Replay Tutor, Journal Coaching, mistake analysis.

# STEP 15 — MVP, Build Phases & Priority Roadmap

Codex must build in this order:

```text
Phase 0 Repository/Foundation/Data Architecture
Phase 1 XAUUSD Chart Shell
Phase 2 Market Structure
Phase 3 Liquidity
Phase 4 Displacement + FVG/IFVG
Phase 5 No Wick Architecture V1
Phase 6 Session & Time
Phase 7 Core Setup State Machine
Phase 8 Entry & Verdict
Phase 9 Risk Calculator
Phase 10 Watchlist & Scanner
Phase 11 Alerts V1
Phase 12 AI Assistant V1
Phase 13 Economic Calendar
Phase 14 Basic Macro
Phase 15 Journal V1
Phase 16 Paper Trading
Phase 17 Analytics V1
Phase 18 Backtesting
Phase 19 Replay
Phase 20+ Advanced PD Arrays / SMT / advanced macro / adaptive No Wick / advanced scoring / playbooks / mobile
```

V1 must-have: auth, independent XAUUSD data, historical/live chart, 5M/15M/1H/4H/D, structure, liquidity, PDH/PDL, Asia/London levels, MSS/BOS, FVG, No Wick, sessions, DOL, scoring, LONG/SHORT/WAIT/NO TRADE, entry/SL/TP/R:R, manual risk, alerts, AI explanations, watchlist, basic journal.
Do not overbuild and do not skip phase gates.

# STEP 16 — Testing, Validation, QA & Failure Safety

Testing layers: data validation, unit, strategy, integration, historical chart validation, replay, backtest, alerts, UI, outage/failure, performance, regression, security.

Test data: missing timestamps, duplicates, invalid OHLC, negative/zero range, stale, out-of-order, bad ticks, feed gaps, DST/session boundaries, aggregation, closed/live candles.
Structure: swings, HH/HL/LH/LL, BOS, wick-only rejection, MSS, false MSS, protected structure, range.
Liquidity: EQH/EQL, sweep, run, false sweep, PDH/PDL, DOL, two-sided unclear.
FVG: bull/bear, tiny rejection, partial/50/full fill, invalidation, IFVG.
No Wick: true/near, one-sided, tiny rejection, inside bar, news, bad data, rebalance, invalidation, no-lookahead.
Entry/Risk: WAIT states, close confirmation, second candle, retracement, chase, R:R, stop/target, sizing, locks, unknown specs, correlation.

Critical principle: NEVER FAIL OPEN. Critical uncertainty -> WAIT/BLOCKED/UNAVAILABLE.

# STEP 17 — Security, Privacy, User Data & Product Governance

Never request/store broker passwords, broker API keys, broker sessions, withdrawal credentials, bank credentials, trading-platform passwords.
Use HTTPS/WSS, secret manager, MFA/passkeys where appropriate, least privilege, user isolation, audit logs, server-side provider keys, env separation, dependency scanning, input validation, rate limits.
Private by default: journal, P/L, risk, balance, screenshots, playbooks, trade history.
Support export, deletion, retention, backups, session/security management.
Product is analysis/decision support, not broker/custodian/money manager/auto bot/copy trader.
No guaranteed-profit/win-rate/risk-free claims. Commercial launch requires legal/data-licensing/regulatory review.

# STEP 18 — Master Workflow & System Orchestration

Suggested MasterDecision:

```typescript
type MasterDecision = {
  symbol: string;
  verdict: "LONG"|"SHORT"|"WAIT"|"NO_TRADE"|"UNAVAILABLE";
  direction?: "LONG"|"SHORT";
  setupType?: string;
  setupState: string;
  setupGrade?: string;
  setupScore?: number;
  decisionConfidence: "LOW"|"MODERATE"|"HIGH"|"VERY_HIGH";
  htfBias: string;
  primaryDOL?: string;
  secondaryDOL?: string;
  liquidityEvent?: string;
  structureEvent?: string;
  displacement?: string;
  pdArray?: object;
  noWickState?: object;
  sessionState?: object;
  macroState?: object;
  entryZone?: { low: number; high: number };
  preferredEntry?: number;
  stop?: number;
  tp1?: number;
  tp2?: number;
  tp3?: number;
  rr?: number;
  riskStatus: string;
  blockers: string[];
  nextRequiredEvent?: string;
  invalidation?: string;
  dataQuality: string;
  strategyVersion: string;
  updatedAt: string;
};
```

Update cycle:

```text
1 Ingest
2 Validate
3 Normalize time
4 Update candle
5 Candle features
6 HTF context
7 Structure
8 Liquidity
9 DOL
10 Displacement
11 PD Arrays
12 No Wick
13 Sessions
14 News
15 Macro
16 Setup match
17 Setup state
18 Entry
19 Risk
20 Score
21 Confidence
22 Devil's Advocate
23 Blockers
24 Verdict
25 Next required event
26 Persist
27 Alert if changed
28 Update UI
29 Expose same state to AI
```

All surfaces use same Master Decision Object. Dashboard LONG + AI WAIT + Alert SHORT is a system defect.

# STEP 19 — Final Codex Build Instructions

```text
START FROM EMPTY REPOSITORY.
DO NOT COPY PREVIOUS GENERATED CODE.
DO NOT SKIP PHASES.
DO NOT IMPLEMENT LATER-PHASE LOGIC EARLY JUST TO MAKE UI LOOK COMPLETE.
KEEP VERDICT FAIL-SAFE UNTIL REQUIRED GATES EXIST.
```

After each phase:

```text
1 Implement
2 Add tests
3 Run tests
4 Run type/static checks
5 Backend smoke tests
6 Frontend build
7 Update BUILD_STATUS.md
8 Update phase docs
9 Stop if critical tests fail
```

Required repository shape:

```text
/
  apps/web/
  services/api/
  packages/strategy-spec/
  packages/shared-types/
  docs/
  infra/docker/
  scripts/
  .github/workflows/
  README.md
  BUILD_STATUS.md
  docker-compose.yml
  .env.example
```

Deterministic service boundaries:

```text
data_quality
candles
timeframes
structure
liquidity
displacement
fvg
pd_arrays
no_wick
sessions
macro
setup_state
entry
risk
scoring
alerts
market_state
backtest
replay
journal
```

Every phase doc must define: GOAL, INPUTS, RULES, OUTPUTS, STATES, INVALIDATION, ERROR STATES, UNIT TESTS, INTEGRATION TESTS, UI DISPLAY, KNOWN LIMITATIONS, STRATEGY VERSION.

Final philosophy:

```text
NARRATIVE -> LOCATION -> LIQUIDITY -> STRUCTURE -> DISPLACEMENT -> PD ARRAY -> NO WICK -> TIME -> MACRO -> ENTRY -> RISK -> DECISION
Incomplete -> WAIT
Invalid -> NO TRADE
Unusable data -> UNAVAILABLE
All required conditions align -> LONG or SHORT
```

Goal: high-quality decisions, clear invalidation, controlled risk, explainable logic, repeatable process. Not maximum signal frequency.

# CODEX FIRST MESSAGE

Paste this into Codex after adding this file to the new repository:

```text
Build this project from scratch using CODEX_MASTER_BUILD_SPEC.md as the source of truth.

Do not reuse or assume any previous codebase exists.
Start with Phase 0 only.

Before coding:
1. Propose the repository structure.
2. Identify dependencies and why each is needed.
3. State exact Phase 0 acceptance criteria.
4. Implement Phase 0 completely.
5. Add tests and run them.
6. Create BUILD_STATUS.md.
7. Stop after Phase 0 and summarize what passed, what failed, and what remains.

Do not begin Phase 1 until Phase 0 passes.
Do not add broker integration or order execution.
Do not use TradingView as market-data source.
Keep provider credentials server-side.
Use deterministic logic for every trading calculation.
```

# END OF MASTER SPEC
