/**
 * AI assistant client. The assistant explains the deterministic engine state; the client refuses any answer that
 * claims authority, belongs to another market, publishes an external answer that did not pass the guard, or reads
 * like a trade instruction.
 */
import {
  ASSISTANT_INTENTS,
  ASSISTANT_PROVIDERS,
  EDUCATION_LEVELS,
  GUARD_STATUSES,
  TOOL_STATUSES,
  VERDICTS,
  type AssistantAnswer,
  type AssistantCapabilities,
  type EducationLevel,
} from "@fmcc/shared-types";

export type AnswerLoadState = { status: "READY"; answer: AssistantAnswer } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);

export const TRADE_INSTRUCTION =
  /\b(you should|i recommend|we recommend)\s+(buy|sell|buying|selling|enter|go long|go short)\b|\b(go long|go short|place (an? |the )?(order|trade)|buy now|sell now)\b/i;

export function reconcileAnswer(symbol: string, payload: unknown): AnswerLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Assistant API unreachable" };
  if (!isObject(payload)) return { status: "UNAVAILABLE", reason: "Malformed assistant answer" };
  const reject = (why: string): AnswerLoadState => ({ status: "UNAVAILABLE", reason: `Rejected assistant answer: ${why}` });
  const sym = symbol.toUpperCase();
  if (payload.authority !== "NOT_AUTHORIZED") return reject("authority claimed");
  if (payload.symbol !== sym) return reject("answer for another market");
  if (!member(ASSISTANT_INTENTS, payload.intent) || !member(EDUCATION_LEVELS, payload.level) || !member(ASSISTANT_PROVIDERS, payload.provider)) {
    return reject("enum unknown");
  }
  if (typeof payload.answer !== "string" || !Array.isArray(payload.facts) || !Array.isArray(payload.unknowns) || !Array.isArray(payload.tools)) {
    return reject("fields missing");
  }
  const guard = payload.guard;
  if (!isObject(guard) || !member(GUARD_STATUSES, guard.status) || !Array.isArray(guard.violations)) return reject("guard report invalid");
  if (payload.provider === "ANTHROPIC" && guard.status !== "PASSED") return reject("external answer published without passing the guard");
  if (guard.status === "FALLBACK" && payload.provider !== "DETERMINISTIC") return reject("fallback answer not deterministic");
  const decision = payload.decision;
  if (isObject(decision) && (decision.symbol !== sym || !member(VERDICTS, decision.verdict))) return reject("decision reference invalid");
  for (const t of payload.tools) {
    if (!isObject(t) || !member(TOOL_STATUSES, t.status)) return reject("tool record invalid");
    if (t.status === "OK" && t.symbol !== null && t.symbol !== sym) return reject("tool used another market's context");
  }
  if (TRADE_INSTRUCTION.test(payload.answer)) return reject("reads like a trade instruction");
  const proposal = payload.proposal;
  if (proposal !== null && (!isObject(proposal) || proposal.symbol !== sym)) return reject("proposal for another market");
  return { status: "READY", answer: payload as unknown as AssistantAnswer };
}

export function reconcileCapabilities(payload: unknown): AssistantCapabilities | null {
  if (!isObject(payload) || payload.authority !== "NOT_AUTHORIZED" || !member(ASSISTANT_PROVIDERS, payload.provider)) return null;
  if (!Array.isArray(payload.commands) || !Array.isArray(payload.tools)) return null;
  return payload as unknown as AssistantCapabilities;
}

export const ASK_TIMEOUT_MS = 60_000;

export async function askAssistant(
  baseUrl: string,
  symbol: string,
  question: string,
  level: EducationLevel,
  fetcher: typeof fetch = fetch,
): Promise<AnswerLoadState> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ASK_TIMEOUT_MS);
  try {
    const res = await fetcher(`${baseUrl}/api/v1/assistant/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: symbol.toUpperCase(), question, level }),
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) return { status: "UNAVAILABLE", reason: `Assistant rejected the question (HTTP ${res.status})` };
    return reconcileAnswer(symbol, (await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Assistant API unreachable" };
  } finally {
    clearTimeout(timer);
  }
}
