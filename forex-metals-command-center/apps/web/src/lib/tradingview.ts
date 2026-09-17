/**
 * TradingView Desktop chart control (client-safe helpers).
 *
 * This only changes the resolution shown in the user's own TradingView Desktop app via the local MCP
 * bridge. TradingView is never used as a market-data source: nothing here reads prices.
 */

/** Toolbar order as TradingView shows it. `code` is the resolution string TradingView's chart API uses. */
export const TV_TIMEFRAMES = [
  { label: "1m", code: "1" },
  { label: "2m", code: "2" },
  { label: "5m", code: "5" },
  { label: "15m", code: "15" },
  { label: "30m", code: "30" },
  { label: "1h", code: "60" },
  { label: "2h", code: "120" },
  { label: "3h", code: "180" },
  { label: "4h", code: "240" },
  { label: "6h", code: "360" },
  { label: "8h", code: "480" },
  { label: "10h", code: "600" },
  { label: "12h", code: "720" },
  { label: "16h", code: "960" },
  { label: "D", code: "D" },
  { label: "2D", code: "2D" },
  { label: "3D", code: "3D" },
  { label: "W", code: "W" },
  { label: "M", code: "M" },
] as const;

export type TvTimeframeCode = (typeof TV_TIMEFRAMES)[number]["code"];

const CODES = new Set<string>(TV_TIMEFRAMES.map((t) => t.code));

export function isTvTimeframeCode(value: unknown): value is TvTimeframeCode {
  return typeof value === "string" && CODES.has(value);
}

/** TradingView reports daily/weekly/monthly as "1D"/"1W"/"1M" or "D"/"W"/"M"; treat them as the same. */
export function normalizeResolution(resolution: string): string {
  const r = resolution.trim().toUpperCase();
  return /^1[DWM]$/.test(r) ? r.slice(1) : r;
}

export function labelForResolution(resolution: string | null): string {
  if (resolution === null) return "—";
  const code = normalizeResolution(resolution);
  return TV_TIMEFRAMES.find((t) => t.code === code)?.label ?? resolution;
}

export type TvChartState = {
  connected: boolean;
  symbol: string | null;
  resolution: string | null;
  error: string | null;
};

export const TV_DISCONNECTED: TvChartState = {
  connected: false,
  symbol: null,
  resolution: null,
  error: "TradingView bridge unreachable",
};

/** Validates the local API route payload; anything unexpected is treated as disconnected. */
export function reconcileTvState(payload: unknown): TvChartState {
  if (typeof payload !== "object" || payload === null) return TV_DISCONNECTED;
  const p = payload as Record<string, unknown>;
  if (typeof p.connected !== "boolean") return TV_DISCONNECTED;
  const str = (v: unknown) => (typeof v === "string" && v.length > 0 ? v : null);
  if (!p.connected) return { ...TV_DISCONNECTED, error: str(p.error) ?? TV_DISCONNECTED.error };
  const resolution = str(p.resolution);
  if (resolution === null) return { ...TV_DISCONNECTED, error: "TradingView reported no timeframe" };
  return { connected: true, symbol: str(p.symbol), resolution, error: str(p.error) };
}

const ROUTE = "/api/tradingview/timeframe";

async function requestState(init: RequestInit | undefined, fetcher: typeof fetch): Promise<TvChartState> {
  try {
    const res = await fetcher(ROUTE, { cache: "no-store", ...init });
    const body: unknown = await res.json().catch(() => null);
    return reconcileTvState(body);
  } catch {
    return TV_DISCONNECTED;
  }
}

export function loadTvState(fetcher: typeof fetch = fetch): Promise<TvChartState> {
  return requestState(undefined, fetcher);
}

export function setTvTimeframe(code: TvTimeframeCode, fetcher: typeof fetch = fetch): Promise<TvChartState> {
  return requestState(
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ timeframe: code }) },
    fetcher,
  );
}
