# Forex & Metals ICT/SMC Trading Command Center

Broker-free decision support for Forex & Metals, with XAUUSD as the first deeply validated market.
**No broker connection. No order execution.** Market data comes from an independent provider behind an
abstraction. TradingView Lightweight Charts will be used only as a renderer. All strategy logic is
deterministic. AI (later phase) only explains structured outputs.

> Analysis/decision support only, not financial advice. No profit, win-rate or risk-free claims.

Source of truth: [`CODEX_MASTER_BUILD_SPEC.md`](CODEX_MASTER_BUILD_SPEC.md) · Progress: [`BUILD_STATUS.md`](BUILD_STATUS.md)

## Layout

```
apps/web/                 Next.js + TypeScript command-center shell + Lightweight Charts (render only)
services/api/             FastAPI + Python 3.12 (providers, data quality, candles/chart series, decision gate, DB)
packages/strategy-spec/   Versioned JSON: enums, thresholds, market hours, instruments
packages/shared-types/    TS contract types (tested against strategy-spec + API field contract)
docs/                     architecture.md, phases/PHASE_N.md
infra/docker/             API and web Dockerfiles
scripts/                  check-all.{ps1,sh}, check-guardrails.sh
.github/workflows/ci.yml  lint, types, tests, build, guardrails
```

## Prerequisites
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically)
- Node.js ≥ 22.12 (tested on 24)
- Optional: Docker (Postgres/Redis via `docker-compose.yml`)

## Run locally

```bash
cp .env.example .env
```

```bash
cd services/api && uv sync && MARKET_DATA_PROVIDER=fixture uv run uvicorn app.main:app --port 8000
```

```bash
npm install && npm run dev --workspace @fmcc/web
```

Open http://localhost:3000. With the `fixture` provider the chart shows labelled SYNTHETIC XAUUSD candles (M5/M15/H1/H4/D1; STALE against a real clock) and the verdict is always **UNAVAILABLE** (`DATA_SYNTHETIC`). That is intended.

Chart data API: `GET /api/v1/candles/XAUUSD?timeframe=H4&limit=300` (H4/D1 use New York 17:00 buckets derived from H1).

Structure API: `GET /api/v1/structure/XAUUSD?timeframe=M15` and `GET /api/v1/structure/XAUUSD/alignment` (BOS/CHoCH/MSS, protected levels, states; see `docs/phases/PHASE_2.md`).

Liquidity API: `GET /api/v1/liquidity/XAUUSD?timeframe=H1` (BSL/SSL pools, EQH/EQL, PDH/PDL/PWH/PWL, sweep/run states, magnet score, DOL; see `docs/phases/PHASE_3.md`).

PD arrays API: `GET /api/v1/pd-arrays/XAUUSD?timeframe=M15` (displacement grades, FVG fill/invalidation, IFVG lifecycle; see `docs/phases/PHASE_4.md`).

No Wick API: `GET /api/v1/no-wick/XAUUSD?timeframe=M15` (candle features, no-wick classification and strength, separate quality/context/relevance scores, rebalance zones; context only, never a trade signal; see `docs/phases/PHASE_5.md`).

Sessions API: `GET /api/v1/sessions/XAUUSD` (New York-time session clock and kill zones, session highs/lows, daily/midnight/weekly opens, Asian range state, ADR and expansion, Judas swings; context only; see `docs/phases/PHASE_6.md`).

Setups API: `GET /api/v1/setups/XAUUSD` (core setup state machine on M15: HTF bias, DOL, liquidity sweep, displacement MSS, leg FVG retracement, invalidation/expiry, PO3; progress only, never LONG/SHORT_READY; see `docs/phases/PHASE_7.md`).

