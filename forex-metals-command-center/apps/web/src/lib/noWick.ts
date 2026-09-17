/**
 * No Wick payload validation and overlay construction (client side, no analysis logic).
 */
import {
  DATA_QUALITIES,
  DIRECTIONS,
  NO_WICK_CLASSIFICATIONS,
  NO_WICK_CONTEXT_FACTORS,
  NO_WICK_STRENGTHS,
  NO_WICK_ZONE_EVENT_TYPES,
  NO_WICK_ZONE_STATES,
  SCORE_COMPONENT_STATUSES,
  TIMEFRAMES,
  type ChartCandle,
  type ChartTimeframe,
  type MasterDecision,
  type NoWickAnalysis,
  type NoWickDecisionState,
  type NoWickEvent,
  type NoWickStrength,
  type NoWickZone,
  type NoWickZoneState,
} from "@fmcc/shared-types";

import type { Overlay, OverlayMarker, OverlaySegment } from "./structure";

export type NoWickLoadState = { status: "READY"; analysis: NoWickAnalysis } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));
const isPrice = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
const isScore = (v: unknown): v is number => typeof v === "number" && v >= 0 && v <= 100;

export const ACTIVE_ZONE_STATES: ReadonlySet<NoWickZoneState> = new Set([
  "FRESH",
  "APPROACHING",
  "TOUCHED",
  "PARTIAL",
  "HALF_REBALANCED",
]);
const STRENGTH_RANK: Record<NoWickStrength, number> = { INSIGNIFICANT: 0, MEANINGFUL: 1, STRONG: 2, EXCEPTIONAL: 3 };
export const isMeaningful = (e: { strength: NoWickStrength }) => STRENGTH_RANK[e.strength] >= 1;

function eventError(e: unknown): string | null {
  if (!isObject(e) || typeof e.id !== "string" || !isIso(e.time)) return "event malformed";
  if (!member(DIRECTIONS, e.direction) || !member(NO_WICK_STRENGTHS, e.strength)) return "event enum unknown";
  if (!member(NO_WICK_CLASSIFICATIONS, e.classification) || !member(NO_WICK_CLASSIFICATIONS, e.shape)) {
    return "classification unknown";
  }
  const tags = Array.isArray(e.tags) ? e.tags : null;
  if (!tags || !tags.every((t) => member(NO_WICK_CLASSIFICATIONS, t))) return "tags invalid";
  // The economic calendar does not exist yet: a news-driven label would be an invented market fact.
  if (e.classification === "NEWS_DRIVEN_NO_WICK" || tags.includes("NEWS_DRIVEN_NO_WICK")) {
    return "news-driven label without a calendar";
  }
  if (!isScore(e.candleQualityScore) || !isScore(e.contextScore) || !isScore(e.relevanceScore)) {
    return "score out of range";
  }
  if (!Array.isArray(e.contextComponents)) return "context components missing";
  for (const c of e.contextComponents) {
    if (!isObject(c) || !member(NO_WICK_CONTEXT_FACTORS, c.factor) || !member(SCORE_COMPONENT_STATUSES, c.status)) {
      return "context component invalid";
    }
    if (c.status === "NOT_EVALUATED" && c.points !== 0) return "unevaluated component carries points";
  }
  return null;
}

function zoneError(z: unknown, eventIds: Set<string>): string | null {
  if (!isObject(z) || typeof z.id !== "string" || typeof z.eventId !== "string") return "zone malformed";
  if (!eventIds.has(z.eventId)) return "zone for unknown event";
  if (!member(DIRECTIONS, z.direction) || !member(NO_WICK_ZONE_STATES, z.state)) return "zone enum unknown";
  if (![z.closeLevel, z.openLevel, z.level25, z.level50, z.level75, z.originExtreme].every(isPrice)) {
    return "zone levels invalid";
  }
  if (!isScore(z.rebalancePct) || !isIso(z.createdAt)) return "zone fields invalid";
  if (typeof z.active !== "boolean" || z.active !== ACTIVE_ZONE_STATES.has(z.state)) return "zone activity inconsistent";
  return null;
}

