/**
 * Replay client (Phase 19). Replays are education only and must never show the future. The client independently
 * refuses a state whose candles extend past the cursor, a blind session that leaks the instrument or engine view, a
 * quiz that shows the engine before the first answer, or a score that does not match the graded history.
 */
import {
  CHART_TIMEFRAMES,
  REPLAY_MODES,
  type ChartTimeframe,
  type CreateReplayRequest,
  type ReplayMode,
  type ReplayReveal,
  type ReplayState,
} from "@fmcc/shared-types";

import { API_BASE_URL } from "./api";

export type ReplayLoad = { status: "READY"; state: ReplayState } | { status: "UNAVAILABLE"; reason: string };
export type RevealLoad = { ok: true; reveal: ReplayReveal } | { ok: false; error: string };

const TF_MINUTES: Record<string, number> = { M5: 5, M15: 15, H1: 60, H4: 240, D1: 1440 };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function replayStateError(s: unknown): string | null {
  if (!isObject(s) || typeof s.id !== "string") return "replay malformed";
  if (s.authority !== "EDUCATION_ONLY") return "a replay claimed authority";
  if (!(REPLAY_MODES as readonly string[]).includes(String(s.mode))) return "replay mode unknown";
  if (!(CHART_TIMEFRAMES as readonly string[]).includes(String(s.timeframe))) return "timeframe unknown";
  if (typeof s.cursor !== "string" || !Array.isArray(s.candles)) return "cursor or candles missing";
  const cursor = Date.parse(s.cursor);
  const minutes = TF_MINUTES[String(s.timeframe)] ?? 0;
  for (const c of s.candles) {
    if (!isObject(c) || typeof c.time !== "string" || c.isClosed !== true) return "open or malformed candle";
    if (Date.parse(c.time) + minutes * 60_000 > cursor) return "a candle extends past the cursor (future shown)";
  }
  if (s.masked === true) {
    if (s.mode !== "BLIND") return "masking on a non-blind session";
    if (s.label !== "Hidden instrument" || s.analysis !== null || s.guidance !== null) return "a blind session leaked the instrument or engine view";
  }
  const history = Array.isArray(s.history) ? (s.history as unknown[]) : null;
  if (history === null) return "quiz history missing";
  if (s.mode === "QUIZ") {
    if (history.length === 0 && s.analysis !== null) return "the engine view was shown before the first answer";
    const score = s.score;
    if (!isObject(score)) return "quiz score missing";
    const n = score as Record<string, number>;
    if (n.asked !== history.length || n.correct! + n.incorrect! + n.void! !== n.asked) return "score does not match the quiz history";
    const last = history.length ? history[history.length - 1] : null;
    if (JSON.stringify(last) !== JSON.stringify(s.lastResult ?? null)) return "last result does not match the history";
  } else if (history.length > 0 || s.quiz !== null) {
    return "quiz data on a non-quiz session";
  }
  return null;
}

export function reconcileReplay(payload: unknown): ReplayLoad {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Replay API unreachable" };
  const err = replayStateError(payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected replay: ${err}` } : { status: "READY", state: payload as ReplayState };
}

export type ReplayForm = { symbol: string; mode: ReplayMode; timeframe: ChartTimeframe; start: string };

export function replayRequest(f: ReplayForm, now = new Date()): { body: CreateReplayRequest } | { error: string } {
  const start = new Date(f.start);
  if (Number.isNaN(start.getTime())) return { error: "Start time is required" };
  if (start >= now) return { error: "Start must be in the past" };
  return { body: { symbol: f.symbol, mode: f.mode, timeframe: f.timeframe, start: start.toISOString() } };
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as unknown;
    if (isObject(body) && typeof body.detail === "string") return body.detail;
    if (isObject(body) && Array.isArray(body.detail)) return body.detail.map((d) => (isObject(d) ? String(d.msg) : "")).join("; ");
  } catch {
    /* fall through */
  }
  return `HTTP ${res.status}`;
}

async function call(path: string, init: RequestInit, fetcher: typeof fetch): Promise<ReplayLoad> {
  try {
    const res = await fetcher(`${API_BASE_URL}${path}`, { cache: "no-store", ...init });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcileReplay((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Replay API unreachable" };
  }
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const createReplay = (body: CreateReplayRequest, fetcher: typeof fetch = fetch) => call("/api/v1/replay/sessions", post(body), fetcher);
export const stepReplay = (id: string, bars: number, fetcher: typeof fetch = fetch) =>
  call(`/api/v1/replay/sessions/${encodeURIComponent(id)}/step`, post({ bars }), fetcher);
export const answerReplay = (id: string, questionId: string, answer: string, fetcher: typeof fetch = fetch) =>
  call(`/api/v1/replay/sessions/${encodeURIComponent(id)}/answer`, post({ questionId, answer }), fetcher);
export const loadReplay = (id: string, fetcher: typeof fetch = fetch) =>
  call(`/api/v1/replay/sessions/${encodeURIComponent(id)}`, { method: "GET" }, fetcher);

export async function endReplay(id: string, fetcher: typeof fetch = fetch): Promise<RevealLoad> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/replay/sessions/${encodeURIComponent(id)}/end`, { method: "POST", cache: "no-store" });
    if (!res.ok) return { ok: false, error: await detailOf(res) };
    return { ok: true, reveal: (await res.json()) as ReplayReveal };
  } catch {
    return { ok: false, error: "Replay API unreachable" };
  }
}