Evaluation API: `GET /api/v1/evaluation/XAUUSD` (entry confirmation plan, spec-weighted score/grade, capped confidence, conflicts, warnings, devil's advocate; always NOT_AUTHORIZED while verdict authority is FAIL_SAFE_ONLY; see `docs/phases/PHASE_8.md`).

Risk API: `GET /api/v1/risk/XAUUSD` (limits, loss-aware budget, locks, volatility and position size for a confirmed plan) and the stateless what-if `POST /api/v1/risk/calculate`. To enable it, copy `config/risk_profile.example.json` to `config/risk_profile.local.json` (git-ignored), enter your balance, today's account state and your broker's contract spec, and set `RISK_PROFILE_PATH=../../config/risk_profile.local.json` for the API. Update the state every trading day: a state from another day locks risk. See `docs/phases/PHASE_9.md`.

Markets & scanner API: `GET /api/v1/markets` (catalog, validation, market hours) and `GET /api/v1/scanner?symbols=XAUUSD,EURUSD&minScore=40&onlySetups=true` (each market's own Master Decision, isolated and ranked for attention; never a trade signal). Only XAUUSD is deeply validated; other markets are research only and carry `MARKET_NOT_VALIDATED`. A cold scan of all 9 markets takes ~16 s; rows are cached for 60 s. See `docs/phases/PHASE_10.md`.

Alerts API: `GET /api/v1/alerts` (in-app feed of engine state changes, deduplicated with cooldowns; never trade instructions) and `GET/POST/DELETE /api/v1/alerts/ready-watches` (ALERT_ME_WHEN_READY: a live checklist that fires once, only on a LONG/SHORT decision under FULL verdict authority — impossible while authority is FAIL_SAFE_ONLY). The monitor runs inside the API (`ALERT_MONITOR_ENABLED`, default on) for XAUUSD plus watched markets; alerts and watches are in memory. See `docs/phases/PHASE_11.md`.

AI assistant: `POST /api/v1/assistant/ask` (`{symbol, question, level}`) and `GET /api/v1/assistant/capabilities`. It explains the deterministic engine state through read-only tools and never creates prices, verdicts or trade instructions. Default `AI_PROVIDER=deterministic` works offline; `AI_PROVIDER=anthropic` with a server-side `ANTHROPIC_API_KEY` uses Claude, whose answers are published only if they pass the grounding guard (otherwise the deterministic answer is shown). Account amounts are not sent unless `AI_SHARE_ACCOUNT_DATA=true`. See `docs/phases/PHASE_12.md`.

News gate: `GET /api/v1/news/XAUUSD` and `GET /api/v1/calendar`. By default (`CALENDAR_PROVIDER=unconfigured`) news cannot be proven clear and every decision is blocked by `NEWS_DATA_UNAVAILABLE`. To enable it, maintain a calendar file from a licensed source (template `config/calendar.example.json`; fetched < 24 h ago, covering the next 24 h) and set `CALENDAR_PROVIDER=file` and `CALENDAR_FILE_PATH`. `CALENDAR_PROVIDER=fixture` is a synthetic schedule for development that can never clear the gate. A strict BLACKOUT is a hard blocker. See `docs/phases/PHASE_13.md`.

Macro: `GET /api/v1/macro/XAUUSD?direction=BULLISH` and `GET /api/v1/macro/series`. Macro is context: DXY, US 2Y/10Y, real yields and VIX trends give a bias and a state versus the open setup (STRONGLY_SUPPORTIVE … STRONG_CONFLICT) that adjusts score and confidence; it never blocks or changes the verdict. By default (`MACRO_PROVIDER=unconfigured`) the MACRO factor is NOT_EVALUATED. To enable it, maintain daily series from a licensed source (template `config/macro.example.json`; DXY required, at most 4 days old) and set `MACRO_PROVIDER=file` and `MACRO_FILE_PATH`. `MACRO_PROVIDER=fixture` gives synthetic series that are shown but never scored. See `docs/phases/PHASE_14.md`.

Journal: the left-nav Journal view, or `GET/POST /api/v1/journal/entries`, `POST /api/v1/journal/entries/{id}/outcomes`, `DELETE /api/v1/journal/entries/{id}`, `GET /api/v1/journal/export`. Record decisions (NO_TRADE, MISSED_ENTRY) and your own manual trades; each entry keeps an immutable, hash-verified engine snapshot, detected rule violations, and append-only outcome revisions with R, MFE/MAE, result state and process class. Nothing is placed or authorized. By default (`JOURNAL_STORE=unconfigured`) nothing is saved. To enable it set `JOURNAL_STORE=database` and `DATABASE_URL` (the docker-compose Postgres, or `sqlite+pysqlite:///path/journal.db`), then run `uv run alembic upgrade head` in `services/api`. There is no authentication yet: keep the API local. See `docs/phases/PHASE_15.md`.

Paper trading: the Journal view's Paper sims tab (`#paper`), or `GET/POST /api/v1/paper/sims`, `POST /api/v1/paper/sims/{id}/close|cancel`, `DELETE /api/v1/paper/sims/{id}`. Broker-free simulations replayed bar by bar on the independent market data with assumed spread/slippage/commission (`packages/strategy-spec/paper.json`; replace with your venue's costs): MANUAL levels or ENGINE_PLAN (forward-test the confirmed plan). Nothing is sent anywhere; every sim is `SIMULATION_ONLY`. By default (`PAPER_STORE=unconfigured`) nothing is simulated. To enable it set `PAPER_STORE=database` and `DATABASE_URL`, then run `uv run alembic upgrade head`. With the synthetic fixture feed in real time the data is stale, so sims wait for new bars. See `docs/phases/PHASE_16.md`.

Analytics: the Journal view's Analytics tab (`#analytics`) or `GET /api/v1/analytics?source=JOURNAL|PAPER&symbol&from&to&includeSynthetic`. Descriptive statistics of verified closed journal trades or paper sims (never mixed): win rate, R, expectancy, profit factor, drawdown/recovery, durations, breakdowns by asset/session/setup/timeframe/day/No Wick/liquidity, rule violations and DOL accuracy, each with the spec sample-size label (<30 INSUFFICIENT … 300+ STRONGER_EVIDENCE). Recorded history only: not a probability, forecast, guarantee or trade signal. Requires the journal and/or paper store. See `docs/phases/PHASE_17.md`.

Backtesting: the left-nav Backtest view (`#backtest`) or `POST /api/v1/backtests` (then `GET /api/v1/backtests/{id}`). A background research replay: at every closed M15 candle in the range (≤ 14 days) the live setup engine runs as of that close; newly confirmed plans are simulated with the paper engine and assumed costs; A/B entry modes and cost multipliers, segments, an in/out-of-sample split and a seeded Monte Carlo are reported with sample-size labels and disclosures (risk/news/authority gates are not applied; not a forecast). Synthetic data is never decision-eligible, so backtests on the fixture feed report a funnel with no trades. Enable with `BACKTEST_STORE=database` + `DATABASE_URL` and `uv run alembic upgrade head`. See `docs/phases/PHASE_18.md`.

Replay: the left-nav Replay view (`#replay`) or `POST /api/v1/replay/sessions` (then `/step`, `/answer`, `/end`). Practise on closed history from a chosen start: every response is computed as of the replay cursor, so bars after it are never sent. Modes: MANUAL (step both ways, engine view), GUIDED (plus tutor notes), BLIND (instrument, prices and dates masked, forward only, revealed on end) and QUIZ (predict direction / which ATR level is hit first / setup progress; graded once the bars are revealed). Education only; sessions live in memory and need no store. See `docs/phases/PHASE_19.md`.

## Verify

```bash
pwsh scripts/check-all.ps1
```

or `bash scripts/check-all.sh`.
