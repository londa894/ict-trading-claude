import { describe, expect, it } from "vitest";

import { loadChartSeries } from "../src/lib/api";
import { candleListError, reconcileChartSeries, toRendererData } from "../src/lib/candles";

const candle = (time: string, o = 2030, h = 2031, l = 2029, c = 2030.5, isClosed = true) => ({
  time,
  open: o,
  high: h,
  low: l,
  close: c,
  volume: 100,
  isClosed,
});

function payload(overrides: Record<string, unknown> = {}) {
  return {
    symbol: "XAUUSD",
    timeframe: "M5",
    sourceTimeframe: "M5",
    provider: "fixture",
    isSynthetic: true,
    quality: "CURRENT",
    marketStatus: "OPEN",
    candles: [candle("2024-01-09T10:00:00Z"), candle("2024-01-09T10:05:00Z", 2030, 2032, 2030, 2031, false)],
    issues: [],
    providerError: null,
    strategyVersion: "0.20.0-phase20",
    generatedAt: "2024-01-09T10:07:00Z",
    ...overrides,
  };
}

describe("reconcileChartSeries", () => {
  it("accepts a valid series", () => {
    const s = reconcileChartSeries("xauusd", "M5", payload());
    expect(s.status).toBe("READY");
    if (s.status === "READY") {
      expect(s.candles).toHaveLength(2);
      expect(s.isSynthetic).toBe(true);
    }
  });

  it("unreachable API is unavailable", () => {
    const s = reconcileChartSeries("XAUUSD", "M5", null);
    expect(s.status).toBe("UNAVAILABLE");
    expect(s.quality).toBe("DISCONNECTED");
  });

  it.each([
    ["symbol mismatch", { symbol: "EURUSD" }],
    ["timeframe mismatch", { timeframe: "H1" }],
    ["unknown quality", { quality: "GOOD" }],
    ["impossible OHLC", { candles: [candle("2024-01-09T10:00:00Z", 2030, 2029, 2028, 2030)] }],
    ["non-positive price", { candles: [candle("2024-01-09T10:00:00Z", 0, 1, 0, 1)] }],
    ["NaN price", { candles: [candle("2024-01-09T10:00:00Z", Number.NaN)] }],
    ["non-UTC time", { candles: [candle("2024-01-09T05:00:00-05:00")] }],
    ["unordered", { candles: [candle("2024-01-09T10:05:00Z"), candle("2024-01-09T10:00:00Z")] }],
    ["duplicate time", { candles: [candle("2024-01-09T10:00:00Z"), candle("2024-01-09T10:00:00Z")] }],
    [
      "forming candle not last",
      { candles: [candle("2024-01-09T10:00:00Z", 2030, 2031, 2029, 2030, false), candle("2024-01-09T10:05:00Z")] },
    ],
    ["candles for INVALID data", { quality: "INVALID" }],
  ])("rejects %s", (_name, overrides) => {
    const s = reconcileChartSeries("XAUUSD", "M5", payload(overrides));
    expect(s.status).toBe("UNAVAILABLE");
  });

  it("DISCONNECTED with no candles surfaces the provider error", () => {
    const s = reconcileChartSeries(
      "XAUUSD",
      "M5",
      payload({ quality: "DISCONNECTED", candles: [], providerError: "No market-data provider configured" }),
    );
    expect(s.status).toBe("UNAVAILABLE");
    if (s.status === "UNAVAILABLE") expect(s.reason).toContain("No market-data provider");
  });

  it("STALE data is drawable (labelled by the panel)", () => {
    expect(reconcileChartSeries("XAUUSD", "M5", payload({ quality: "STALE" })).status).toBe("READY");
  });

  it("empty but valid series is unavailable, not a blank chart", () => {
    expect(reconcileChartSeries("XAUUSD", "M5", payload({ candles: [] })).status).toBe("UNAVAILABLE");
  });
});

describe("candleListError", () => {
  it("accepts null volume", () => {
    expect(candleListError([{ ...candle("2024-01-09T10:00:00Z"), volume: null }])).toBeNull();
  });
  it("rejects negative volume", () => {
    expect(candleListError([{ ...candle("2024-01-09T10:00:00Z"), volume: -1 }])).toMatch(/volume/);
  });
});

describe("toRendererData", () => {
  it("maps ISO UTC to epoch seconds and colours volume by candle direction", () => {
    const { bars, volumes } = toRendererData([
      candle("2024-01-09T10:00:00Z", 10, 12, 9, 11),
      { ...candle("2024-01-09T10:05:00Z", 11, 11, 8, 9), volume: null },
    ]);
    expect(bars.map((b) => b.time)).toEqual([1704794400, 1704794700]);
    expect(bars[0]).toMatchObject({ open: 10, high: 12, low: 9, close: 11 });
    expect(volumes).toHaveLength(1);
    expect(volumes[0]!.color.startsWith("#3fb950")).toBe(true);
  });
});

describe("loadChartSeries", () => {
  it("requests the bounded candles endpoint and validates the result", async () => {
    let requested = "";
    const fetcher = (async (url: string) => {
      requested = url;
      return new Response(JSON.stringify(payload({ timeframe: "H4", sourceTimeframe: "H1" })), { status: 200 });
    }) as unknown as typeof fetch;
    const s = await loadChartSeries("XAUUSD", "H4", fetcher);
    expect(requested).toMatch(/\/api\/v1\/candles\/XAUUSD\?timeframe=H4&limit=300$/);
    expect(s.status).toBe("READY");
  });

  it("HTTP 422/404/500 fail safe", async () => {
    for (const status of [404, 422, 500]) {
      const fetcher = (async () => new Response("{}", { status })) as unknown as typeof fetch;
      expect((await loadChartSeries("XAUUSD", "M5", fetcher)).status).toBe("UNAVAILABLE");
    }
  });
});
