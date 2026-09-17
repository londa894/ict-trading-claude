import type { ChartCandle, StructureAnalysis, StructureEvent, Swing } from "@fmcc/shared-types";
import { describe, expect, it } from "vitest";

import { resolveOverlay } from "../src/components/ChartPanel";
import { loadAlignment, loadStructure } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { buildOverlay, overlaySyncError, reconcileStructure } from "../src/lib/structure";

const T = (min: number) => new Date(Date.UTC(2024, 0, 9, 10, min)).toISOString().replace(".000Z", "Z");
const TE = (min: number) => T(min + 5);

const swing = (over: Partial<Swing> = {}): Swing => ({
  id: "EXTERNAL:HIGH:x",
  level: "EXTERNAL",
  kind: "HIGH",
  label: "HH",
  price: 2031,
  time: T(5),
  confirmedAt: TE(10),
  brokenAt: T(20),
  brokenBy: "e1",
  ...over,
});

const event = (over: Partial<StructureEvent> = {}): StructureEvent => ({
  id: "e1",
  level: "EXTERNAL",
  type: "BOS",
  direction: "BULLISH",
  status: "CONFIRMED",
  confirmation: "CANDLE_CLOSE",
  price: 2031,
  time: T(20),
  brokenSwingId: "EXTERNAL:HIGH:x",
  brokenSwingTime: T(5),
  trendBefore: "NONE",
  ambiguous: false,
  liquidityQualifier: "NOT_EVALUATED",
  displacementQualifier: "NOT_EVALUATED",
  ...over,
});

function analysis(over: Partial<StructureAnalysis> = {}): StructureAnalysis {
  const level = (lvl: "INTERNAL" | "EXTERNAL") => ({
    level: lvl,
    pivotLength: lvl === "INTERNAL" ? 3 : 10,
    state: "BULLISH" as const,
    trend: "BULLISH" as const,
    swings: [
      swing({ level: lvl }),
      swing({ level: lvl, kind: "LOW", label: "HL", price: 2029, time: T(10), confirmedAt: TE(15), brokenAt: null, brokenBy: null }),
    ],
    events: [event({ level: lvl, id: `${lvl}-e1` }), event({ level: lvl, id: `${lvl}-p`, status: "POTENTIAL", time: T(15), confirmation: "WICK_ONLY" })].sort(
      (a, b) => Date.parse(a.time) - Date.parse(b.time),
    ),
    protectedHigh: null,
    protectedLow: swing({ level: lvl, kind: "LOW", label: "HL", price: 2029, time: T(10), brokenAt: null, brokenBy: null }),
    barsSinceLastBreak: 2,
  });
  return {
    symbol: "XAUUSD",
    timeframe: "M5",
    asOf: TE(25),
    candleCount: 6,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    internal: level("INTERNAL"),
    external: level("EXTERNAL"),
    events: [],
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: TE(26),
    ...over,
  };
}

const candles: ChartCandle[] = [0, 5, 10, 15, 20, 25].map((m) => ({
  time: T(m),
  open: 2030,
  high: 2032,
  low: 2028,
  close: 2030.5,
  volume: 1,
  isClosed: true,
}));

const readyChart: ChartState = {
  status: "READY",
  symbol: "XAUUSD",
  timeframe: "M5",
  quality: "CURRENT",
  marketStatus: "OPEN",
  isSynthetic: false,
  provider: "p",
  sourceTimeframe: "M5",
  candles,
  issues: [],
};

