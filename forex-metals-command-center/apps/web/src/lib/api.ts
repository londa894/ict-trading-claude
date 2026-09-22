import type { ChartTimeframe, Direction, MtfStructureResponse, SystemStatus, VerdictAuthority } from "@fmcc/shared-types";

import { reconcileChartSeries, type ChartState } from "./candles";
import { reconcileMarketState, type ReconciledState } from "./failsafe";
import { reconcileLiquidity, type LiquidityLoadState } from "./liquidity";
import { reconcileNoWick, type NoWickLoadState } from "./noWick";
import { reconcilePdArrays, type PdArrayLoadState } from "./pdArrays";
import { reconcileSessions, type SessionLoadState } from "./sessions";
import { reconcileSetups, type SetupLoadState } from "./setups";
import { reconcileEvaluation, type EvaluationLoadState } from "./evaluation";
import { reconcileNews, type NewsLoadState } from "./news";
import { reconcileMacro, type MacroLoadState } from "./macro";
import { reconcileMarkets, reconcileScan, type MarketsLoadState, type ScanLoadState } from "./scanner";
import {
  reconcileAlertFeed,
  reconcileWatches,
  watchRequestBody,
  type AlertFeedLoadState,
  type WatchesLoadState,
} from "./alerts";
import { isAlignment, reconcileStructure, type StructureLoadState } from "./structure";

