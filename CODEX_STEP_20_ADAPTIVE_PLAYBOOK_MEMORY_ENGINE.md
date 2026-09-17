# CODEX ADDENDUM — STEP 20
## Adaptive Winning Playbook, Anti-Pattern & Trade Memory Engine
### Forex & Metals ICT/SMC Trading Command Center

This addendum extends `CODEX_MASTER_BUILD_SPEC.md`.

It must not weaken any existing safety rule.

The system remains broker-free. "Take the trade" means:
- promote the setup to `LONG_READY` / `SHORT_READY`,
- optionally create a paper trade,
- generate a trade plan,
- alert the user.

It does NOT mean submitting an order to a broker.

---

# 20.1 OBJECTIVE

Build a deterministic learning and memory subsystem that:

1. Reviews completed trades and setups.
2. Determines which pre-trade conditions were present.
3. Groups genuinely similar historical setups.
4. Identifies repeatable winning configurations.
5. Saves statistically supported winners as `Winning Playbooks`.
6. Identifies repeatable failure/process-error configurations.
7. Saves statistically supported failures as `Anti-Patterns`.
8. Checks every new candidate setup against both memories before final approval.
9. Increases confidence for validated playbook matches.
10. Warns or blocks when a setup matches a validated anti-pattern.
11. Never allows memory to override hard safety/risk/data/news/invalidation gates.
12. Never learns from future information when making a historical decision.

---

# 20.2 CORE SAFETY PRINCIPLE

Do NOT create a permanent rule from one winning or losing trade.

A winning trade can be luck.
A losing trade can be a valid loss.

The system must distinguish:

- `VALID_WIN`
- `BAD_PROCESS_WIN`
- `VALID_LOSS`
- `PROCESS_ERROR`

Only repeated, statistically supported patterns may become validated memories.

A single outcome may create only:

- `CANDIDATE_PLAYBOOK`
- `CANDIDATE_ANTI_PATTERN`

Never a validated permanent rule.

---

# 20.3 IMMUTABLE PRE-TRADE FEATURE SNAPSHOT

Before every setup becomes actionable, store an immutable feature snapshot.

Example model:

```python
class SetupFingerprint:
    symbol: str
    timestamp: datetime

    # regime / HTF
    daily_bias: str
    h4_bias: str
    h1_bias: str
    dealing_range_id: str | None
    premium_discount_state: str
    volatility_regime: str
    atr_percentile: float | None
    adr_percent_used: float | None

    # structure
    structure_state: str
    structure_sequence: list[str]
    mss_confirmed: bool
    bos_confirmed: bool
    displacement_grade: str
    displacement_score: float | None

    # liquidity
    liquidity_event: str | None
    liquidity_side: str | None
    liquidity_level_type: str | None
    primary_dol: str | None
    dol_distance: float | None
    eqh_present: bool
    eql_present: bool

    # PD arrays
    pd_array_type: str | None
    pd_array_quality: float | None
    fvg_state: str | None
    fvg_quality: float | None
    ifvg_present: bool
    order_block_present: bool
    ote_state: str | None

    # no wick
    no_wick_type: str | None
    no_wick_quality: float | None
    no_wick_context_score: float | None
    no_wick_rebalance_state: str | None

    # timing
    session: str
    kill_zone: str | None
    day_of_week: int
    minute_of_session: int | None

    # macro/news
    news_state: str
    minutes_to_high_impact_news: int | None
    macro_state: str
    dxy_state: str | None
    yield_state: str | None

    # entry
    entry_model: str
    confirmation_timeframe: str
    execution_timeframe: str
    entry_location_score: float
    setup_score: float
    decision_confidence: str

    # risk
    rr_at_entry: float
    stop_distance: float
    target_type: str
    risk_status: str

    # provenance
    strategy_version: str
    config_version: str
    data_source: str
```

Store only information known at that timestamp.

Never alter this snapshot after the outcome is known.

---

# 20.4 POST-TRADE OUTCOME RECORD

After closure, attach outcome data separately.

