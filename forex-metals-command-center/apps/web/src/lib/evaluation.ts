/**
 * Verdict evaluation payload validation (client side). The evaluation is never a trade signal: the client rejects
 * anything that claims authority, confidence above the cap, or a plan that does not match its outcome.
 */
import {
  BLOCKERS,
  DECISION_CONFIDENCES,
  DIRECTIONS,
  ENTRY_WARNINGS,
  EVALUATION_OUTCOMES,
  SCORE_COMPONENT_STATUSES,
  SCORE_FACTORS,
  SETUP_GRADES,
  SETUP_STATES,
  type DecisionEvaluation,
  type SetupGrade,
} from "@fmcc/shared-types";

import { macroError } from "./macro";
import { newsError } from "./news";
import { riskError } from "./risk";

export type EvaluationLoadState =
  | { status: "READY"; evaluation: DecisionEvaluation }
  | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const allMembers = <T extends string>(values: readonly T[], v: unknown): boolean =>
  Array.isArray(v) && v.every((x) => member(values, x));

/** Highest confidence allowed while verdict authority is FAIL_SAFE_ONLY. */
export const CONFIDENCE_CAP = "MODERATE";
const GRADE_FLOORS: ReadonlyArray<[SetupGrade, number]> = [["A+", 90], ["A", 80], ["B", 70], ["C", 60]];

export function gradeFor(score: number): SetupGrade {
  return GRADE_FLOORS.find(([, floor]) => score >= floor)?.[0] ?? "D";
}

export function reconcileEvaluation(symbol: string, payload: unknown): EvaluationLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Evaluation API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed evaluation payload" };
  if (payload.symbol !== symbol.toUpperCase()) return { status: "UNAVAILABLE", reason: "Evaluation symbol mismatch" };
  const reject = (err: string): EvaluationLoadState => ({ status: "UNAVAILABLE", reason: `Rejected evaluation: ${err}` });
  if (payload.authority !== "NOT_AUTHORIZED") return reject("authority claimed before the required gates exist");
  if (!member(EVALUATION_OUTCOMES, payload.outcome) || !member(DECISION_CONFIDENCES, payload.confidence)) {
    return reject("outcome or confidence unknown");
  }
  const levels = DECISION_CONFIDENCES as readonly string[];
  if (levels.indexOf(payload.confidence) > levels.indexOf(CONFIDENCE_CAP)) return reject("confidence above the cap");
  if (payload.direction !== null && !member(DIRECTIONS, payload.direction)) return reject("direction unknown");
  if (payload.setupState !== null && !member(SETUP_STATES, payload.setupState)) return reject("setup state unknown");
  const score = payload.score;
  if (score !== null && !(typeof score === "number" && score >= 0 && score <= 100)) return reject("score out of range");
  if ((score === null) !== (payload.grade === null)) return reject("grade without score");
  if (typeof score === "number" && (!member(SETUP_GRADES, payload.grade) || gradeFor(score) !== payload.grade)) {
    return reject("grade does not match the score");
  }
  if (!allMembers(BLOCKERS, payload.hardBlockers) || !allMembers(BLOCKERS, payload.missingGates)) return reject("blockers unknown");
  const gates = payload.missingGates as string[];
  const news = payload.news ?? null;
  if (news === null) {
    if (!gates.includes("NEWS_GATE_MISSING")) return reject("news gate claimed without a news assessment");
  } else {
    const newsErr = newsError(symbol, news);
    if (newsErr) return reject(newsErr);
    if (gates.includes("NEWS_GATE_MISSING")) return reject("news assessment present but the news gate is listed as missing");
  }
  const risk = payload.risk ?? null;
  if (risk === null) {
    if (!gates.includes("RISK_GATE_MISSING")) return reject("risk gate claimed without a risk assessment");
  } else {
    const err = riskError(symbol, risk);
    if (err) return reject(err);
    if (gates.includes("RISK_GATE_MISSING")) return reject("risk assessment present but the risk gate is listed as missing");
  }
  const macro = payload.macro ?? null;
  if (macro !== null) {
    const macroErr = macroError(symbol, macro);
    if (macroErr) return reject(macroErr);
  }
  const macroScored = isObject(macro) && macro.available === true && macro.isSynthetic === false && macro.direction === payload.direction;
  const hasPlan = isObject(payload.plan);
  const riskLocked = isObject(risk) && risk.status === "LOCKED";
  const blackout = isObject(news) && news.state === "BLACKOUT";
  const hard = payload.hardBlockers as string[];
  if (hasPlan && payload.setupState !== "BLOCKED") return reject("plan does not match the outcome");
  // A confirmed plan is NO_TRADE under a risk lock or strict news blackout (final veto), CONFIRMED_AWAITING_AUTHORITY
  // when every gate is clear, otherwise CONFIRMED_PENDING_GATES. Neither confirmed outcome is an authorization.
  const planOutcome =
    riskLocked || blackout ? "NO_TRADE" : gates.length === 0 && hard.length === 0 ? "CONFIRMED_AWAITING_AUTHORITY" : "CONFIRMED_PENDING_GATES";
  const confirmedOutcome = payload.outcome === "CONFIRMED_PENDING_GATES" || payload.outcome === "CONFIRMED_AWAITING_AUTHORITY";
  if (hasPlan ? payload.outcome !== planOutcome : confirmedOutcome) {
    return reject("plan does not match the outcome");
  }
  if (!allMembers(ENTRY_WARNINGS, payload.warnings)) return reject("warning unknown");
  if (!Array.isArray(payload.components)) return reject("components missing");
  for (const c of payload.components) {
    if (!isObject(c) || !member(SCORE_FACTORS, c.factor) || !member(SCORE_COMPONENT_STATUSES, c.status)) return reject("component invalid");
    if (typeof c.points !== "number" || typeof c.maxPoints !== "number" || c.points < 0 || c.points > c.maxPoints) {
      return reject("component points out of range");
    }
    if (c.factor === "MACRO" && c.status === "EVALUATED" && !macroScored) return reject("macro scored without trusted macro data");
  }
  return { status: "READY", evaluation: payload as unknown as DecisionEvaluation };
}

export function scoreLabel(score: number | null, grade: string | null): string | null {
  return score === null ? null : `${score}${grade ? ` (${grade})` : ""}`;
}
