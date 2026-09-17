/**
 * Journal client (Phase 15). The journal holds the user's private manual records; nothing is placed or authorized.
 * The client refuses entries whose status, result, R or process class contradict the recorded trade, so a corrupted
 * or inconsistent record is never shown as valid. Nothing is kept in browser storage.
 */
import {
  EXIT_REASONS,
  EXTREME_SOURCES,
  JOURNAL_ENTRY_KINDS,
  JOURNAL_STATUSES,
  PROCESS_CLASSIFICATIONS,
  RULE_VIOLATIONS,
  SNAPSHOT_INTEGRITIES,
  SNAPSHOT_TIMINGS,
  TRADE_RESULTS,
  type CreateJournalEntryRequest,
  type Direction,
  type ExitReason,
  type JournalEntry,
  type JournalEntryKind,
  type JournalListResponse,
  type RecordOutcomeRequest,
  type RuleViolation,
} from "@fmcc/shared-types";

import { API_BASE_URL } from "./api";

export type JournalListState = { status: "READY"; list: JournalListResponse } | { status: "UNAVAILABLE"; reason: string };
export type JournalEntryState = { status: "READY"; entry: JournalEntry } | { status: "UNAVAILABLE"; reason: string };
export type JournalWrite = { ok: true; entry: JournalEntry | null } | { ok: false; error: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const allMembers = <T extends string>(values: readonly T[], v: unknown): boolean =>
  Array.isArray(v) && v.every((x) => member(values, x));

const NON_TRADE_RESULT: Record<string, string> = { NO_TRADE: "NO_TRADE", MISSED_ENTRY: "MISSED_ENTRY" };

export function rMultiple(direction: Direction, entry: number, stop: number | null, exit: number): number | null {
  if (stop === null || entry === stop) return null;
  const s = direction === "BULLISH" ? 1 : -1;
  return (s * (exit - entry)) / Math.abs(entry - stop);
}

export function journalEntryError(e: unknown): string | null {
  if (!isObject(e) || typeof e.id !== "string") return "journal entry malformed";
  if (!member(JOURNAL_ENTRY_KINDS, e.kind) || !member(JOURNAL_STATUSES, e.status)) return "entry kind or status unknown";
  if (e.result !== null && !member(TRADE_RESULTS, e.result)) return "result unknown";
  if (e.classification !== null && !member(PROCESS_CLASSIFICATIONS, e.classification)) return "process class unknown";
  if (!allMembers(RULE_VIOLATIONS, e.detectedViolations)) return "violation unknown";
  const snap = e.snapshot;
  if (!isObject(snap) || !member(SNAPSHOT_INTEGRITIES, snap.integrity) || !member(SNAPSHOT_TIMINGS, snap.timing)) {
    return "snapshot invalid";
  }
  if (typeof snap.hash !== "string" || !/^[0-9a-f]{64}$/.test(snap.hash)) return "snapshot hash invalid";
  if (!isObject(e.summary)) return "snapshot summary missing";
  if (!Array.isArray(e.outcomeRevisions)) return "outcome revisions missing";
  const revisions = e.outcomeRevisions as unknown[];
  const latest = revisions.length ? revisions[revisions.length - 1] : null;
  if ((e.outcome ?? null) === null ? latest !== null : JSON.stringify(e.outcome) !== JSON.stringify(latest)) {
    return "latest outcome does not match its revisions";
  }
  if (e.kind !== "TRADE") {
    if (e.trade !== null || revisions.length > 0) return "a non-trade entry with trade data";
    if (e.status !== "CLOSED" || e.result !== NON_TRADE_RESULT[e.kind]) return "non-trade entry result inconsistent";
    return null;
  }
  const t = e.trade;
  if (!isObject(t) || !member(["BULLISH", "BEARISH"] as const, t.direction) || typeof t.entry !== "number") {
    return "trade details missing";
  }
  if ((e.status === "CLOSED") !== (latest !== null)) return "status does not match the outcomes";
  for (const [i, o] of revisions.entries()) {
    if (!isObject(o) || o.revision !== i + 1) return "outcome revisions out of order";
    if (!member(EXIT_REASONS, o.exitReason) || !member(TRADE_RESULTS, o.result) || !member(EXTREME_SOURCES, o.extremeSource)) {
      return "outcome enum unknown";
    }
    if (!member(PROCESS_CLASSIFICATIONS, o.classification) || !allMembers(RULE_VIOLATIONS, o.violations)) return "outcome class invalid";
    const violations = o.violations as string[];
    const clean = o.classification === "VALID_WIN" || o.classification === "VALID_LOSS";
    if (clean !== (violations.length === 0)) return "process class does not match the violations";
    if (!(e.detectedViolations as string[]).every((v) => violations.includes(v))) return "detected violations missing from the outcome";
    if (typeof o.exitPrice !== "number") return "exit price missing";
    const r = rMultiple(t.direction, t.entry, typeof t.stop === "number" ? t.stop : null, o.exitPrice);
    if ((r === null) !== (o.rMultiple === null) || (r !== null && Math.abs(r - Number(o.rMultiple)) > 0.01)) {
      return "R does not match the recorded trade";
    }
  }
  if (latest !== null && isObject(latest)) {
    if (e.result !== latest.result || e.classification !== latest.classification || e.rMultiple !== latest.rMultiple) {
      return "entry result does not match the latest outcome";
    }
  } else if (e.result !== null || e.classification !== null) {
    return "open trade with a result";
  }
  return null;
}

export function reconcileJournalEntry(payload: unknown): JournalEntryState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Journal API unreachable" };
  const err = journalEntryError(payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected journal entry: ${err}` } : { status: "READY", entry: payload as JournalEntry };
}

export function reconcileJournalList(payload: unknown): JournalListState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Journal API unreachable" };
  if (!isObject(payload) || !Array.isArray(payload.entries) || !isObject(payload.store)) {
    return { status: "UNAVAILABLE", reason: "Rejected journal list: malformed" };
  }
  if (typeof payload.store.available !== "boolean") return { status: "UNAVAILABLE", reason: "Rejected journal list: store info missing" };
  if (!payload.store.available && payload.entries.length > 0) {
    return { status: "UNAVAILABLE", reason: "Rejected journal list: entries from an unavailable store" };
  }
  for (const row of payload.entries) {
    if (!isObject(row) || !member(JOURNAL_ENTRY_KINDS, row.kind) || !member(SNAPSHOT_INTEGRITIES, row.integrity)) {
      return { status: "UNAVAILABLE", reason: "Rejected journal list: row invalid" };
    }
    if (row.result !== null && !member(TRADE_RESULTS, row.result)) return { status: "UNAVAILABLE", reason: "Rejected journal list: result unknown" };
  }
  return { status: "READY", list: payload as unknown as JournalListResponse };
}

// --- form -> request ------------------------------------------------------------------------------------------

export type EntryForm = {
  symbol: string;
  kind: JournalEntryKind;
  direction: Direction;
  entry: string;
  stop: string;
  targets: string;
  volume: string;
  riskPct: string;
  openedAt: string; // datetime-local, the viewer's local time
  notes: string;
};

const num = (s: string): number | null => {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : Number.NaN;
};

/** Builds the request or returns the first problem. Local datetime → UTC ISO. */
export function entryRequest(f: EntryForm): { body: CreateJournalEntryRequest } | { error: string } {
  const base = { symbol: f.symbol, kind: f.kind, notes: f.notes.trim() };
  if (f.kind !== "TRADE") return { body: { ...base, trade: null } };
  const entry = num(f.entry);
  if (entry === null || Number.isNaN(entry) || entry <= 0) return { error: "Entry price is required" };
  const stop = num(f.stop);
  if (Number.isNaN(stop) || (stop !== null && stop <= 0)) return { error: "Stop must be a price or empty (no stop)" };
  const targets = f.targets.split(",").map((x) => x.trim()).filter(Boolean).map(Number);
  if (targets.some((x) => !Number.isFinite(x) || x <= 0) || targets.length > 3) return { error: "Targets: up to 3 prices, comma separated" };
  const volume = num(f.volume);
  const riskPct = num(f.riskPct);
  if (Number.isNaN(volume) || Number.isNaN(riskPct)) return { error: "Volume and risk % must be numbers" };
  const opened = new Date(f.openedAt);
  if (Number.isNaN(opened.getTime())) return { error: "Opened time is required" };
  return {
    body: {
      ...base,
      trade: { direction: f.direction, entry, stop, targets, volume, riskPct, openedAt: opened.toISOString() },
    },
  };
}

export type OutcomeForm = {
  exitPrice: string;
  exitedAt: string;
  exitReason: ExitReason;
  mfePrice: string;
  maePrice: string;
  reportedViolations: RuleViolation[];
  notes: string;
};

export function outcomeRequest(f: OutcomeForm): { body: RecordOutcomeRequest } | { error: string } {
  const exitPrice = num(f.exitPrice);
  if (exitPrice === null || Number.isNaN(exitPrice) || exitPrice <= 0) return { error: "Exit price is required" };
  const mfe = num(f.mfePrice);
  const mae = num(f.maePrice);
  if (Number.isNaN(mfe) || Number.isNaN(mae)) return { error: "MFE / MAE must be prices or empty" };
  const exited = new Date(f.exitedAt);
  if (Number.isNaN(exited.getTime())) return { error: "Exit time is required" };
  return {
    body: {
      exitPrice,
      exitedAt: exited.toISOString(),
      exitReason: f.exitReason,
      mfePrice: mfe,
      maePrice: mae,
      reportedViolations: [...new Set(f.reportedViolations)],
      notes: f.notes.trim(),
    },
  };
}

// --- API ------------------------------------------------------------------------------------------------------

async function detailOf(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as unknown;
    if (isObject(body) && typeof body.detail === "string") return body.detail;
    if (isObject(body) && Array.isArray(body.detail)) {
      return body.detail.map((d) => (isObject(d) ? `${(d.loc as unknown[] | undefined)?.slice(1).join(".") ?? ""}: ${String(d.msg)}` : "")).join("; ");
    }
  } catch {
    /* fall through */
  }
  return `HTTP ${res.status}`;
}

async function send(path: string, init: RequestInit, fetcher: typeof fetch): Promise<JournalWrite> {
  try {
    const res = await fetcher(`${API_BASE_URL}${path}`, { cache: "no-store", ...init });
    if (res.status === 204) return { ok: true, entry: null };
    if (res.status !== 201) return { ok: false, error: await detailOf(res) };
    const state = reconcileJournalEntry((await res.json()) as unknown);
    return state.status === "READY" ? { ok: true, entry: state.entry } : { ok: false, error: state.reason };
  } catch {
    return { ok: false, error: "Journal API unreachable" };
  }
}

export async function loadJournal(
  params: { symbol?: string; kind?: string; cursor?: string | null } = {},
  fetcher: typeof fetch = fetch,
): Promise<JournalListState> {
  const q = new URLSearchParams();
  if (params.symbol) q.set("symbol", params.symbol);
  if (params.kind) q.set("kind", params.kind);
  if (params.cursor) q.set("cursor", params.cursor);
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/journal/entries${q.size ? `?${q}` : ""}`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcileJournalList((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Journal API unreachable" };
  }
}

export async function loadJournalEntry(id: string, fetcher: typeof fetch = fetch): Promise<JournalEntryState> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/journal/entries/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: await detailOf(res) };
    return reconcileJournalEntry((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Journal API unreachable" };
  }
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const createJournalEntry = (body: CreateJournalEntryRequest, fetcher: typeof fetch = fetch) =>
  send("/api/v1/journal/entries", json(body), fetcher);

export const recordJournalOutcome = (id: string, body: RecordOutcomeRequest, fetcher: typeof fetch = fetch) =>
  send(`/api/v1/journal/entries/${encodeURIComponent(id)}/outcomes`, json(body), fetcher);

export const deleteJournalEntry = (id: string, fetcher: typeof fetch = fetch) =>
  send(`/api/v1/journal/entries/${encodeURIComponent(id)}`, { method: "DELETE" }, fetcher);