```python
class TradeOutcome:
    setup_id: str
    result_class: str
    realized_r: float | None
    mfe_r: float | None
    mae_r: float | None
    duration_seconds: int | None
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    stop_hit: bool
    thesis_invalidated: bool
    process_classification: str
    exit_reason: str | None
```

Never write outcome fields back into the original fingerprint.

---

# 20.5 WHY-DID-IT-WORK ANALYSIS

For winning trades, the system must not simply ask:

"Which features were present?"

It must compare the trade against similar trades.

Evaluate:

- HTF alignment
- liquidity sequence
- DOL
- MSS/displacement quality
- FVG/PD Array quality
- No Wick state
- premium/discount
- session
- news state
- macro state
- ADR state
- confirmation timeframe
- entry timing
- R:R
- stop placement

Use cohort comparisons such as:

```text
SETUPS WITH FEATURE X
versus
SIMILAR SETUPS WITHOUT FEATURE X
```

Prefer statistical evidence over AI narrative.

The AI may explain the result, but it may not invent causality.

Allowed language:

- "This feature is strongly associated with better expectancy in the current sample."
- "This appears helpful, but the sample is limited."

Avoid:

- "This trade won because of X"

unless there is sufficient evidence.

---

# 20.6 WINNING PLAYBOOK OBJECT

```python
class WinningPlaybook:
    id: str
    name: str
    status: str  # CANDIDATE / TESTING / VALIDATED / PAUSED / RETIRED

    symbol_scope: list[str]
    setup_type: str

    required_features: dict
    optional_features: dict
    disqualifiers: dict

    sample_size: int
    wins: int
    losses: int
    win_rate: float
    expectancy_r: float
    profit_factor: float | None
    avg_r: float
    median_r: float
    max_drawdown_r: float | None

    avg_mfe_r: float | None
    avg_mae_r: float | None

    in_sample_expectancy_r: float | None
    out_of_sample_expectancy_r: float | None
    walk_forward_passed: bool

    regime_stability_score: float
    confidence_score: float

    strategy_version: str
    created_at: datetime
    last_validated_at: datetime | None
```

Example human-readable playbook:

```text
PLAYBOOK:
XAUUSD London Sell-Side Sweep Reversal

REQUIRED:
Daily/4H bullish
Price in HTF discount
London/NY liquidity event
Sell-side sweep
15M bullish displacement
15M bullish MSS
Bullish FVG
5M bullish confirmation
No high-impact blackout
R:R >= configured threshold

SUPPORTING:
Bullish No-Lower-Wick displacement
DXY bearish
US yields weakening

DISQUALIFIERS:
Entry already missed
Daily ADR exhausted
High-impact blackout
Opposing HTF invalidation
Risk lock
```

---

# 20.7 PLAYBOOK PROMOTION RULES

Do not validate a Winning Playbook from a handful of trades.

Suggested evidence states:

```text
1 trade:
OBSERVATION_ONLY

2-9 similar trades:
CANDIDATE

10-29:
TESTING / VERY_LOW_CONFIDENCE

30-99:
LIMITED_EVIDENCE

100-299:
MODERATE_EVIDENCE

300+:
STRONGER_EVIDENCE
```

A playbook may become `VALIDATED` only if all configured requirements pass, including:

- minimum sample size
- positive expectancy
- acceptable drawdown
- minimum profit factor if used
- out-of-sample performance
- walk-forward stability
- no single period dominating results
- acceptable performance across multiple market regimes
- no evidence of severe overfitting

Exact thresholds must be configurable.

---

# 20.8 NEVER-DO-THIS-AGAIN / ANTI-PATTERN OBJECT

```python
class AntiPattern:
    id: str
    name: str
    status: str  # CANDIDATE / TESTING / VALIDATED / PAUSED / RETIRED
    severity: str  # INFO / WARNING / BLOCK

    symbol_scope: list[str]
    feature_signature: dict
    context_requirements: dict

    sample_size: int
    loss_rate: float
    expectancy_r: float
    avg_mae_r: float | None
    process_error_rate: float

    out_of_sample_expectancy_r: float | None
    walk_forward_passed: bool
    confidence_score: float

    recommended_action: str
    strategy_version: str
```

