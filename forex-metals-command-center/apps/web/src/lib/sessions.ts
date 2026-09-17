/**
 * Session & Time payload validation, chart overlay and display-time-zone formatting (no analysis logic).
 */
import {
  ASIAN_RANGE_STATES,
  DATA_QUALITIES,
  DIRECTIONS,
  EXPANSION_STATES,
  JUDAS_STATUSES,
  KILL_ZONES,
  MARKET_STATUSES,
  SESSION_INSTANCE_STATES,
  SESSION_NAMES,
  SESSION_QUALITIES,
  type ChartCandle,
  type ChartTimeframe,
  type MasterDecision,
  type SessionAnalysis,
  type SessionDecisionState,
  type SessionInstance,
  type SessionName,
} from "@fmcc/shared-types";

import type { Overlay, OverlaySegment } from "./structure";

export type SessionLoadState = { status: "READY"; analysis: SessionAnalysis } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));
const isPrice = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
const nullOr = <T>(v: unknown, check: (x: unknown) => x is T): boolean => v === null || check(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function clockError(c: unknown): string | null {
  if (!isObject(c) || !isIso(c.now) || typeof c.tradingDay !== "string") return "clock malformed";
  if (!member(MARKET_STATUSES, c.marketStatus) || !member(SESSION_QUALITIES, c.timeQuality)) return "clock enum unknown";
  const sessions = Array.isArray(c.activeSessions) ? c.activeSessions : null;
  const zones = Array.isArray(c.activeKillZones) ? c.activeKillZones : null;
  if (!sessions?.every((s) => member(SESSION_NAMES, s)) || !zones?.every((z) => member(KILL_ZONES, z))) {
    return "active windows invalid";
  }
  if (c.nextSession !== null && !member(SESSION_NAMES, c.nextSession)) return "next session invalid";
  return null;
}

function instanceError(i: unknown): string | null {
  if (!isObject(i) || typeof i.id !== "string" || !isIso(i.start) || !isIso(i.end)) return "instance malformed";
  if (!member(SESSION_NAMES, i.session) || !member(SESSION_INSTANCE_STATES, i.state)) return "instance enum unknown";
  if (Date.parse(i.start) >= Date.parse(i.end)) return "instance window inverted";
  if (!nullOr(i.high, isPrice) || !nullOr(i.low, isPrice)) return "instance levels invalid";
  if ((i.high === null) !== (i.low === null)) return "instance levels incomplete";
  if (typeof i.high === "number" && typeof i.low === "number" && i.low > i.high) return "instance low above high";
  if (i.state === "NOT_STARTED" && i.high !== null) return "levels before the session started";
  const complete = i.state === "COMPLETE";
  if (complete !== (i.knownAt !== null) || (complete && (i.knownAt !== i.end || i.candleCount !== i.expectedCount))) {
    return "instance completeness inconsistent";
  }
  if (i.asianRangeState !== null && !(complete && i.session === "ASIA" && member(ASIAN_RANGE_STATES, i.asianRangeState))) {
    return "Asian range state on a non-complete or non-Asian session";
  }
  return null;
}

function judasError(j: unknown, instanceIds: Set<string>): string | null {
  if (!isObject(j) || typeof j.id !== "string" || !isIso(j.sweepTime)) return "Judas malformed";
  if (!member(SESSION_NAMES, j.session) || !member(DIRECTIONS, j.direction) || !member(JUDAS_STATUSES, j.status)) {
    return "Judas enum unknown";
  }
  if (!instanceIds.has(`${j.session}:${String(j.tradingDay)}`)) return "Judas for unknown session";
  if (!(isNum(j.asianLow) && isNum(j.asianMidpoint) && isNum(j.asianHigh) && j.asianLow < j.asianMidpoint && j.asianMidpoint < j.asianHigh)) {
    return "Judas Asian range invalid";
  }
  if ((j.resolvedAt === null) !== (j.status === "CANDIDATE")) return "Judas resolution inconsistent";
  return null;
}

export function reconcileSessions(symbol: string, payload: unknown): SessionLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Session API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed session payload" };
  if (payload.symbol !== symbol.toUpperCase()) return { status: "UNAVAILABLE", reason: "Session symbol mismatch" };
  const { instances, judas, opens, adr } = payload;
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(instances) || !Array.isArray(judas) || !isObject(opens)) {
    return { status: "UNAVAILABLE", reason: "Malformed session metadata" };
  }
  const reject = (err: string): SessionLoadState => ({ status: "UNAVAILABLE", reason: `Rejected session payload: ${err}` });
  const clockErr = clockError(payload.clock);
  if (clockErr) return reject(clockErr);
  if (payload.sessionQuality !== null && !member(SESSION_QUALITIES, payload.sessionQuality)) return reject("session quality unknown");
  for (const i of instances) {
    const err = instanceError(i);
    if (err) return reject(err);
  }
  const ids = new Set((instances as SessionInstance[]).map((i) => i.id));
  for (const j of judas) {
    const err = judasError(j, ids);
    if (err) return reject(err);
  }
  for (const key of ["dailyOpen", "nyMidnightOpen", "weeklyOpen", "lastClose"]) {
    if (!nullOr(opens[key], isPrice)) return reject(`${key} invalid`);
  }
  if (!nullOr(opens.dailyChange, isNum)) return reject("daily change invalid");
  if (adr !== null && !(isObject(adr) && isPrice(adr.adr) && nullOr(adr.expansion, (x): x is string => member(EXPANSION_STATES, x)))) {
    return reject("ADR invalid");
  }
  return { status: "READY", analysis: payload as unknown as SessionAnalysis };
}