export function reconcileNoWick(symbol: string, timeframe: ChartTimeframe, payload: unknown): NoWickLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "No Wick API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed No Wick payload" };
  if (payload.symbol !== symbol.toUpperCase() || payload.timeframe !== timeframe) {
    return { status: "UNAVAILABLE", reason: "No Wick symbol/timeframe mismatch" };
  }
  const { events, zones, zoneEvents } = payload;
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(events) || !Array.isArray(zones) || !Array.isArray(zoneEvents)) {
    return { status: "UNAVAILABLE", reason: "Malformed No Wick metadata" };
  }
  for (const e of events) {
    const err = eventError(e);
    if (err) return { status: "UNAVAILABLE", reason: `Rejected No Wick payload: ${err}` };
  }
  const eventIds = new Set((events as NoWickEvent[]).map((e) => e.id));
  for (const z of zones) {
    const err = zoneError(z, eventIds);
    if (err) return { status: "UNAVAILABLE", reason: `Rejected No Wick payload: ${err}` };
  }
  const zoneIds = new Set((zones as NoWickZone[]).map((z) => z.id));
  for (const e of zoneEvents) {
    if (!isObject(e) || typeof e.zoneId !== "string" || !zoneIds.has(e.zoneId) || !member(NO_WICK_ZONE_EVENT_TYPES, e.type) || !isIso(e.time)) {
      return { status: "UNAVAILABLE", reason: "Rejected No Wick payload: zone event invalid" };
    }
  }
  if (events.length === 0 && zones.length === 0 && payload.candleCount === 0) {
    return { status: "UNAVAILABLE", reason: `No No Wick analysis (data ${payload.quality})` };
  }
  return { status: "READY", analysis: payload as unknown as NoWickAnalysis };
}

export function noWickSyncError(analysis: NoWickAnalysis, candles: ChartCandle[]): string | null {
  const times = new Set(candles.map((c) => Date.parse(c.time)));
  const inWindow = (iso: string) => times.has(Date.parse(iso));
  for (const e of analysis.events) if (isMeaningful(e) && !inWindow(e.time)) return "no-wick candle outside chart window";
  for (const z of analysis.zones) if (z.active && !inWindow(z.createdAt)) return "no-wick zone outside chart window";
  return null;
}

/** Validates MasterDecision.noWickState; anything unexpected is treated as absent. */
export function readNoWickState(decision: MasterDecision): NoWickDecisionState | null {
  const s: unknown = decision.noWickState;
  if (!isObject(s) || s.authority !== "CONTEXT_ONLY") return null;
  if (!member(TIMEFRAMES, s.timeframe) || !member(DIRECTIONS, s.direction) || !member(NO_WICK_STRENGTHS, s.strength)) return null;
  if (!member(NO_WICK_CLASSIFICATIONS, s.classification) || !isIso(s.time) || !isScore(s.relevanceScore)) return null;
  if (s.zoneState !== null && !member(NO_WICK_ZONE_STATES, s.zoneState)) return null;
  return s as unknown as NoWickDecisionState;
}

export function formatNoWickState(s: NoWickDecisionState): string {
  const zone = s.zoneState ? ` · zone ${s.zoneState}` : "";
  return `${s.timeframe} ${s.direction} ${s.classification} ${s.strength} · relevance ${s.relevanceScore}${zone} (${s.time})`;
}

export const NW_BULL = "#2dd4bf";
export const NW_BEAR = "#f0883e";
export const MAX_NW_MARKERS = 30;
export const MAX_NW_ZONES = 6;
const STRENGTH_TEXT: Record<NoWickStrength, string> = { INSIGNIFICANT: "", MEANINGFUL: "M", STRONG: "S", EXCEPTIONAL: "X" };
const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/**
 * MEANINGFUL+ no-wick candles get a small marker; the most recent active rebalance zones are drawn as a solid
 * close-level segment and a dashed open-level segment from the source candle to the last drawn candle.
 */
export function buildNoWickOverlay(analysis: NoWickAnalysis, candles: ChartCandle[]): Overlay {
  const end = candles.length ? sec(candles[candles.length - 1]!.time) : 0;
  const markers: OverlayMarker[] = analysis.events
    .filter(isMeaningful)
    .slice(-MAX_NW_MARKERS)
    .map((e) => ({
      time: sec(e.time),
      position: e.direction === "BULLISH" ? "belowBar" : "aboveBar",
      shape: "circle",
      color: e.direction === "BULLISH" ? NW_BULL : NW_BEAR,
      text: `NW ${STRENGTH_TEXT[e.strength]}`,
      size: 0.5,
    }));
  const segments: OverlaySegment[] = [];
  for (const z of analysis.zones.filter((zone) => zone.active).slice(-MAX_NW_ZONES)) {
    const from = sec(z.createdAt);
    if (from >= end) continue;
    const color = z.direction === "BULLISH" ? NW_BULL : NW_BEAR;
    segments.push({ id: `${z.id}:close`, from, to: end, price: z.closeLevel, color, dashed: false });
    segments.push({ id: `${z.id}:open`, from, to: end, price: z.openLevel, color, dashed: true });
  }
  return { markers, segments, priceLines: [] };
}
