/**
 * Displacement + FVG/IFVG payload validation and overlay construction (client side, no analysis logic).
 */
import {
  DATA_QUALITIES,
  DIRECTIONS,
  DISPLACEMENT_GRADES,
  IFVG_STATUSES,
  PD_ARRAY_EVENT_TYPES,
  PD_ARRAY_STATES,
  PD_ARRAY_TYPES,
  type ChartCandle,
  type ChartTimeframe,
  type PdArrayAnalysis,
  type PdArrayZone,
} from "@fmcc/shared-types";

import type { Overlay, OverlayMarker, OverlaySegment } from "./structure";

export type PdArrayLoadState = { status: "READY"; analysis: PdArrayAnalysis } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));
const isPrice = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

function zoneError(z: unknown): string | null {
  if (!isObject(z) || typeof z.id !== "string") return "zone malformed";
  if (!member(PD_ARRAY_TYPES, z.type) || !member(DIRECTIONS, z.direction) || !member(PD_ARRAY_STATES, z.state)) {
    return "zone enum unknown";
  }
  if (!isPrice(z.top) || !isPrice(z.bottom) || z.bottom >= z.top) return "zone bounds invalid";
  if (typeof z.fillPct !== "number" || z.fillPct < 0 || z.fillPct > 100) return "fill % out of range";
  if (z.type === "IFVG" ? !member(IFVG_STATUSES, z.ifvgStatus) : z.ifvgStatus !== null) return "IFVG status inconsistent";
  if (!Array.isArray(z.sourceTimes) || !z.sourceTimes.every(isIso) || !isIso(z.createdAt)) return "zone times invalid";
  if (typeof z.active !== "boolean" || (z.qualityScore === null) !== !z.active) return "quality must exist exactly for active zones";
  return null;
}

export function reconcilePdArrays(symbol: string, timeframe: ChartTimeframe, payload: unknown): PdArrayLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "PD array API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed PD array payload" };
  if (payload.symbol !== symbol.toUpperCase() || payload.timeframe !== timeframe) {
    return { status: "UNAVAILABLE", reason: "PD array symbol/timeframe mismatch" };
  }
  const { zones, events, displacements } = payload;
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(zones) || !Array.isArray(events) || !Array.isArray(displacements)) {
    return { status: "UNAVAILABLE", reason: "Malformed PD array metadata" };
  }
  for (const z of zones) {
    const err = zoneError(z);
    if (err) return { status: "UNAVAILABLE", reason: `Rejected PD array payload: ${err}` };
  }
  const ids = new Set((zones as PdArrayZone[]).map((z) => z.id));
  for (const e of events) {
    if (!isObject(e) || typeof e.zoneId !== "string" || !ids.has(e.zoneId) || !member(PD_ARRAY_EVENT_TYPES, e.type) || !isIso(e.time)) {
      return { status: "UNAVAILABLE", reason: "Rejected PD array payload: event invalid" };
    }
  }
  for (const d of displacements) {
    if (!isObject(d) || !member(DISPLACEMENT_GRADES, d.grade) || !member(DIRECTIONS, d.direction) || !isIso(d.time)) {
      return { status: "UNAVAILABLE", reason: "Rejected PD array payload: displacement invalid" };
    }
  }
  if (zones.length === 0 && displacements.length === 0 && events.length === 0 && payload.candleCount === 0) {
    return { status: "UNAVAILABLE", reason: `No PD array analysis (data ${payload.quality})` };
  }
  return { status: "READY", analysis: payload as unknown as PdArrayAnalysis };
}

export function pdArraySyncError(analysis: PdArrayAnalysis, candles: ChartCandle[]): string | null {
  const times = new Set(candles.map((c) => Date.parse(c.time)));
  const inWindow = (iso: string) => times.has(Date.parse(iso));
  for (const z of analysis.zones) if (!inWindow(z.createdAt)) return "zone outside chart window";
  for (const d of analysis.displacements) if (!inWindow(d.time)) return "displacement outside chart window";
  return null;
}

export const FVG_BULL = "#3fb950";
export const FVG_BEAR = "#f85149";
export const IFVG_COLOR = "#a371f7";
export const MAX_ZONES = 10;
const MARKER_GRADES = new Set(["STRONG", "EXCEPTIONAL"]);
const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/**
 * Active zones (most recent MAX_ZONES) drawn as top/bottom edge segments from their first candle to the last
 * drawn candle. Lightweight Charts has no rectangle primitive. STRONG+ displacements get a marker.
 */
export function buildPdArrayOverlay(analysis: PdArrayAnalysis, candles: ChartCandle[]): Overlay {
  const end = candles.length ? sec(candles[candles.length - 1]!.time) : 0;
  const active = analysis.zones.filter((z) => z.active).slice(-MAX_ZONES);
  const segments: OverlaySegment[] = [];
  for (const z of active) {
    const from = sec(z.sourceTimes[0] ?? z.createdAt);
    if (from >= end) continue;
    const color = z.type === "IFVG" ? IFVG_COLOR : z.direction === "BULLISH" ? FVG_BULL : FVG_BEAR;
    segments.push({ id: `${z.id}:top`, from, to: end, price: z.top, color, dashed: z.type === "IFVG" });
    segments.push({ id: `${z.id}:bottom`, from, to: end, price: z.bottom, color, dashed: z.type === "IFVG" });
  }
  const markers: OverlayMarker[] = analysis.displacements
    .filter((d) => MARKER_GRADES.has(d.grade))
    .map((d) => ({
      time: sec(d.time),
      position: d.direction === "BULLISH" ? "belowBar" : "aboveBar",
      shape: d.direction === "BULLISH" ? "arrowUp" : "arrowDown",
      color: d.direction === "BULLISH" ? FVG_BULL : FVG_BEAR,
      text: `Disp ${d.grade === "EXCEPTIONAL" ? "X" : "S"}`,
      size: 0.6,
    }));
  return { markers, segments, priceLines: [] };
}