Example:

```text
ANTI-PATTERN:
XAUUSD Early Long Before 15M Confirmation

MATCH:
Bullish narrative
Liquidity sweep occurred
15M MSS not yet closed
Entry taken on 1M/5M anticipation
Price above intended retracement zone

HISTORICAL EFFECT:
Negative expectancy
High MAE
High process-error rate

ACTION:
BLOCK ENTRY
WAIT FOR 15M CLOSE + RETRACEMENT
```

---

# 20.9 CRITICAL ANTI-PATTERN RULE

A normal losing trade must NOT automatically create an anti-pattern.

Example:

A perfect A+ setup can lose.

That is a `VALID_LOSS`.

Creating a "Never Do This Again" rule from it would teach the system the wrong lesson.

Anti-pattern creation should prioritize:

- repeated negative-expectancy conditions
- rule violations
- early entries
- chasing
- poor R:R
- entering during blocked news
- wrong premium/discount location
- counter-HTF trades
- invalidated structure
- stale data
- excessive ADR usage
- repeated setup features with statistically poor outcomes

---

# 20.10 SETUP SIMILARITY ENGINE

Before every candidate setup becomes LONG_READY / SHORT_READY:

1. Build the current `SetupFingerprint`.
2. Search Winning Playbooks.
3. Search Anti-Patterns.
4. Search historical setup fingerprints.
5. Compute similarity.

Similarity must use trading-relevant features, not raw text.

Possible components:

```text
HTF regime similarity
Structure sequence similarity
Liquidity-event similarity
DOL similarity
PD Array similarity
No Wick similarity
Premium/discount similarity
Session similarity
Volatility-regime similarity
News/macro similarity
Entry-model similarity
R:R bucket
```

Example:

```python
memory_result = memory_engine.evaluate(current_fingerprint)

memory_result = {
    "winning_playbook_matches": [...],
    "anti_pattern_matches": [...],
    "nearest_historical_setups": [...],
    "playbook_confidence_adjustment": 0.0,
    "anti_pattern_penalty": 0.0,
    "memory_gate": "PASS" | "WARNING" | "BLOCK",
}
```

---

# 20.11 PRE-TRADE MEMORY GATE

Place the memory gate AFTER current-market deterministic analysis but BEFORE final verdict approval.

Correct orchestration:

```text
CURRENT MARKET DATA
→ deterministic ICT/SMC analysis
→ candidate setup
→ risk checks
→ build current fingerprint
→ memory retrieval
→ Winning Playbook match
→ Anti-Pattern match
→ memory adjustment/gate
→ final score/confidence
→ hard blockers
→ LONG_READY / SHORT_READY / WAIT / NO_TRADE
```

Memory must never create a setup that does not exist in current deterministic market logic.

Bad:

```text
"Past winner looked similar, therefore BUY."
```

Correct:

```text
"Current setup already satisfies deterministic entry requirements and also matches
a validated historical Winning Playbook."
```

---

# 20.12 MEMORY AUTHORITY RULES

Winning Playbook match:

```text
May increase confidence.
May increase playbook score.
May rank one valid setup above another.
May NOT bypass:
- stale data
- risk lock
- news blackout
- invalid structure
- missing confirmation
- bad R:R
- entry missed
```

Validated Anti-Pattern match:

```text
INFO:
Explain only.

WARNING:
Reduce confidence / require stronger confirmation.

BLOCK:
Prevent LONG_READY / SHORT_READY and return WAIT or NO_TRADE with reason.
```

Hard blocking should require high-quality evidence and explicit configuration.

---

# 20.13 MEMORY EXPLANATION

Show:

```text
MEMORY MATCH

Winning Playbook:
London SSL Sweep Reversal
Similarity: 88%
Historical sample: 147
Expectancy: +0.74R
Out-of-sample expectancy: +0.51R

Anti-Pattern:
None above blocking threshold

Memory Result:
SUPPORTIVE
```

