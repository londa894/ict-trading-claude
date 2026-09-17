/**
 * Alert feed and ALERT_ME_WHEN_READY client. Alerts describe engine state changes; they are never trade
 * instructions. The client rejects a feed that claims authority, carries a READY alert or a FIRED watch while the
 * backend reports FAIL_SAFE_ONLY, or puts LONG/SHORT wording into a non-READY alert.
 */
import {
  ALERT_CATEGORIES,
  ALERT_PRIORITIES,
  ALERT_TYPES,
  CONDITION_STATUSES,
  DIRECTIONS,
  READY_WATCH_STATES,
  type Alert,
  type AlertFeed,
  type AlertPriority,
  type Direction,
  type ReadyWatch,
  type VerdictAuthority,
} from "@fmcc/shared-types";

import type { EventEntry } from "@/components/EventLog";

export type AlertFeedLoadState = { status: "READY"; feed: AlertFeed } | { status: "UNAVAILABLE"; reason: string };
export type WatchesLoadState = { status: "READY"; watches: ReadyWatch[] } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): boolean => typeof v === "string" && !Number.isNaN(Date.parse(v));
const DIRECTIONAL_WORDS = /\b(LONG|SHORT|BUY|SELL)\b/;

function alertError(a: unknown, authority: VerdictAuthority | null): string | null {
  if (!isObject(a) || typeof a.id !== "string" || typeof a.seq !== "number" || typeof a.symbol !== "string") return "alert malformed";
  if (!member(ALERT_TYPES, a.type) || !member(ALERT_CATEGORIES, a.category) || !member(ALERT_PRIORITIES, a.priority)) {
    return `${a.id}: enum unknown`;
  }
  if (a.direction !== null && !member(DIRECTIONS, a.direction)) return `${a.id}: direction unknown`;
  if (typeof a.title !== "string" || typeof a.message !== "string" || !isIso(a.createdAt) || !isIso(a.occurredAt)) {
    return `${a.id}: fields invalid`;
  }
  if (a.type === "READY") {
    if (authority !== "FULL") return `${a.id}: READY alert without FULL verdict authority`;
  } else if (DIRECTIONAL_WORDS.test(`${a.title} ${a.message}`)) {
    return `${a.id}: directional wording in a non-READY alert`;
  }
  return null;
}

export function reconcileAlertFeed(payload: unknown, authority: VerdictAuthority | null): AlertFeedLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Alerts API unreachable" };
  if (!isObject(payload) || !Array.isArray(payload.alerts) || !isObject(payload.monitor) || typeof payload.nextCursor !== "number") {
    return { status: "UNAVAILABLE", reason: "Malformed alert feed" };
  }
  const reject = (why: string): AlertFeedLoadState => ({ status: "UNAVAILABLE", reason: `Rejected alert feed: ${why}` });
  if (payload.authority !== "NOT_AUTHORIZED") return reject("authority claimed");
  let previous = Number.POSITIVE_INFINITY;
  for (const a of payload.alerts as unknown[]) {
    const err = alertError(a, authority);
    if (err) return reject(err);
    const seq = (a as Alert).seq;
    if (seq >= previous) return reject("alerts not newest first");
    if (seq > payload.nextCursor) return reject("cursor behind the alerts");
    previous = seq;
  }
  return { status: "READY", feed: payload as unknown as AlertFeed };
}

export function reconcileWatches(payload: unknown, authority: VerdictAuthority | null): WatchesLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Ready watches unreachable" };
  if (!Array.isArray(payload)) return { status: "UNAVAILABLE", reason: "Malformed ready watches" };
  for (const w of payload) {
    if (!isObject(w) || typeof w.id !== "string" || typeof w.symbol !== "string" || !member(READY_WATCH_STATES, w.state)) {
      return { status: "UNAVAILABLE", reason: "Rejected ready watches: watch malformed" };
    }
    if (w.state === "FIRED" && authority !== "FULL") {
      return { status: "UNAVAILABLE", reason: "Rejected ready watches: FIRED without FULL verdict authority" };
    }
    const conditions = w.conditions;
    if (!Array.isArray(conditions) || !conditions.every((c) => isObject(c) && member(CONDITION_STATUSES, c.status))) {
      return { status: "UNAVAILABLE", reason: "Rejected ready watches: condition invalid" };
    }
  }
  return { status: "READY", watches: payload as ReadyWatch[] };
}

const PRIORITY_SEVERITY: Record<AlertPriority, EventEntry["severity"]> = {
  LOW: "INFO",
  MEDIUM: "INFO",
  HIGH: "WARNING",
  CRITICAL: "ERROR",
};

export function alertEvents(alerts: readonly Alert[]): EventEntry[] {
  // Feed is newest first; the event log wants arrival order for appendEvents.
  return [...alerts].reverse().map((a) => ({
    key: `alert:${a.id}`,
    at: a.createdAt,
    severity: PRIORITY_SEVERITY[a.priority],
    text: `[${a.priority} ${a.category}] ${a.title} · ${a.message}`,
  }));
}

export function mergeAlerts(existing: readonly Alert[], incoming: readonly Alert[], max = 300): Alert[] {
  const byId = new Map<string, Alert>();
  for (const a of [...incoming, ...existing]) if (!byId.has(a.id)) byId.set(a.id, a);
  return [...byId.values()].sort((x, y) => y.seq - x.seq).slice(0, max);
}

// --- dismissed alerts (per-viewer display preference) -----------------------------------------------------

export const DISMISSED_KEY = "fmcc.alerts.dismissed.v1";

export function readDismissed(storage: Pick<Storage, "getItem"> | null = safeStorage()): string[] {
  try {
    const raw = storage?.getItem(DISMISSED_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(-500) : [];
  } catch {
    return [];
  }
}

export function saveDismissed(ids: readonly string[], storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(DISMISSED_KEY, JSON.stringify(ids.slice(-500)));
  } catch {
    // storage blocked: dismissals last for this page view
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function watchRequestBody(symbol: string, direction: Direction | "ANY"): { symbol: string; direction: Direction | null } {
  return { symbol: symbol.toUpperCase(), direction: direction === "ANY" ? null : direction };
}
