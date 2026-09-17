/**
 * Setup state machine payload validation and chart overlay (client side, no analysis logic).
 */
import {
  DATA_QUALITIES,
  DIRECTIONS,
  PO3_PHASES,
  SETUP_STATES,
  SETUP_STEP_STATUSES,
  SETUP_STEPS,
  type ChartCandle,
  type SetupAnalysis,
  type SetupEvent,
  type SetupState,
} from "@fmcc/shared-types";

import type { Overlay, OverlayMarker, OverlayPriceLine } from "./structure";

export type SetupLoadState = { status: "READY"; analysis: SetupAnalysis } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): v is string => typeof v === "string" && !Number.isNaN(Date.parse(v));

/** Without FULL verdict authority or trade tracking these states would be invented authority. */
export const AUTHORITY_STATES: ReadonlySet<SetupState> = new Set(["LONG_READY", "SHORT_READY", "ACTIVE", "CLOSED"]);
const TERMINAL: ReadonlySet<SetupState> = new Set(["INVALIDATED", "EXPIRED", "ENTRY_MISSED"]);
const NOT_YET_EVALUATED_STEPS = new Set(["RISK"]);

function setupError(s: unknown): string | null {
  if (!isObject(s) || typeof s.id !== "string" || !isIso(s.discoveredAt)) return "setup malformed";
  if (!member(SETUP_STATES, s.state) || !member(DIRECTIONS, s.direction)) return "setup enum unknown";
  if (AUTHORITY_STATES.has(s.state)) return `setup state ${s.state} is not allowed`;
  if (s.state === "BLOCKED" && !isObject(s.entryPlan)) return "BLOCKED setup without a confirmed plan";
  if (s.terminal !== TERMINAL.has(s.state)) return "setup terminal flag inconsistent";
  if (s.terminal ? typeof s.reason !== "string" : s.reason !== null) return "setup reason inconsistent";
  if (!Array.isArray(s.steps)) return "setup steps missing";
  for (const st of s.steps) {
    if (!isObject(st) || !member(SETUP_STEPS, st.step) || !member(SETUP_STEP_STATUSES, st.status)) return "setup step invalid";
    if (NOT_YET_EVALUATED_STEPS.has(st.step) && st.status !== "NOT_EVALUATED") return `${st.step} cannot be evaluated yet`;
  }
  return null;
}

export function reconcileSetups(symbol: string, payload: unknown): SetupLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Setup API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed setup payload" };
  if (payload.symbol !== symbol.toUpperCase()) return { status: "UNAVAILABLE", reason: "Setup symbol mismatch" };
  const { setups, events, po3, bias } = payload;
  if (!member(DATA_QUALITIES, payload.quality) || !Array.isArray(setups) || !Array.isArray(events) || !isObject(po3) || !isObject(bias)) {
    return { status: "UNAVAILABLE", reason: "Malformed setup metadata" };
  }
  const reject = (err: string): SetupLoadState => ({ status: "UNAVAILABLE", reason: `Rejected setup payload: ${err}` });
  if (!member(PO3_PHASES, po3.phase)) return reject("PO3 phase unknown");
  if (bias.direction !== null && !member(DIRECTIONS, bias.direction)) return reject("bias direction unknown");
  for (const s of setups) {
    const err = setupError(s);
    if (err) return reject(err);
  }
  const open = (setups as SetupAnalysis["setups"]).filter((s) => !s.terminal);
  if (open.length > 1) return reject("more than one open setup");
  const current = payload.current as SetupAnalysis["current"];
  if ((current?.id ?? null) !== (open[0]?.id ?? null)) return reject("current setup is not the open setup");
  const state = payload.currentState;
  if (state !== null && !member(SETUP_STATES, state)) return reject("current state unknown");
  if (!payload.eligibleForDecision && state !== "BLOCKED") return reject("ineligible data must be BLOCKED");
  if (payload.eligibleForDecision && (current?.state ?? null) !== state) return reject("current state mismatch");
  const ids = new Set((setups as SetupAnalysis["setups"]).map((s) => s.id));
  for (const e of events) {
    if (!isObject(e) || typeof e.setupId !== "string" || !ids.has(e.setupId) || !member(SETUP_STATES, e.state) || !isIso(e.time)) {
      return reject("event invalid");
    }
    if (AUTHORITY_STATES.has(e.state)) return reject(`event state ${e.state} is not allowed`);
  }
  return { status: "READY", analysis: payload as unknown as SetupAnalysis };
}

export const SETUP_TIMEFRAME = "M15";
export const SETUP_BULL = "#56d364";
export const SETUP_BEAR = "#ff7b72";
const MARKER_TEXT: Partial<Record<SetupState, string>> = {
  LIQUIDITY_EVENT: "Setup sweep",
  SETUP_ARMED: "Armed",
  ENTRY_ZONE_TOUCHED: "Zone touch",
  BLOCKED: "Confirmed (pending gates)",
  ENTRY_MISSED: "Entry missed",
  INVALIDATED: "Setup ✕",
  EXPIRED: "Setup exp",
};
const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

export function setupSyncError(analysis: SetupAnalysis, candles: ChartCandle[]): string | null {
  const times = new Set(candles.map((c) => Date.parse(c.time)));
  for (const e of analysis.events) if (MARKER_TEXT[e.state] && !times.has(Date.parse(e.time))) return "setup event outside chart window";
  return null;
}

/**
 * Milestone markers (sweep, armed, zone touch, confirmation, missed, invalidated/expired) for every setup, and — for
 * the open setup only — its DOL (TP1) and protective levels, plus a confirmed plan's entry/stop/TP2/TP3 labelled
 * "not authorized".
 */
export function buildSetupOverlay(analysis: SetupAnalysis): Overlay {
  const markers: OverlayMarker[] = analysis.events
    .filter((e): e is SetupEvent => Boolean(MARKER_TEXT[e.state]))
    .map((e) => {
      const bull = e.direction === "BULLISH";
      const terminal = TERMINAL.has(e.state);
      return {
        time: sec(e.time),
        position: bull === (e.state === "LIQUIDITY_EVENT" || e.state === "ENTRY_ZONE_TOUCHED") ? "belowBar" : "aboveBar",
        shape: terminal ? "circle" : bull ? "arrowUp" : "arrowDown",
        color: terminal ? "#8b949e" : bull ? SETUP_BULL : SETUP_BEAR,
        text: MARKER_TEXT[e.state]!,
        size: 0.7,
      };
    });
  const priceLines: OverlayPriceLine[] = [];
  const c = analysis.current;
  if (c) {
    if (c.target) priceLines.push({ price: c.target.price, title: `Setup DOL ${c.target.label}`, color: c.direction === "BULLISH" ? SETUP_BULL : SETUP_BEAR, style: "dotted" });
    if (c.protectiveLevel !== null) priceLines.push({ price: c.protectiveLevel, title: "Setup invalidation", color: "#8b949e", style: "dashed" });
    const plan = c.entryPlan;
    if (plan) {
      priceLines.push({ price: plan.entry, title: "Plan entry (not authorized)", color: "#d29922", style: "dashed" });
      priceLines.push({ price: plan.stop, title: "Plan stop (not authorized)", color: "#8b949e", style: "dotted" });
      for (const [label, tp] of [["TP2", plan.tp2], ["TP3", plan.tp3]] as const) {
        if (tp !== null) priceLines.push({ price: tp, title: `Plan ${label} (not authorized)`, color: "#8b949e", style: "dotted" });
      }
    }
  }
  return { markers, segments: [], priceLines };
}