Or:

```text
ANTI-PATTERN DETECTED

Pattern:
Early long before confirmed 15M MSS

Similarity: 93%
Historical sample: 68
Expectancy: -0.62R

Action:
BLOCK
Next Required Event:
Wait for confirmed 15M bullish MSS and retracement.
```

Do not display misleading certainty.

---

# 20.14 MEMORY DATABASE

Suggested logical tables:

```text
setup_fingerprints
trade_outcomes
playbook_candidates
winning_playbooks
anti_pattern_candidates
anti_patterns
memory_matches
feature_statistics
regime_statistics
playbook_versions
anti_pattern_versions
learning_runs
```

Keep:

- strategy version
- data version
- feature version
- model/version used for clustering or similarity
- exact training window
- validation window

---

# 20.15 TIME-SERIES LEARNING SAFETY

Historical learning must use chronological splits.

Do NOT randomly shuffle time-series observations for final validation.

Recommended:

```text
TRAIN
→ VALIDATION
→ OUT-OF-SAMPLE TEST
→ WALK-FORWARD WINDOWS
```

Examples:

```text
2016-2022 train
2023 validation
2024 test

then roll forward
```

Exact dates should be selected based on available timeframe data.

Never use future outcomes to tune rules that are evaluated on the past.

---

# 20.16 REGIME AWARENESS

A playbook may work in:

```text
TRENDING
RANGING
HIGH_VOLATILITY
LOW_VOLATILITY
NEWS_DOMINATED
RISK_OFF
USD_STRENGTH
USD_WEAKNESS
```

but fail in another regime.

Store regime-specific performance.

Do not call a setup "proven" if success exists only in one narrow regime unless the playbook is explicitly restricted to that regime.

---

# 20.17 DRIFT / DECAY

Market behavior changes.

Winning Playbooks and Anti-Patterns must be re-evaluated.

Track:

```text
rolling expectancy
rolling win rate
rolling MAE/MFE
recent sample
regime changes
data-provider changes
strategy-version changes
```

Possible playbook state transitions:

```text
VALIDATED
→ DEGRADING
→ PAUSED
→ RETIRED
```

Do not permanently trust a rule because it once worked.

---

# 20.18 HUMAN REVIEW

Support manual actions:

```text
Approve playbook
Reject candidate
Pause playbook
Retire playbook
Approve anti-pattern
Change anti-pattern severity
Add notes
```

Do not silently convert a weak statistical observation into a permanent hard rule.

---

# 20.19 AI ROLE IN LEARNING

AI may:

- summarize clusters
- explain why a pattern may be important
- name playbooks
- describe anti-patterns
- produce review reports
- compare winners vs losers

AI may NOT:

- fabricate statistics
- decide a playbook is validated without deterministic validation
- change strategy rules silently
- use post-trade narrative as pre-trade data
- override risk or safety gates

---

# 20.20 XAUUSD HISTORICAL DATA INGESTION

The user's currently supplied files contain approximately:

```text
DAILY:
2,624 rows
2016-08-15 through 2026-09-14

WEEKLY:
522 rows
2016-09-18 through 2026-09-13

MONTHLY:
120 rows
2016-10-01 through 2026-09-01
```

Columns:

```text
Date
Price
Open
High
Low
Vol.
Change %
```

Interpret `Price` as close only after validating source semantics.

Data is currently descending by date and must be normalized chronologically before backtesting.

The uploaded data contains:
- no duplicate dates found in the inspected files
- almost no usable volume data
- a small number of OHLC inconsistencies that must be quarantined/repaired rather than silently learned from

Build a historical-ingestion validator that:

1. Parses dates.
2. Parses comma-formatted numeric prices.
3. Renames `Price` to `Close` after validation.
4. Sorts ascending.
5. Detects duplicate timestamps.
6. Validates `Low <= Open/Close <= High`.
7. Flags holiday/synthetic/anomalous rows.
8. Stores data-quality flags.
9. Never silently modifies source data.
10. Creates a cleaned research dataset plus an anomaly report.

