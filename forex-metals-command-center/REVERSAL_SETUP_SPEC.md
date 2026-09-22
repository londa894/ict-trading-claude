# Reversal Setup Spec — HTF No-Wick Rebalance + IFVG Flip (DRAFT v0.1)

Starting point for the Playbook revamp. Sketch only — not built yet. Complements (does not replace) the
existing bias-following continuation model `LIQUIDITY_SWEEP_MSS`.

## Why this exists
The current engine takes exactly one setup **in the HTF bias direction** (sweep → MSS in bias direction →
retracement entry). It correctly refuses counter-bias reversals. But the operator's real edge is a
**counter-bias reversal**: a higher-timeframe **no-wick candle** marks an imbalance; price trades back to
**rebalance** that area, **rejects**, and an **IFVG flips** in the reversal direction. This happens most
often in the **Asia session on Wed/Thu/Fri (sometimes Mon)**. The continuation model can't see it, so those
trades are tagged "context only" and skipped. This spec adds a second setup type to capture them.

## New setup type: `REVERSAL_NO_WICK_IFVG`
Runs **alongside** the continuation model on the same candle-sequential pass. Direction is the **reversal**
direction (opposite the local move into the zone), and is explicitly **allowed against the HTF bias** — but
only when confirmed by the full stack below, so it is not a naked counter-trend entry.

### Inputs
- **Multi-TF No-Wick** on **D1, H4, H1** (+ existing M15) — the "origin" zones. *(Being added now.)*
- **IFVG** events — already detected (`PdArrayType.IFVG`, `includeConfirmedIfvg`), tie each to its zone.
- **Liquidity pools** — for the target (opposite draw) and invalidation context.
- **Displacement / structure** — reaction quality.
- **Session + day-of-week** — Asia + Wed/Thu/Fri weighting.

### State machine (mirrors the existing engine's style)
```
DISCOVERED         a qualifying HTF (D1>H4>H1) no-wick zone exists (strength >= threshold)
REBALANCE_WATCH    price is trading back toward that zone's imbalance
REBALANCE_TOUCH    price enters/fills the no-wick imbalance (rebalance)
REACTION           rejection from the zone: a rejection wick and/or an opposite displacement candle close
IFVG_FLIP          an IFVG confirms in the REVERSAL direction within `flipWindowBars` of the reaction
ARMED              reversal confirmed (reaction + IFVG flip); protective = far edge of the no-wick origin
ENTRY_ZONE         retest of the flipped IFVG / no-wick edge
BLOCKED            entry model confirmed; plan built; pending risk + news gates (reuse Phase 8/9/13)
Terminal:
  INVALIDATED      close beyond the no-wick origin's far edge (the imbalance held the wrong way)
  EXPIRED          no reaction, or no IFVG flip within the window, or day/session window closed
  ENTRY_MISSED     chase guard: target (opposite draw) already reached before a confirmed entry
```

### Entry / stop / target
- **Entry:** limit at the flipped-IFVG edge, or reactive on the reaction candle close (M5/M15). Reuse the
  existing entry-model machinery; add `IFVG_FLIP` as a first-class entry model.
- **Stop:** beyond the **no-wick origin's far edge** + `stopBufferAtr` × ATR (tight, since a valid reversal
  should not revisit the origin).
- **Target (DOL):** the **opposite-side liquidity draw** — prior session high/low, PDH/PDL, equal highs/lows —
  same `eligible_targets` logic, mirrored for the reversal direction.

### Scoring factors (new weights in a `reversal` block of `scoring.json`)
| Factor | Meaning |
|---|---|
| `HTF_NO_WICK` | origin strength × timeframe weight (**D1 > H4 > H1**) |
| `REBALANCE` | clean fill of the imbalance + rejection depth |
| `IFVG_FLIP` | confirmed inversion in the reversal direction (mandatory) |
| `DISPLACEMENT` | reversal displacement on the reaction |
| `SESSION_DAY` | **Asia + Wed/Thu/Fri (sometimes Mon) boost**; other windows neutral/penalised |
| `LIQUIDITY_TARGET` | a clear opposite draw exists (R:R viable) |

### Bias handling (the key design decision)
The reversal is counter-bias by nature. Two options to gate it (pick per backtest):
1. **Always allowed** when the full stack (HTF no-wick rebalance + reject + IFVG flip) confirms, regardless of
   bias. Highest frequency, needs the strongest confirmation gate.
2. **Bias-aware**: allow the reversal only when HTF bias is `TRANSITIONING`/unclear, or when the HTF no-wick is
   on **D1** (a daily imbalance outranks the H4/H1 bias). Lower frequency, higher conviction.
Recommendation for v1: **option 2** (D1 no-wick, or unclear bias) — matches "reversal at a real HTF level."

### Coexistence with the continuation model
- Both models advance each candle. The reversal model owns HTF no-wick zones; the continuation model owns
  the bias-aligned sweep→MSS.
- **Conflict rule:** never emit a reversal LONG while a continuation SHORT is `BLOCKED` at overlapping price
  (and mirror). Resolve by grade/score; the higher-conviction one wins, the other goes `ENTRY_MISSED`.

### Reuse (what already exists)
- Directional verdict + ready-watch (Tier-2, `verdict_authority()` FULL) — reversal signals ride the same path.
- Risk / news gates, chase guard, entry-plan builder, analytics/backtest engine.
- IFVG detection, liquidity pools/targets, displacement, sessions/day-of-week.

## Build phases
1. **Multi-TF No-Wick** (D1/H4/H1) detection + exposure. *(In progress now.)*
2. IFVG-flip events tied to no-wick zones (reversal-direction confirmation).
3. `REVERSAL_NO_WICK_IFVG` state machine (new `setup_state` engine path or a sibling engine).
4. `reversal` scoring block + day/session weighting.
5. Directional verdict + ready-watch integration (reuse Tier-2).
6. Backtest against `DATA_ROOT/cleaned` XAUUSD, focus on Asia Wed/Thu/Fri.

## Open questions for the operator (to finalise before build)
1. **Origin timeframe:** D1 only, or D1+H4+H1? (spec assumes all three, D1 weighted highest)
2. **Rebalance:** full fill of the no-wick body, or a touch of its origin edge?
3. **Reaction:** rejection wick, opposite displacement, or both required?
4. **IFVG flip:** mandatory (recommended) or optional confirmation?
5. **Bias gate:** option 1 (always) or option 2 (D1/unclear only)?
6. **Target:** nearest opposite draw, or a specific pool type (PDH/PDL, session H/L)?
7. **Day/session:** hard filter (only trade Asia Wed/Thu/Fri) or soft score boost?
