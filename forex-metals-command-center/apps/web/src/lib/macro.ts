/**
 * Macro client (Phase 14). Macro is context: it adjusts score and confidence and never blocks. The client refuses a
 * macro payload that claims a bias without available data, a score outside -1..+1, a bias that does not match the
 * score, or a state versus a setup direction that does not match the score.
 */
import {
  CORRELATION_REGIMES,
  MACRO_BIASES,
  MACRO_STATES,
  SERIES_DIRECTIONS,
  type MacroAssessment,
  type MacroBias,
  type MacroState,
  type MasterDecision,
} from "@fmcc/shared-types";

export type MacroLoadState = { status: "READY"; macro: MacroAssessment } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);

const EPS = 1e-6;

export function biasFor(score: number, supportive: number): MacroBias {
  return score >= supportive - EPS ? "BULLISH" : score <= -supportive + EPS ? "BEARISH" : "NEUTRAL";
}

export function stateFor(score: number, direction: "BULLISH" | "BEARISH", supportive: number, strong: number): MacroState {
  const s = direction === "BULLISH" ? score : -score;
  if (s >= strong - EPS) return "STRONGLY_SUPPORTIVE";
  if (s >= supportive - EPS) return "SUPPORTIVE";
  if (s > -supportive + EPS) return "NEUTRAL";
  if (s > -strong + EPS) return "CONFLICT";
  return "STRONG_CONFLICT";
}

export function macroError(symbol: string, m: unknown): string | null {
  if (!isObject(m)) return "macro payload malformed";
  if (m.symbol !== symbol.toUpperCase()) return "macro for another market";
  if (!member(MACRO_BIASES, m.bias)) return "macro bias unknown";
  if (m.state !== null && !member(MACRO_STATES, m.state)) return "macro state unknown";
  if (typeof m.available !== "boolean" || typeof m.isSynthetic !== "boolean") return "macro availability missing";
  if (!Array.isArray(m.drivers) || !Array.isArray(m.series) || !Array.isArray(m.warnings)) return "macro fields missing";
  if ("blockers" in m) return "macro must never carry blockers";
  if (!m.available) {
    if (m.bias !== "UNAVAILABLE" || m.score !== null) return "bias claimed without macro data";
    if (m.state !== null && m.state !== "UNAVAILABLE") return "state claimed without macro data";
    return null;
  }
  const score = m.score;
  if (typeof score !== "number" || score < -1 - EPS || score > 1 + EPS) return "macro score out of range";
  const t = m.thresholds;
  if (!isObject(t) || typeof t.supportive !== "number" || typeof t.stronglySupportive !== "number") return "macro thresholds missing";
  if (m.bias !== biasFor(score, t.supportive)) return "macro bias does not match the score";
  if (m.direction === null) {
    if (m.state !== null) return "macro state without a direction";
  } else if (m.direction !== "BULLISH" && m.direction !== "BEARISH") {
    return "macro direction unknown";
  } else if (m.state !== stateFor(score, m.direction, t.supportive, t.stronglySupportive)) {
    return "macro state does not match the score";
  }
  for (const d of m.drivers) {
    if (!isObject(d) || (d.direction !== null && !member(SERIES_DIRECTIONS, d.direction))) return "macro driver invalid";
  }
  const c = m.correlation;
  if (c !== null && (!isObject(c) || !member(CORRELATION_REGIMES, c.regime))) return "correlation regime unknown";
  return null;
}

export function reconcileMacro(symbol: string, payload: unknown): MacroLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Macro API unreachable" };
  const err = macroError(symbol, payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected macro: ${err}` } : { status: "READY", macro: payload as MacroAssessment };
}

export type MacroDecisionState = {
  authority: "CONTEXT_ONLY";
  state: MacroState | null;
  bias: MacroBias;
  score: number | null;
  available: boolean;
  synthetic: boolean;
  correlationRegime: string | null;
};

export function readMacroState(decision: MasterDecision): MacroDecisionState | null {
  const s: unknown = decision.macroState;
  if (!isObject(s) || s.authority !== "CONTEXT_ONLY" || !member(MACRO_BIASES, s.bias) || typeof s.available !== "boolean") return null;
  return s as unknown as MacroDecisionState;
}

/** Status-bar label: bias, the state versus the open setup when there is one, and the synthetic flag. */
export function macroLabel(s: MacroDecisionState | null): string {
  if (!s || !s.available) return "UNAVAILABLE";
  const state = s.state ? ` · ${s.state}` : "";
  return `${s.bias}${state}${s.synthetic ? " · synthetic" : ""}`;
}
