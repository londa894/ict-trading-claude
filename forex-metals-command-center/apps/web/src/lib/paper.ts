/**
 * Paper trading client (Phase 16). Paper sims are broker-free simulations; nothing is sent anywhere. The client refuses
 * a sim that claims any authority other than SIMULATION_ONLY or whose status, fills, events and result disagree.
 */
import {
  PAPER_ENTRY_TYPES,
  PAPER_EVENT_TYPES,
  PAPER_SOURCES,
  PAPER_STATUSES,
  PROCESS_CLASSIFICATIONS,
  SNAPSHOT_INTEGRITIES,
  TRADE_RESULTS,
  type CreatePaperSimRequest,
  type Direction,
  type PaperEntryType,
  type PaperListResponse,
  type PaperSim,
  type PaperSource,
} from "@fmcc/shared-types";

import { API_BASE_URL } from "./api";

export type PaperListState = { status: "READY"; list: PaperListResponse } | { status: "UNAVAILABLE"; reason: string };
export type PaperSimState = { status: "READY"; sim: PaperSim } | { status: "UNAVAILABLE"; reason: string };
export type PaperWrite = { ok: true; sim: PaperSim | null } | { ok: false; error: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const TERMINAL_EVENT: Record<string, readonly string[]> = {
  CLOSED: ["STOP_HIT", "TARGET_HIT", "CLOSED_MANUALLY"],
  EXPIRED: ["EXPIRED"],
  CANCELLED: ["CANCELLED"],
};

export function paperSimError(p: unknown): string | null {
  if (!isObject(p) || typeof p.id !== "string") return "paper sim malformed";
  if (p.authority !== "SIMULATION_ONLY") return "a paper sim claimed authority";
  if (!member(PAPER_STATUSES, p.status) || !member(PAPER_SOURCES, p.source) || !member(PAPER_ENTRY_TYPES, p.entryType)) {
    return "paper enum unknown";
  }
  if (!member(["BULLISH", "BEARISH"] as const, p.direction) || !num(p.referencePrice) || !num(p.stop) || !num(p.target)) {
    return "paper prices missing";
  }
  const s = p.direction === "BULLISH" ? 1 : -1;
  if (s * (p.referencePrice - p.stop) <= 0 || s * (p.target - p.referencePrice) <= 0) return "stop or target on the wrong side";
  if (!member(SNAPSHOT_INTEGRITIES, p.integrity)) return "integrity unknown";
  if (!Array.isArray(p.events) || p.events.length === 0) return "events missing";
  const events = p.events as unknown[];
  for (const [i, e] of events.entries()) {
    if (!isObject(e) || e.seq !== i + 1 || !member(PAPER_EVENT_TYPES, e.type)) return "events out of order";
  }
  if ((events[0] as { type: string }).type !== "CREATED") return "first event is not CREATED";
  const types = events.map((e) => (e as { type: string }).type);
  const last = types[types.length - 1]!;
  const filled = types.includes("FILLED");
  switch (p.status) {
    case "PENDING":
      if (filled || p.fillPrice !== null || p.result !== null) return "pending sim with a fill";
      break;
    case "OPEN":
      if (!filled || !num(p.fillPrice) || p.exitPrice !== null || p.result !== null) return "open sim inconsistent";
      break;
    case "CLOSED":
      if (!filled || !num(p.fillPrice) || !num(p.exitPrice) || !isObject(p.result)) return "closed sim without fill, exit or result";
      break;
    default:
      if (filled || p.exitPrice !== null || p.result !== null) return "expired/cancelled sim with a fill";
  }
  const terminal = TERMINAL_EVENT[p.status];
  if (terminal ? !terminal.includes(last) : types.some((t) => Object.values(TERMINAL_EVENT).flat().includes(t))) {
    return "events do not match the status";
  }
  if (isObject(p.result)) {
    const r = p.result;
    if (!member(TRADE_RESULTS, r.result) || !member(PROCESS_CLASSIFICATIONS, r.classification) || !Array.isArray(r.violations)) {
      return "result invalid";
    }
    const clean = r.classification === "VALID_WIN" || r.classification === "VALID_LOSS";
    if (clean !== (r.violations.length === 0)) return "process class does not match the violations";
    if (num(r.rMultiple) && num(r.netRMultiple) && r.netRMultiple > r.rMultiple + 1e-9) return "net R above gross R";
  }
  return null;
}

export function reconcilePaperSim(payload: unknown): PaperSimState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Paper API unreachable" };
  const err = paperSimError(payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected paper sim: ${err}` } : { status: "READY", sim: payload as PaperSim };
}

export function reconcilePaperList(payload: unknown): PaperListState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Paper API unreachable" };
  if (!isObject(payload) || !Array.isArray(payload.sims) || !isObject(payload.store) || typeof payload.store.available !== "boolean") {
    return { status: "UNAVAILABLE", reason: "Rejected paper list: malformed" };
  }
  if (!payload.store.available && payload.sims.length > 0) return { status: "UNAVAILABLE", reason: "Rejected paper list: sims from an unavailable store" };
  for (const row of payload.sims) {
    if (!isObject(row) || !member(PAPER_STATUSES, row.status) || !member(SNAPSHOT_INTEGRITIES, row.integrity)) {
      return { status: "UNAVAILABLE", reason: "Rejected paper list: row invalid" };
    }
  }
  return { status: "READY", list: payload as unknown as PaperListResponse };
}

export type PaperForm = {
  symbol: string;
  source: PaperSource;
  direction: Direction;
  entryType: PaperEntryType;
  limitPrice: string;
  stop: string;
  target: string;
  notes: string;
};

const parse = (s: string): number | null => (s.trim() === "" ? null : Number(s));

export function paperRequest(f: PaperForm): { body: CreatePaperSimRequest } | { error: string } {
  if (f.source === "ENGINE_PLAN") {
    return { body: { symbol: f.symbol, source: "ENGINE_PLAN", direction: null, entryType: "LIMIT", limitPrice: null, stop: null, target: null, notes: f.notes.trim() } };
  }
  const stop = parse(f.stop);
  const target = parse(f.target);
  if (stop === null || target === null || !Number.isFinite(stop) || !Number.isFinite(target) || stop <= 0 || target <= 0) {
    return { error: "Stop and target prices are required" };
  }
  const limit = f.entryType === "LIMIT" ? parse(f.limitPrice) : null;
  if (f.entryType === "LIMIT" && (limit === null || !Number.isFinite(limit) || limit <= 0)) return { error: "Limit price is required" };
  return {
    body: { symbol: f.symbol, source: "MANUAL", direction: f.direction, entryType: f.entryType, limitPrice: limit, stop, target, notes: f.notes.trim() },
  };
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

async function send(path: string, init: RequestInit, fetcher: typeof fetch): Promise<PaperWrite> {
  try {
    const res = await fetcher(`${API_BASE_URL}${path}`, { cache: "no-store", ...init });
    if (res.status === 204) return { ok: true, sim: null };
    if (!res.ok) return { ok: false, error: await detailOf(res) };
    const state = reconcilePaperSim((await res.json()) as unknown);
    return state.status === "READY" ? { ok: true, sim: state.sim } : { ok: false, error: state.reason };
  } catch {
    return { ok: false, error: "Paper API unreachable" };
  }
}

export async function loadPaperSims(fetcher: typeof fetch = fetch): Promise<PaperListState> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/paper/sims`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcilePaperList((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Paper API unreachable" };
  }
}

export async function loadPaperSim(id: string, fetcher: typeof fetch = fetch): Promise<PaperSimState> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/paper/sims/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcilePaperSim((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Paper API unreachable" };
  }
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const createPaperSim = (body: CreatePaperSimRequest, fetcher: typeof fetch = fetch) => send("/api/v1/paper/sims", post(body), fetcher);
export const closePaperSim = (id: string, fetcher: typeof fetch = fetch) => send(`/api/v1/paper/sims/${encodeURIComponent(id)}/close`, post(), fetcher);
export const cancelPaperSim = (id: string, fetcher: typeof fetch = fetch) => send(`/api/v1/paper/sims/${encodeURIComponent(id)}/cancel`, post(), fetcher);
export const deletePaperSim = (id: string, fetcher: typeof fetch = fetch) =>
  send(`/api/v1/paper/sims/${encodeURIComponent(id)}`, { method: "DELETE" }, fetcher);
