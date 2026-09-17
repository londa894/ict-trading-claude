/**
 * Markets, watchlist and scanner payload validation (client side). A scan ranks attention; it is never a trade
 * signal. The client rejects any scan that claims authority, emits a directional verdict while the backend is
 * FAIL_SAFE_ONLY, shows a READY/ACTIVE setup state, or mixes symbols.
 */
import {
  ASSET_CLASSES,
  BLOCKERS,
  DATA_QUALITIES,
  DECISION_CONFIDENCES,
  MARKET_STATUSES,
  VERDICTS,
  type MarketRow,
  type ScanResponse,
  type ScanRow,
} from "@fmcc/shared-types";

import { gradeFor } from "./evaluation";
import { AUTHORITY_STATES } from "./setups";

export type MarketsLoadState = { status: "READY"; markets: MarketRow[] } | { status: "UNAVAILABLE"; reason: string };
export type ScanLoadState = { status: "READY"; scan: ScanResponse } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const isIso = (v: unknown): boolean => typeof v === "string" && !Number.isNaN(Date.parse(v));
const SYMBOL_RE = /^[A-Z]{3,12}$/;

export function reconcileMarkets(payload: unknown): MarketsLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Markets API unreachable" };
  if (!Array.isArray(payload)) return { status: "UNAVAILABLE", reason: "Malformed markets payload" };
  const seen = new Set<string>();
  for (const m of payload) {
    if (!isObject(m) || typeof m.symbol !== "string" || !SYMBOL_RE.test(m.symbol) || seen.has(m.symbol)) {
      return { status: "UNAVAILABLE", reason: "Rejected markets: symbol invalid or duplicated" };
    }
    seen.add(m.symbol);
    if (!member(ASSET_CLASSES, m.assetClass) || !member(MARKET_STATUSES, m.marketStatus) || typeof m.deeplyValidated !== "boolean") {
      return { status: "UNAVAILABLE", reason: `Rejected markets: ${m.symbol} fields invalid` };
    }
  }
  return { status: "READY", markets: payload as MarketRow[] };
}

function rowError(r: unknown, fullAuthority: boolean): string | null {
  if (!isObject(r) || typeof r.symbol !== "string" || !SYMBOL_RE.test(r.symbol)) return "row malformed";
  if (!member(VERDICTS, r.verdict) || !member(DATA_QUALITIES, r.dataQuality) || !member(DECISION_CONFIDENCES, r.decisionConfidence)) {
    return `${r.symbol}: enum unknown`;
  }
  if (!fullAuthority && r.verdict !== "WAIT" && r.verdict !== "UNAVAILABLE") return `${r.symbol}: directional verdict without authority`;
  if (typeof r.setupState !== "string" || AUTHORITY_STATES.has(r.setupState as never)) return `${r.symbol}: authority setup state`;
  if (!Array.isArray(r.blockers) || !r.blockers.every((b) => member(BLOCKERS, b))) return `${r.symbol}: blocker unknown`;
  if (r.verdict !== "LONG" && r.verdict !== "SHORT" && r.blockers.length === 0) return `${r.symbol}: a fail-safe verdict without blockers`;
  if (r.deeplyValidated === false && !r.blockers.includes("MARKET_NOT_VALIDATED")) return `${r.symbol}: research-only market not flagged`;
  const score = r.setupScore;
  if (score !== null && !(typeof score === "number" && score >= 0 && score <= 100)) return `${r.symbol}: score out of range`;
  if ((score === null) !== (r.setupGrade === null)) return `${r.symbol}: grade without score`;
  if (typeof score === "number" && gradeFor(score) !== r.setupGrade) return `${r.symbol}: grade does not match the score`;
  if (r.verdict === "UNAVAILABLE" && r.setupProgress !== 0) return `${r.symbol}: progress on unusable data`;
  if (!isIso(r.evaluatedAt) || typeof r.cacheAgeSeconds !== "number" || r.cacheAgeSeconds < 0) return `${r.symbol}: timing invalid`;
  return null;
}

export function reconcileScan(payload: unknown, requested: readonly string[] | null = null): ScanLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Scanner API unreachable" };
  if (!isObject(payload) || !Array.isArray(payload.rows) || !Array.isArray(payload.requestedSymbols)) {
    return { status: "UNAVAILABLE", reason: "Malformed scan payload" };
  }
  const reject = (why: string): ScanLoadState => ({ status: "UNAVAILABLE", reason: `Rejected scan: ${why}` });
  if (payload.authority !== "NOT_AUTHORIZED") return reject("authority claimed");
  if (payload.verdictAuthority !== "FAIL_SAFE_ONLY" && payload.verdictAuthority !== "FULL") return reject("verdict authority unknown");
  const asked = (payload.requestedSymbols as unknown[]).map(String);
  if (requested && [...requested].map((s) => s.toUpperCase()).join(",") !== asked.join(",")) return reject("requested symbols mismatch");
  const seen = new Set<string>();
  for (const [i, r] of (payload.rows as unknown[]).entries()) {
    const err = rowError(r, payload.verdictAuthority === "FULL");
    if (err) return reject(err);
    const row = r as ScanRow;
    if (!asked.includes(row.symbol) || seen.has(row.symbol)) return reject(`${row.symbol}: not requested or duplicated`);
    seen.add(row.symbol);
    if (row.rank !== i + 1) return reject("ranks out of order");
  }
  return { status: "READY", scan: payload as unknown as ScanResponse };
}

// --- watchlist (per-viewer display preference; not account data) -------------------------------------

export const WATCHLIST_KEY = "fmcc.watchlist.v1";

export function sanitizeWatchlist(raw: unknown, catalog: readonly string[]): string[] {
  if (!Array.isArray(raw)) return [...catalog];
  const out: string[] = [];
  for (const s of raw) {
    if (typeof s === "string" && catalog.includes(s.toUpperCase()) && !out.includes(s.toUpperCase())) out.push(s.toUpperCase());
  }
  return out;
}

export function readWatchlist(catalog: readonly string[], storage: Pick<Storage, "getItem"> | null = safeStorage()): string[] {
  try {
    const raw = storage?.getItem(WATCHLIST_KEY);
    return raw ? sanitizeWatchlist(JSON.parse(raw) as unknown, catalog) : [...catalog];
  } catch {
    return [...catalog];
  }
}

export function saveWatchlist(symbols: readonly string[], storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(WATCHLIST_KEY, JSON.stringify(symbols));
  } catch {
    // storage blocked: the watchlist still works for this page view
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function progressLabel(row: ScanRow): string {
  return row.setupProgress === 0 ? row.setupState : `${row.setupState} · stage ${row.setupProgress}`;
}

export function ageLabel(seconds: number): string {
  return seconds < 1 ? "fresh" : `${Math.round(seconds)}s cached`;
}
