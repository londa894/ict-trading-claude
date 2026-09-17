/**
 * Backtest client (Phase 18). Backtests are research replays of the live setup engine over closed history. The client
 * refuses a run that claims authority, whose status and result disagree, whose funnel does not account for every
 * confirmed plan, whose sample labels contradict the counts, or which is missing its disclosures.
 */
import {
  BACKTEST_STATUSES,
  type BacktestListResponse,
  type BacktestRequest,
  type BacktestRun,
  type EntryMode,
} from "@fmcc/shared-types";

import { sampleLabel } from "./analytics";
import { API_BASE_URL } from "./api";

export type BacktestRunState = { status: "READY"; run: BacktestRun } | { status: "UNAVAILABLE"; reason: string };
export type BacktestListState = { status: "READY"; list: BacktestListResponse } | { status: "UNAVAILABLE"; reason: string };
export type BacktestWrite = { ok: true; run: BacktestRun | null } | { ok: false; error: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function backtestRunError(r: unknown): string | null {
  if (!isObject(r) || typeof r.id !== "string") return "backtest malformed";
  if (r.authority !== "RESEARCH_ONLY") return "a backtest claimed authority";
  if (!(BACKTEST_STATUSES as readonly string[]).includes(String(r.status))) return "backtest status unknown";
  if (!isObject(r.progress) || typeof r.progress.pct !== "number" || r.progress.pct < 0 || r.progress.pct > 100) return "progress invalid";
  const result = r.result;
  if (r.status !== "COMPLETED" && result !== null) return "a result on an unfinished run";
  if (r.status === "COMPLETED" && result === null && r.integrity !== "TAMPERED" && r.integrity !== "UNREADABLE") return "completed run without a result";
  if (result === null) return null;
  if (!isObject(result) || !Array.isArray(result.variants) || !Array.isArray(result.disclosures)) return "result malformed";
  if (!result.disclosures.some((d) => String(d).includes("NOT applied"))) return "disclosures missing";
  for (const v of result.variants) {
    if (!isObject(v) || !isObject(v.funnel) || !isObject(v.stats)) return "variant malformed";
    const f = v.funnel as { plansConfirmed: number; closed: number; expired: number; plansSkippedOverlap: number; openAtEnd: number };
    if (f.plansConfirmed !== f.closed + f.expired + f.plansSkippedOverlap + f.openAtEnd) return "funnel does not account for every plan";
    const stats = v.stats as { count: number; label: string };
    if (stats.count !== f.closed) return "statistics do not match the closed trades";
    if (stats.label !== sampleLabel(stats.count)) return "sample label does not match the count";
    const mc = v.monteCarlo;
    if (isObject(mc)) {
      const m = mc as { totalRP05: number; totalRP50: number; totalRP95: number; maxDrawdownRP50: number; maxDrawdownRP95: number };
      if (!(m.totalRP05 <= m.totalRP50 && m.totalRP50 <= m.totalRP95 && m.maxDrawdownRP50 <= m.maxDrawdownRP95)) return "Monte Carlo percentiles out of order";
    }
  }
  return null;
}

export function reconcileBacktestRun(payload: unknown): BacktestRunState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Backtest API unreachable" };
  const err = backtestRunError(payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected backtest: ${err}` } : { status: "READY", run: payload as BacktestRun };
}

export function reconcileBacktestList(payload: unknown): BacktestListState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Backtest API unreachable" };
  if (!isObject(payload) || !Array.isArray(payload.runs) || !isObject(payload.store) || typeof payload.store.available !== "boolean") {
    return { status: "UNAVAILABLE", reason: "Rejected backtest list: malformed" };
  }
  if (!payload.store.available && payload.runs.length > 0) return { status: "UNAVAILABLE", reason: "Rejected backtest list: runs from an unavailable store" };
  return { status: "READY", list: payload as unknown as BacktestListResponse };
}

export type BacktestForm = {
  symbol: string;
  start: string; // datetime-local (viewer's local time)
  end: string;
  modeA: EntryMode;
  compare: boolean;
  modeB: EntryMode;
  costMultiplierB: string;
  segments: string;
  outOfSampleFrom: string;
};

export function backtestRequest(f: BacktestForm): { body: BacktestRequest } | { error: string } {
  const start = new Date(f.start);
  const end = new Date(f.end);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { error: "Start and end are required" };
  if (end <= start) return { error: "End must be after start" };
  const segments = Number(f.segments || "1");
  if (!Number.isInteger(segments) || segments < 1 || segments > 6) return { error: "Segments: 1 to 6" };
  const variants: BacktestRequest["variants"] = [{ name: "A", entryMode: f.modeA, costMultiplier: 1 }];
  if (f.compare) {
    const mult = Number(f.costMultiplierB || "1");
    if (!Number.isFinite(mult) || mult < 0 || mult > 5) return { error: "Cost multiplier: 0 to 5" };
    variants.push({ name: "B", entryMode: f.modeB, costMultiplier: mult });
  }
  let oos: string | null = null;
  if (f.outOfSampleFrom) {
    const d = new Date(f.outOfSampleFrom);
    if (Number.isNaN(d.getTime()) || d <= start || d >= end) return { error: "Out-of-sample start must lie inside the range" };
    oos = d.toISOString();
  }
  return { body: { symbol: f.symbol, start: start.toISOString(), end: end.toISOString(), variants, segments, outOfSampleFrom: oos } };
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

export async function loadBacktests(fetcher: typeof fetch = fetch): Promise<BacktestListState> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/backtests`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcileBacktestList((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Backtest API unreachable" };
  }
}

export async function loadBacktest(id: string, fetcher: typeof fetch = fetch): Promise<BacktestRunState> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/backtests/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcileBacktestRun((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Backtest API unreachable" };
  }
}

async function send(path: string, init: RequestInit, fetcher: typeof fetch): Promise<BacktestWrite> {
  try {
    const res = await fetcher(`${API_BASE_URL}${path}`, { cache: "no-store", ...init });
    if (res.status === 204) return { ok: true, run: null };
    if (!res.ok) return { ok: false, error: await detailOf(res) };
    const state = reconcileBacktestRun((await res.json()) as unknown);
    return state.status === "READY" ? { ok: true, run: state.run } : { ok: false, error: state.reason };
  } catch {
    return { ok: false, error: "Backtest API unreachable" };
  }
}

export const startBacktest = (body: BacktestRequest, fetcher: typeof fetch = fetch) =>
  send("/api/v1/backtests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, fetcher);
export const cancelBacktest = (id: string, fetcher: typeof fetch = fetch) =>
  send(`/api/v1/backtests/${encodeURIComponent(id)}/cancel`, { method: "POST" }, fetcher);
export const deleteBacktest = (id: string, fetcher: typeof fetch = fetch) =>
  send(`/api/v1/backtests/${encodeURIComponent(id)}`, { method: "DELETE" }, fetcher);
