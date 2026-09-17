/**
 * Structure payload validation + overlay construction (client side, no analysis logic).
 * The backend computes structure; this module only verifies the payload and maps it to chart primitives.
 */
import {
  DATA_QUALITIES,
  DIRECTIONS,
  STRUCTURE_EVENT_STATUSES,
  STRUCTURE_EVENT_TYPES,
  STRUCTURE_LEVELS,
  STRUCTURE_STATES,
  SWING_KINDS,
  SWING_LABELS,
  type ChartCandle,
  type ChartTimeframe,
  type LevelStructure,
  type MtfStructureResponse,
  type StructureAnalysis,
  type StructureEvent,
  type Swing,
} from "@fmcc/shared-types";

export type StructureLoadState =
  | { status: "READY"; analysis: StructureAnalysis }
  | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));
const isPrice = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

function swingError(s: unknown): string | null {
  if (!isObject(s)) return "swing is not an object";
  if (typeof s.id !== "string" || !member(STRUCTURE_LEVELS, s.level) || !member(SWING_KINDS, s.kind)) return "bad swing identity";
  if (!member(SWING_LABELS, s.label) || !isPrice(s.price) || !isIso(s.time) || !isIso(s.confirmedAt)) return "bad swing fields";
  if (Date.parse(s.confirmedAt) <= Date.parse(s.time)) return "swing confirmed before it formed";
  return null;
}

function eventError(e: unknown): string | null {
  if (!isObject(e)) return "event is not an object";
  if (!member(STRUCTURE_EVENT_TYPES, e.type) || !member(DIRECTIONS, e.direction)) return "bad event type";
  if (!member(STRUCTURE_EVENT_STATUSES, e.status) || !member(STRUCTURE_LEVELS, e.level)) return "bad event status";
  if (!isPrice(e.price) || !isIso(e.time) || !isIso(e.brokenSwingTime)) return "bad event fields";
  if (Date.parse(e.brokenSwingTime) >= Date.parse(e.time)) return "event precedes its swing";
  return null;
}

function levelError(level: unknown, expected: string): string | null {
  if (level === null) return null;
  if (!isObject(level) || level.level !== expected) return `${expected} level malformed`;
  if (!member(STRUCTURE_STATES, level.state)) return `${expected} state unknown`;
  if (!Array.isArray(level.swings) || !Array.isArray(level.events)) return `${expected} lists missing`;
  for (const s of level.swings) {
    const err = swingError(s);
    if (err) return `${expected}: ${err}`;
  }
  let previous = -Infinity;
  for (const e of level.events) {
    const err = eventError(e);
    if (err) return `${expected}: ${err}`;
    const t = Date.parse((e as StructureEvent).time);
    if (t < previous) return `${expected}: events not chronological`;
    previous = t;
  }
  return null;
}

export function reconcileStructure(symbol: string, timeframe: ChartTimeframe, payload: unknown): StructureLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Structure API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed structure payload" };
  if (payload.symbol !== symbol.toUpperCase() || payload.timeframe !== timeframe) {
    return { status: "UNAVAILABLE", reason: "Structure symbol/timeframe mismatch" };
  }
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(payload.ineligibility)) {
    return { status: "UNAVAILABLE", reason: "Malformed structure metadata" };
  }
  const err = levelError(payload.internal, "INTERNAL") ?? levelError(payload.external, "EXTERNAL");
  if (err) return { status: "UNAVAILABLE", reason: `Rejected structure payload: ${err}` };
  if (payload.internal === null && payload.external === null) {
    return { status: "UNAVAILABLE", reason: `No structure analysis (data ${payload.quality})` };
  }
  return { status: "READY", analysis: payload as unknown as StructureAnalysis };
}

export function isAlignment(payload: unknown): payload is MtfStructureResponse {
  return isObject(payload) && typeof payload.alignment === "string" && Array.isArray(payload.timeframes);
}

/**
 * Overlay may only be drawn if every anchor exists among the drawn candles (same data window). If the
 * chart and structure polls are out of step, the overlay is hidden rather than misplaced.
 */