/** Validates MasterDecision.sessionState; anything unexpected is treated as absent. */
export function readSessionState(decision: MasterDecision): SessionDecisionState | null {
  const s: unknown = decision.sessionState;
  if (!isObject(s) || s.authority !== "CONTEXT_ONLY") return null;
  if (!member(SESSION_QUALITIES, s.timeQuality) || !member(MARKET_STATUSES, s.marketStatus)) return null;
  if (!Array.isArray(s.activeSessions) || !s.activeSessions.every((x) => member(SESSION_NAMES, x))) return null;
  if (!Array.isArray(s.activeKillZones) || !s.activeKillZones.every((x) => member(KILL_ZONES, x))) return null;
  if (s.sessionQuality !== null && !member(SESSION_QUALITIES, s.sessionQuality)) return null;
  return s as unknown as SessionDecisionState;
}

export function formatSessionState(s: SessionDecisionState): string {
  const windows = [...s.activeSessions, ...s.activeKillZones].join(" + ") || "no session";
  const adr = s.adrPctUsed !== null ? ` · ADR ${s.adrPctUsed}% ${s.expansion ?? ""}`.trimEnd() : "";
  return `${windows} · time ${s.timeQuality} · session ${s.sessionQuality ?? "—"}${adr}`;
}

/** Short status-bar label from the time-only clock. */
export function clockLabel(a: SessionAnalysis): string {
  const c = a.clock;
  const windows = [...c.activeSessions, ...c.activeKillZones].join(" + ");
  return `${windows || (c.marketStatus === "OPEN" ? "Between sessions" : c.marketStatus)} · ${c.timeQuality}`;
}

export function dailyChangeLabel(a: SessionAnalysis): string | null {
  const { dailyChange, dailyChangePct } = a.opens;
  if (dailyChange === null || dailyChangePct === null) return null;
  const sign = dailyChange > 0 ? "+" : "";
  return `${sign}${dailyChange.toFixed(2)} (${sign}${dailyChangePct.toFixed(2)}%)`;
}

// --- chart overlay -----------------------------------------------------------------------------------------------

export const SESSION_COLORS: Record<SessionName, string> = {
  ASIA: "#d29922",
  LONDON: "#58a6ff",
  NY_AM: "#db61a2",
  NY_PM: "#bc8cff",
  LONDON_CLOSE: "#8b949e",
};
const DRAWN_SESSIONS: ReadonlySet<SessionName> = new Set(["ASIA", "LONDON", "NY_AM", "NY_PM"]);
export const SESSION_TIMEFRAMES: ReadonlySet<ChartTimeframe> = new Set(["M5", "M15", "H1"]);
export const MAX_SESSION_BOXES = 12;
const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/**
 * High/low segments for the most recent COMPLETE (solid) and FORMING (dashed) sessions, snapped to the drawn
 * candles: from the first candle opening inside the window to the last one opening before its end. Windows
 * that contain no drawn candle, or reach before the first drawn candle, are skipped (never extrapolated).
 */
export function buildSessionOverlay(analysis: SessionAnalysis, candles: ChartCandle[]): Overlay {
  const times = candles.map((c) => sec(c.time));
  const first = times[0];
  const segments: OverlaySegment[] = [];
  if (first === undefined) return { markers: [], segments, priceLines: [] };
  const drawable = analysis.instances.filter(
    (i) => DRAWN_SESSIONS.has(i.session) && (i.state === "COMPLETE" || i.state === "FORMING") && i.high !== null && i.low !== null,
  );
  for (const i of drawable.slice(-MAX_SESSION_BOXES)) {
    const start = sec(i.start);
    const end = sec(i.end);
    if (start < first) continue;
    const inside = times.filter((t) => t >= start && t < end);
    if (inside.length === 0) continue;
    const from = inside[0]!;
    const to = inside[inside.length - 1]!;
    if (to <= from) continue;
    const dashed = i.state === "FORMING";
    const color = SESSION_COLORS[i.session];
    segments.push({ id: `${i.id}:high`, from, to, price: i.high!, color, dashed });
    segments.push({ id: `${i.id}:low`, from, to, price: i.low!, color, dashed });
  }
  return { markers: [], segments, priceLines: [] };
}

// --- display time zones ------------------------------------------------------------------------------------------

export const DISPLAY_ZONES = [
  { id: "UTC", label: "UTC" },
  { id: "America/New_York", label: "New York" },
  { id: "Europe/London", label: "London" },
  { id: "local", label: "Local" },
] as const;
export type DisplayZone = (typeof DISPLAY_ZONES)[number]["id"];

function parts(seconds: number, zone: DisplayZone): Record<string, string> {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone === "local" ? undefined : zone,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(fmt.formatToParts(new Date(seconds * 1000)).map((p) => [p.type, p.value]));
}

/** Crosshair label, e.g. "09 Jan 2024 05:00". Formatting only; candle times stay UTC internally. */
export function formatChartTime(seconds: number, zone: DisplayZone): string {
  const p = parts(seconds, zone);
  return `${p.day} ${p.month} ${p.year} ${p.hour}:${p.minute}`;
}

/** Axis tick label. Lightweight Charts tick types: 0 year, 1 month, 2 day, 3 time, 4 time with seconds. */
export function formatTick(seconds: number, tickType: number, zone: DisplayZone): string {
  const p = parts(seconds, zone);
  if (tickType === 0) return p.year!;
  if (tickType === 1) return p.month!;
  if (tickType === 2) return `${p.day} ${p.month}`;
  return `${p.hour}:${p.minute}`;
}

