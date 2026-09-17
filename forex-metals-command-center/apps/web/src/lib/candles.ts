/**
 * Chart series validation (client side, fail-safe). The chart draws only candles it can verify.
 * This performs no market analysis — it rejects payloads that are malformed or inconsistent.
 */
import {
  CHART_TIMEFRAMES,
  DATA_QUALITIES,
  type ChartCandle,
  type ChartTimeframe,
  type DataQuality,
  type MarketStatus,
  type ValidationIssue,
} from "@fmcc/shared-types";

export type ChartState =
  | {
      status: "READY";
      symbol: string;
      timeframe: ChartTimeframe;
      quality: DataQuality;
      marketStatus: MarketStatus;
      isSynthetic: boolean;
      provider: string;
      sourceTimeframe: string;
      candles: ChartCandle[];
      issues: ValidationIssue[];
    }
  | {
      status: "UNAVAILABLE";
      symbol: string;
      timeframe: ChartTimeframe;
      quality: DataQuality;
      isSynthetic: boolean;
      reason: string;
      issues: ValidationIssue[];
    };

const DRAWABLE: readonly DataQuality[] = ["LIVE", "CURRENT", "DELAYED", "STALE"];

export function isChartTimeframe(value: unknown): value is ChartTimeframe {
  return typeof value === "string" && (CHART_TIMEFRAMES as readonly string[]).includes(value);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]00:00)$/;

/** Returns an error string, or null when the candle list is drawable. */
export function candleListError(candles: unknown): string | null {
  if (!Array.isArray(candles)) return "candles is not an array";
  let previous = -Infinity;
  for (let i = 0; i < candles.length; i++) {
    const c: unknown = candles[i];
    if (!isObject(c)) return `candle ${i} is not an object`;
    if (typeof c.time !== "string" || !ISO_UTC.test(c.time)) return `candle ${i} time is not ISO UTC`;
    const t = Date.parse(c.time);
    if (!(t > previous)) return `candle ${i} is not strictly ascending`;
    previous = t;
    const { open, high, low, close } = c;
    const prices = [open, high, low, close];
    if (!prices.every((p) => typeof p === "number" && Number.isFinite(p) && p > 0)) {
      return `candle ${i} has non-finite or non-positive prices`;
    }
    const [o, h, l, cl] = prices as number[];
    if (h! < Math.max(o!, cl!) || l! > Math.min(o!, cl!)) return `candle ${i} has impossible OHLC`;
    if (!(c.volume === null || (typeof c.volume === "number" && Number.isFinite(c.volume) && c.volume >= 0))) {
      return `candle ${i} has invalid volume`;
    }
    if (typeof c.isClosed !== "boolean") return `candle ${i} isClosed missing`;
    if (!c.isClosed && i !== candles.length - 1) return `candle ${i} is forming but not the latest`;
  }
  return null;
}

export function reconcileChartSeries(
  requestedSymbol: string,
  requestedTimeframe: ChartTimeframe,
  payload: unknown,
): ChartState {
  const symbol = requestedSymbol.toUpperCase();
  const unavailable = (
    reason: string,
    extra: Partial<{ quality: DataQuality; isSynthetic: boolean; issues: ValidationIssue[] }> = {},
  ): ChartState => ({
    status: "UNAVAILABLE",
    symbol,
    timeframe: requestedTimeframe,
    quality: extra.quality ?? "DISCONNECTED",
    isSynthetic: extra.isSynthetic ?? false,
    reason,
    issues: extra.issues ?? [],
  });

  if (payload === null || payload === undefined) return unavailable("Chart data API unreachable");
  if (!isObject(payload)) return unavailable("Malformed chart payload", { quality: "INVALID" });
  if (payload.symbol !== symbol) return unavailable("Chart symbol mismatch", { quality: "INVALID" });
  if (payload.timeframe !== requestedTimeframe) return unavailable("Chart timeframe mismatch", { quality: "INVALID" });
  if (typeof payload.quality !== "string" || !(DATA_QUALITIES as readonly string[]).includes(payload.quality)) {
    return unavailable("Unknown data quality", { quality: "INVALID" });
  }
  const quality = payload.quality as DataQuality;
  const isSynthetic = payload.isSynthetic === true;
  const issues = Array.isArray(payload.issues) ? (payload.issues as ValidationIssue[]) : [];
  const meta = { quality, isSynthetic, issues };

  if (!DRAWABLE.includes(quality)) {
    const why = typeof payload.providerError === "string" ? payload.providerError : `Data quality ${quality}`;
    if (Array.isArray(payload.candles) && payload.candles.length > 0) {
      return unavailable("Backend returned candles for non-drawable data", { ...meta, quality: "INVALID" });
    }
    return unavailable(why, meta);
  }
  const error = candleListError(payload.candles);
  if (error) return unavailable(`Rejected chart payload: ${error}`, { ...meta, quality: "INVALID" });
  const candles = payload.candles as ChartCandle[];
  if (candles.length === 0) return unavailable("No candles available", meta);

  return {
    status: "READY",
    symbol,
    timeframe: requestedTimeframe,
    quality,
    marketStatus: (payload.marketStatus as MarketStatus) ?? "UNKNOWN",
    isSynthetic,
    provider: typeof payload.provider === "string" ? payload.provider : "unknown",
    sourceTimeframe: typeof payload.sourceTimeframe === "string" ? payload.sourceTimeframe : requestedTimeframe,
    candles,
    issues,
  };
}

export type ChartBar = { time: number; open: number; high: number; low: number; close: number };
export type VolumeBar = { time: number; value: number; color: string };

export const UP_COLOR = "#3fb950";
export const DOWN_COLOR = "#f85149";

/** Maps validated candles to renderer data: UTC epoch seconds, volume omitted when unknown. */
export function toRendererData(candles: ChartCandle[]): { bars: ChartBar[]; volumes: VolumeBar[] } {
  const bars: ChartBar[] = [];
  const volumes: VolumeBar[] = [];
  for (const c of candles) {
    const time = Math.floor(Date.parse(c.time) / 1000);
    bars.push({ time, open: c.open, high: c.high, low: c.low, close: c.close });
    if (c.volume !== null) {
      volumes.push({ time, value: c.volume, color: `${c.close >= c.open ? UP_COLOR : DOWN_COLOR}66` });
    }
  }
  return { bars, volumes };
}