export function overlaySyncError(analysis: StructureAnalysis, candles: ChartCandle[]): string | null {
  const times = new Set(candles.map((c) => Date.parse(c.time)));
  const levels = [analysis.internal, analysis.external].filter((l): l is LevelStructure => l !== null);
  for (const level of levels) {
    for (const s of level.swings) if (!times.has(Date.parse(s.time))) return "swing outside chart window";
    for (const e of level.events) {
      if (!times.has(Date.parse(e.time)) || !times.has(Date.parse(e.brokenSwingTime))) return "event outside chart window";
    }
  }
  return null;
}

export const EVENT_TEXT: Record<StructureEvent["type"], string> = { BOS: "BOS", CHOCH: "CHoCH", MSS: "MSS" };

export type OverlayMarker = {
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "circle" | "arrowUp" | "arrowDown";
  color: string;
  text: string;
  size: number;
};
export type OverlaySegment = { from: number; to: number; price: number; color: string; dashed: boolean; id: string };
export type OverlayPriceLine = { price: number; title: string; color: string; style?: "solid" | "dashed" | "dotted" };
export type Overlay = { markers: OverlayMarker[]; segments: OverlaySegment[]; priceLines: OverlayPriceLine[] };

export const BULL = "#3fb950";
export const BEAR = "#f85149";
export const POTENTIAL = "#8b949e";
export const MAX_SEGMENTS = 40;

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

export type OverlayOptions = { external: boolean; internal: boolean };

function swingMarkers(swings: Swing[], small: boolean): OverlayMarker[] {
  return swings
    .filter((s) => s.label !== "NONE")
    .map((s) => ({
      time: sec(s.time),
      position: s.kind === "HIGH" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: s.kind === "HIGH" ? BEAR : BULL,
      text: small ? s.label.toLowerCase() : s.label,
      size: small ? 0.4 : 0.6,
    }));
}

function eventMarkers(events: StructureEvent[], internal: boolean): OverlayMarker[] {
  return events.map((e) => {
    const bullish = e.direction === "BULLISH";
    const confirmed = e.status === "CONFIRMED";
    const label = `${EVENT_TEXT[e.type]}${confirmed ? "" : "?"}`;
    return {
      time: sec(e.time),
      position: bullish ? "belowBar" : "aboveBar",
      shape: bullish ? "arrowUp" : "arrowDown",
      color: confirmed ? (bullish ? BULL : BEAR) : POTENTIAL,
      text: internal ? label.toLowerCase() : label,
      size: internal ? 0.6 : 1,
    };
  });
}

export function buildOverlay(analysis: StructureAnalysis, options: OverlayOptions): Overlay {
  const markers: OverlayMarker[] = [];
  const segments: OverlaySegment[] = [];
  const priceLines: OverlayPriceLine[] = [];
  const add = (level: LevelStructure | null, internal: boolean) => {
    if (!level) return;
    markers.push(...swingMarkers(level.swings, internal), ...eventMarkers(level.events, internal));
    for (const e of level.events.filter((x) => x.status === "CONFIRMED").slice(-MAX_SEGMENTS)) {
      segments.push({
        id: e.id,
        from: sec(e.brokenSwingTime),
        to: sec(e.time),
        price: e.price,
        color: e.direction === "BULLISH" ? BULL : BEAR,
        dashed: internal,
      });
    }
    if (!internal) {
      if (level.protectedHigh) priceLines.push({ price: level.protectedHigh.price, title: "Protected High", color: BEAR });
      if (level.protectedLow) priceLines.push({ price: level.protectedLow.price, title: "Protected Low", color: BULL });
    }
  };
  if (options.external) add(analysis.external, false);
  if (options.internal) add(analysis.internal, true);
  markers.sort((a, b) => a.time - b.time);
  return { markers, segments, priceLines };
}

export function mergeOverlays(...overlays: Array<Overlay | null>): Overlay | null {
  const present = overlays.filter((o): o is Overlay => o !== null);
  if (present.length === 0) return null;
  return {
    markers: present.flatMap((o) => o.markers).sort((a, b) => a.time - b.time),
    segments: present.flatMap((o) => o.segments),
    priceLines: present.flatMap((o) => o.priceLines),
  };
}