---

# 20.21 IMPORTANT DATA LIMITATION

Daily / Weekly / Monthly data is enough to research:

- long-term XAUUSD regimes
- HTF structure
- weekly/monthly liquidity
- premium/discount
- broad DOL behavior
- Daily FVG / No Wick behavior
- long-term volatility regimes

It is NOT enough to validate:

- 15M confirmation
- 5M execution
- 1H intraday structure
- session sweeps
- London/NY kill-zone behavior
- intraday FVGs
- 5M/15M No Wick rebalance entries
- precise intraday stops/targets
- realistic spread/slippage around entries

For the full execution-learning engine, obtain at minimum:

```text
1H
15M
5M
```

Preferably also:

```text
1M
```

if advanced execution research will be performed.

All timeframes should be timestamped consistently and preferably originate from the same market-data source used for live analysis.

---

# 20.22 OFFLINE LEARNING PIPELINE

Build a research command such as:

```bash
python -m research.build_memory \
  --symbol XAUUSD \
  --start 2016-01-01 \
  --end 2026-12-31 \
  --strategy-version ICT_METALS_V1 \
  --walk-forward
```

Pipeline:

```text
Historical Data
→ Validation
→ Chronological Replay
→ Deterministic Strategy
→ Setup Fingerprints
→ Simulated Outcomes
→ Feature Statistics
→ Similarity Clusters
→ Candidate Playbooks
→ Candidate Anti-Patterns
→ Out-of-Sample Validation
→ Walk-Forward Validation
→ Memory Database
```

---

# 20.23 REQUIRED TESTS

Unit tests:

```text
Fingerprint contains only information available at timestamp
Outcome cannot mutate fingerprint
Single win cannot become validated playbook
Single loss cannot become validated anti-pattern
Valid loss does not become process-error anti-pattern
Playbook cannot bypass hard blocker
Anti-pattern blocking threshold works
Similarity is deterministic
Versioning is preserved
```

Backtest leakage tests:

```text
No future candles
No future FVG
No future MSS
No future liquidity state
No future macro release
No future outcome
No future playbook generated from trades that had not yet occurred
```

Critical temporal test:

At historical timestamp T, the memory engine may only use trades whose outcomes were fully known BEFORE T.

---

# 20.24 FINAL PRE-TRADE DECISION EXAMPLE

```text
XAUUSD
Current deterministic setup: VALID

HTF:
Bullish

Liquidity:
London SSL swept

Structure:
15M bullish MSS confirmed

PD Array:
15M bullish FVG

No Wick:
Bullish No-Lower-Wick, strong

Execution:
5M bullish confirmation

R:R:
3.2

Risk:
Approved

MEMORY CHECK:
Winning Playbook match: 91%
Playbook sample: 126
OOS expectancy: +0.58R

Anti-Pattern match:
None

Memory Gate:
PASS

FINAL:
LONG_READY
Confidence:
HIGH
```

And:

```text
XAUUSD
Current setup otherwise valid

MEMORY CHECK:
Validated Anti-Pattern match: 94%

Pattern:
Late entry after primary DOL nearly reached

Historical sample:
83

Expectancy:
-0.71R

Memory Gate:
BLOCK

FINAL:
NO_TRADE

Reason:
Validated historical anti-pattern.

Next action:
Wait for a new structural setup.
```

---

# 20.25 FINAL PRINCIPLE

The system must learn:

```text
WHAT WORKS
WHAT FAILS
WHEN IT WORKS
WHEN IT FAILS
UNDER WHICH MARKET REGIME
WITH WHAT EXPECTANCY
```

It must NOT learn:

```text
"Last trade won, repeat it."
"Last trade lost, ban it."
```

Winning Playbooks and Anti-Patterns are statistically validated memories, not emotional reactions to individual trades.

The learning engine may improve confidence and protect the trader from repeated mistakes, but current deterministic market structure, data quality, and risk controls always retain authority.