// Prefer an explicit override; otherwise talk to the API on the SAME host the page was loaded from
// (port 8000). This makes it work both locally (localhost) and over Tailscale (the tailnet name)
// without hardcoding a hostname that only resolves in one place.
const API_HOST_OVERRIDE = process.env.NEXT_PUBLIC_API_BASE_URL;
export const API_BASE_URL =
  API_HOST_OVERRIDE && API_HOST_OVERRIDE.length > 0
    ? API_HOST_OVERRIDE
    : typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.hostname}:8000`
      : "http://localhost:8000";
// The API serializes upstream (TradeLocker) calls behind a rate-limit throttle, so a cold request can take
// several seconds. Keep this generous enough that a slow-but-successful call is not shown as "unreachable".
const TIMEOUT_MS = 20000;

async function getJson(path: string, fetcher: typeof fetch, timeoutMs = TIMEOUT_MS): Promise<unknown | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetcher(`${API_BASE_URL}${path}`, { signal: controller.signal, cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as unknown;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export type DashboardState = {
  status: SystemStatus | null;
  market: ReconciledState;
  fetchedAt: string;
};

function authorityOf(status: unknown): VerdictAuthority | null {
  if (typeof status !== "object" || status === null) return null;
  const value = (status as { verdictAuthority?: unknown }).verdictAuthority;
  return value === "FAIL_SAFE_ONLY" || value === "FULL" ? value : null;
}

export async function loadDashboard(symbol: string, fetcher: typeof fetch = fetch): Promise<DashboardState> {
  const [status, market] = await Promise.all([
    getJson("/api/v1/system/status", fetcher),
    getJson(`/api/v1/market-state/${encodeURIComponent(symbol)}`, fetcher),
  ]);
  const now = new Date();
  return {
    status: authorityOf(status) ? (status as SystemStatus) : null,
    market: reconcileMarketState(symbol, market, authorityOf(status), now),
    fetchedAt: now.toISOString(),
  };
}

export const CHART_LIMIT = 300;

export async function loadChartSeries(
  symbol: string,
  timeframe: ChartTimeframe,
  fetcher: typeof fetch = fetch,
  limit = CHART_LIMIT,
): Promise<ChartState> {
  const query = new URLSearchParams({ timeframe, limit: String(limit) });
  const payload = await getJson(`/api/v1/candles/${encodeURIComponent(symbol)}?${query}`, fetcher);
  return reconcileChartSeries(symbol, timeframe, payload);
}
export async function loadStructure(
  symbol: string,
  timeframe: ChartTimeframe,
  fetcher: typeof fetch = fetch,
  limit = CHART_LIMIT,
): Promise<StructureLoadState> {
  const query = new URLSearchParams({ timeframe, limit: String(limit) });
  const payload = await getJson(`/api/v1/structure/${encodeURIComponent(symbol)}?${query}`, fetcher);
  return reconcileStructure(symbol, timeframe, payload);
}

export async function loadAlignment(symbol: string, fetcher: typeof fetch = fetch): Promise<MtfStructureResponse | null> {
  const payload = await getJson(`/api/v1/structure/${encodeURIComponent(symbol)}/alignment`, fetcher);
  return isAlignment(payload) && payload.symbol === symbol.toUpperCase() ? payload : null;
}

export async function loadLiquidity(
  symbol: string,
  timeframe: ChartTimeframe,
  fetcher: typeof fetch = fetch,
  limit = CHART_LIMIT,
): Promise<LiquidityLoadState> {
  const query = new URLSearchParams({ timeframe, limit: String(limit) });
  const payload = await getJson(`/api/v1/liquidity/${encodeURIComponent(symbol)}?${query}`, fetcher);
  return reconcileLiquidity(symbol, timeframe, payload);
}

export async function loadPdArrays(
  symbol: string,
  timeframe: ChartTimeframe,
  fetcher: typeof fetch = fetch,
  limit = CHART_LIMIT,
): Promise<PdArrayLoadState> {
  const query = new URLSearchParams({ timeframe, limit: String(limit) });
  const payload = await getJson(`/api/v1/pd-arrays/${encodeURIComponent(symbol)}?${query}`, fetcher);
  return reconcilePdArrays(symbol, timeframe, payload);
}

export async function loadNoWick(
  symbol: string,
  timeframe: ChartTimeframe,
  fetcher: typeof fetch = fetch,
  limit = CHART_LIMIT,
): Promise<NoWickLoadState> {
  const query = new URLSearchParams({ timeframe, limit: String(limit) });
  const payload = await getJson(`/api/v1/no-wick/${encodeURIComponent(symbol)}?${query}`, fetcher);
  return reconcileNoWick(symbol, timeframe, payload);
}

export async function loadSessions(symbol: string, fetcher: typeof fetch = fetch): Promise<SessionLoadState> {
  const payload = await getJson(`/api/v1/sessions/${encodeURIComponent(symbol)}`, fetcher);
  return reconcileSessions(symbol, payload);
}

export async function loadSetups(symbol: string, fetcher: typeof fetch = fetch): Promise<SetupLoadState> {
  const payload = await getJson(`/api/v1/setups/${encodeURIComponent(symbol)}`, fetcher);
  return reconcileSetups(symbol, payload);
}

export async function loadEvaluation(symbol: string, fetcher: typeof fetch = fetch): Promise<EvaluationLoadState> {
  const payload = await getJson(`/api/v1/evaluation/${encodeURIComponent(symbol)}`, fetcher);
  return reconcileEvaluation(symbol, payload);
}

/** A cold scan runs every symbol's decision pipeline (~1.7 s each), so it gets a longer timeout. */
export const SCAN_TIMEOUT_MS = 45_000;

export async function loadMarkets(fetcher: typeof fetch = fetch): Promise<MarketsLoadState> {
  return reconcileMarkets(await getJson("/api/v1/markets", fetcher));
}

export async function loadScan(
  symbols: readonly string[] | null,
  options: { minScore?: number | null; onlySetups?: boolean } = {},
  fetcher: typeof fetch = fetch,
): Promise<ScanLoadState> {
  const query = new URLSearchParams();
  if (symbols && symbols.length > 0) query.set("symbols", symbols.join(","));
  if (options.minScore !== null && options.minScore !== undefined) query.set("minScore", String(options.minScore));
  if (options.onlySetups) query.set("onlySetups", "true");
  const qs = query.toString();
  const payload = await getJson(`/api/v1/scanner${qs ? `?${qs}` : ""}`, fetcher, SCAN_TIMEOUT_MS);
  return reconcileScan(payload, symbols && symbols.length > 0 ? symbols : null);
}

export async function loadAlerts(
  since: number,
  authority: VerdictAuthority | null,
  fetcher: typeof fetch = fetch,
): Promise<AlertFeedLoadState> {
  const query = new URLSearchParams({ since: String(since), limit: "200" });
  return reconcileAlertFeed(await getJson(`/api/v1/alerts?${query}`, fetcher), authority);
}

export async function loadReadyWatches(
  authority: VerdictAuthority | null,
  fetcher: typeof fetch = fetch,
): Promise<WatchesLoadState> {
  return reconcileWatches(await getJson("/api/v1/alerts/ready-watches", fetcher), authority);
}

/** Creates an ALERT_ME_WHEN_READY watch. Returns an error message, or null on success. */
export async function createReadyWatch(
  symbol: string,
  direction: Direction | "ANY",
  fetcher: typeof fetch = fetch,
): Promise<string | null> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/alerts/ready-watches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(watchRequestBody(symbol, direction)),
      cache: "no-store",
    });
    if (res.status === 201) return null;
    return `Watch not created (HTTP ${res.status})`;
  } catch {
    return "Alerts API unreachable";
  }
}

export async function deleteReadyWatch(id: string, fetcher: typeof fetch = fetch): Promise<string | null> {
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/alerts/ready-watches/${encodeURIComponent(id)}`, {
      method: "DELETE",
      cache: "no-store",
    });
    return res.status === 204 ? null : `Watch not removed (HTTP ${res.status})`;
  } catch {
    return "Alerts API unreachable";
  }
}

/** Macro context; with the open setup's direction the payload also carries the state versus that direction. */
export async function loadMacro(
  symbol: string,
  direction: Direction | null = null,
  fetcher: typeof fetch = fetch,
): Promise<MacroLoadState> {
  const query = direction ? `?direction=${direction}` : "";
  return reconcileMacro(symbol, await getJson(`/api/v1/macro/${encodeURIComponent(symbol)}${query}`, fetcher));
}

export async function loadNews(symbol: string, fetcher: typeof fetch = fetch): Promise<NewsLoadState> {
  return reconcileNews(symbol, await getJson(`/api/v1/news/${encodeURIComponent(symbol)}`, fetcher));
}
