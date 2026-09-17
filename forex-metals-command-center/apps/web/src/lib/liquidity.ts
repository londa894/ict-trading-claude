/**
 * Liquidity payload validation + overlay construction (client side, no analysis logic).
 */
import {
  DATA_QUALITIES,
  DOL_CONFIDENCES,
  LIQUIDITY_EVENT_TYPES,
  LIQUIDITY_POOL_TYPES,
  LIQUIDITY_SIDES,
  LIQUIDITY_STATES,
  type ChartCandle,
  type ChartTimeframe,
  type LiquidityAnalysis,
  type LiquidityEvent,
  type LiquidityPool,
} from "@fmcc/shared-types";

import type { Overlay, OverlayMarker, OverlayPriceLine } from "./structure";

export type LiquidityLoadState =
  | { status: "READY"; analysis: LiquidityAnalysis }
  | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));
const isPrice = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

function poolError(p: unknown): string | null {
  if (!isObject(p) || typeof p.id !== "string") return "pool malformed";
  if (!member(LIQUIDITY_POOL_TYPES, p.type) || !member(LIQUIDITY_SIDES, p.side) || !member(LIQUIDITY_STATES, p.state)) {
    return "pool enum unknown";
  }
  if (!isPrice(p.price) || !isIso(p.knownAt) || typeof p.taken !== "boolean") return "pool fields invalid";
  const score = p.magnetScore;
  if (score !== null && !(typeof score === "number" && score >= 0 && score <= 100)) return "magnet score out of range";
  if ((score === null) !== p.taken) return "magnet score must exist exactly for untaken pools";
  return null;
}

function eventError(e: unknown, poolIds: Set<string>): string | null {
  if (!isObject(e) || typeof e.poolId !== "string" || !poolIds.has(e.poolId)) return "event references unknown pool";
  if (!member(LIQUIDITY_EVENT_TYPES, e.type) || !member(LIQUIDITY_SIDES, e.side)) return "event enum unknown";
  if (!isPrice(e.price) || !isIso(e.time)) return "event fields invalid";
  return null;
}

export function reconcileLiquidity(symbol: string, timeframe: ChartTimeframe, payload: unknown): LiquidityLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Liquidity API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed liquidity payload" };
  if (payload.symbol !== symbol.toUpperCase() || payload.timeframe !== timeframe) {
    return { status: "UNAVAILABLE", reason: "Liquidity symbol/timeframe mismatch" };
  }
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(payload.pools) || !Array.isArray(payload.events)) {
    return { status: "UNAVAILABLE", reason: "Malformed liquidity metadata" };
  }
  for (const p of payload.pools) {
    const err = poolError(p);
    if (err) return { status: "UNAVAILABLE", reason: `Rejected liquidity payload: ${err}` };
  }
  const ids = new Set((payload.pools as LiquidityPool[]).map((p) => p.id));
  let previous = -Infinity;
  for (const e of payload.events) {
    const err = eventError(e, ids);
    if (err) return { status: "UNAVAILABLE", reason: `Rejected liquidity payload: ${err}` };
    const t = Date.parse((e as LiquidityEvent).time);
    if (t < previous) return { status: "UNAVAILABLE", reason: "Rejected liquidity payload: events not chronological" };
    previous = t;
  }
  const dol = payload.dol;
  if (dol !== null) {
    if (!isObject(dol) || !member(DOL_CONFIDENCES, dol.confidence)) {
      return { status: "UNAVAILABLE", reason: "Rejected liquidity payload: DOL malformed" };
    }
    for (const target of [dol.primary, dol.secondary]) {
      if (target !== null && (!isObject(target) || typeof target.poolId !== "string" || !ids.has(target.poolId))) {
        return { status: "UNAVAILABLE", reason: "Rejected liquidity payload: DOL references unknown pool" };
      }
    }
  }
  if (payload.pools.length === 0 && dol === null) {
    return { status: "UNAVAILABLE", reason: `No liquidity analysis (data ${payload.quality})` };
  }
  return { status: "READY", analysis: payload as unknown as LiquidityAnalysis };
}

export function liquiditySyncError(analysis: LiquidityAnalysis, candles: ChartCandle[]): string | null {
  const times = new Set(candles.map((c) => Date.parse(c.time)));
  return analysis.events.every((e) => times.has(Date.parse(e.time))) ? null : "liquidity event outside chart window";
}

export const DOL_COLOR = "#d29922";
export const BSL_COLOR = "#f85149";
export const SSL_COLOR = "#3fb950";
const KEY_TYPES = new Set(["PDH", "PDL", "PWH", "PWL", "EQH", "EQL"]);
const MARKER_TEXT: Partial<Record<LiquidityEvent["type"], string>> = {
  SWEEP: "Sweep",
  SWEEP_FAILED: "False sweep",
  RUN: "Run",
  RECLAIM: "Reclaim",
};
export const MAX_LIQUIDITY_MARKERS = 30;

/** Price lines: DOL targets + untaken key/EQ levels (latest per type). Markers: liquidity-taking events. */
export function buildLiquidityOverlay(analysis: LiquidityAnalysis): Overlay {
  const priceLines: OverlayPriceLine[] = [];
  const drawn = new Set<string>();
  const dol = analysis.dol;
  for (const [rank, target] of [
    ["DOL", dol?.primary],
    ["DOL2", dol?.secondary],
  ] as const) {
    if (!target) continue;
    drawn.add(target.poolId);
    priceLines.push({ price: target.price, title: `${rank} ${target.label}`, color: DOL_COLOR, style: "solid" });
  }
  const latestByType = new Map<string, LiquidityPool>();
  for (const p of analysis.pools) {
    if (!KEY_TYPES.has(p.type)) continue;
    const prev = latestByType.get(p.type);
    if (!prev || Date.parse(p.knownAt) > Date.parse(prev.knownAt)) latestByType.set(p.type, p);
  }
  // Latest per type is chosen across ALL pools first. If that level is taken or already drawn as a DOL, an
  // older level of the same type must not stand in for it (it would be mislabelled as the current PDH etc.).
  for (const p of latestByType.values()) {
    if (p.taken || drawn.has(p.id)) continue;
    priceLines.push({
      price: p.price,
      title: p.type,
      color: p.side === "BSL" ? BSL_COLOR : SSL_COLOR,
      style: "dashed",
    });
  }
  const markers: OverlayMarker[] = analysis.events
    .filter((e) => MARKER_TEXT[e.type] !== undefined)
    .slice(-MAX_LIQUIDITY_MARKERS)
    .map((e) => ({
      time: Math.floor(Date.parse(e.time) / 1000),
      position: e.side === "BSL" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: e.type === "SWEEP" || e.type === "RECLAIM" ? DOL_COLOR : e.side === "BSL" ? BSL_COLOR : SSL_COLOR,
      // Swing pools are implied; only name the notable ones (PDH/PWL/EQH...) to keep the chart readable.
      text: e.poolType === "SWING_HIGH" || e.poolType === "SWING_LOW" ? `${MARKER_TEXT[e.type]}` : `${MARKER_TEXT[e.type]} ${e.poolType}`,
      size: 0.5,
    }));
  return { markers, segments: [], priceLines };
}