describe("reconcileStructure", () => {
  it("accepts a valid analysis", () => {
    expect(reconcileStructure("xauusd", "M5", analysis()).status).toBe("READY");
  });

  it.each([
    ["unreachable", null],
    ["timeframe mismatch", analysis({ timeframe: "H1" })],
    ["symbol mismatch", analysis({ symbol: "EURUSD" })],
    ["unknown quality", { ...analysis(), quality: "GREAT" }],
    ["withheld (no levels)", analysis({ internal: null, external: null, quality: "INVALID" })],
    ["unknown state", { ...analysis(), external: { ...analysis().external!, state: "SIDEWAYS" } }],
    [
      "event before its swing",
      { ...analysis(), external: { ...analysis().external!, events: [event({ brokenSwingTime: T(25), time: T(20) })] } },
    ],
    [
      "swing confirmed before it formed",
      { ...analysis(), external: { ...analysis().external!, swings: [swing({ confirmedAt: T(0) })] } },
    ],
    [
      "events not chronological",
      {
        ...analysis(),
        external: { ...analysis().external!, events: [event({ time: T(25) }), event({ id: "e2", time: T(20) })] },
      },
    ],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileStructure("XAUUSD", "M5", payload).status).toBe("UNAVAILABLE");
  });
});

describe("overlaySyncError", () => {
  it("passes when every anchor exists in the drawn candles", () => {
    expect(overlaySyncError(analysis(), candles)).toBeNull();
  });
  it("fails when the chart window moved on", () => {
    expect(overlaySyncError(analysis(), candles.slice(2))).toMatch(/outside chart window/);
  });
});

describe("buildOverlay", () => {
  it("external only by default: labelled swings, event markers, segments for confirmed events, protected lines", () => {
    const o = buildOverlay(analysis(), { external: true, internal: false });
    expect(o.markers.map((m) => m.text)).toEqual(["HH", "HL", "BOS?", "BOS"]);
    expect(o.markers.map((m) => m.time)).toEqual([...o.markers.map((m) => m.time)].sort((a, b) => a - b));
    expect(o.segments).toHaveLength(1);
    expect(o.segments[0]).toMatchObject({ price: 2031, dashed: false, from: Date.parse(T(5)) / 1000, to: Date.parse(T(20)) / 1000 });
    expect(o.priceLines).toEqual([{ price: 2029, title: "Protected Low", color: "#3fb950" }]);
    const potential = o.markers.find((m) => m.text === "BOS?");
    expect(potential?.color).toBe("#8b949e");
  });

  it("internal overlay is lower-case, dashed and has no protected lines", () => {
    const o = buildOverlay(analysis(), { external: false, internal: true });
    expect(o.markers.every((m) => m.text === m.text.toLowerCase())).toBe(true);
    expect(o.segments.every((s) => s.dashed)).toBe(true);
    expect(o.priceLines).toEqual([]);
  });
});

describe("resolveOverlay", () => {
  const opts = { external: true, internal: false };
  it("draws only when chart and structure are both trusted and in sync", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolveOverlay(readyChart, ready, opts).overlay).not.toBeNull();
    expect(resolveOverlay(readyChart, ready, { external: false, internal: false })).toEqual({ overlay: null, note: null });
    expect(resolveOverlay(null, ready, opts).overlay).toBeNull();
    expect(resolveOverlay(readyChart, { status: "UNAVAILABLE", reason: "x" }, opts).note).toMatch(/Structure hidden/);
    expect(resolveOverlay(readyChart, { status: "READY", analysis: analysis({ timeframe: "H1" }) }, opts).overlay).toBeNull();
    expect(resolveOverlay({ ...readyChart, candles: candles.slice(3) }, ready, opts).note).toMatch(/refreshing/);
  });
});

describe("structure loaders", () => {
  it("requests the structure endpoint for the chart timeframe and validates", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadStructure("XAUUSD", "M5", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/structure\/XAUUSD\?timeframe=M5&limit=300$/);
  });

  it("alignment loader rejects malformed or other-symbol payloads", async () => {
    const make = (body: unknown) => (async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;
    expect(await loadAlignment("XAUUSD", make({ symbol: "EURUSD", alignment: "MIXED", timeframes: [] }))).toBeNull();
    expect(await loadAlignment("XAUUSD", make({ nope: true }))).toBeNull();
    const ok = await loadAlignment("XAUUSD", make({ symbol: "XAUUSD", alignment: "MIXED", htfBias: "UNKNOWN", timeframes: [] }));
    expect(ok?.alignment).toBe("MIXED");
  });
});
