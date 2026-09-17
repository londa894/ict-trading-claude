// @vitest-environment jsdom
import type { ChartCandle, NoWickAnalysis, NoWickEvent, NoWickZone, ScoreComponent } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { resolveNoWickOverlay } from "../src/components/ChartPanel";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { loadNoWick } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";
import {
  buildNoWickOverlay,
  MAX_NW_ZONES,
  NW_BULL,
  noWickSyncError,
  readNoWickState,
  reconcileNoWick,
} from "../src/lib/noWick";

afterEach(cleanup);

const T = (min: number) => new Date(Date.UTC(2024, 0, 9, 10, min)).toISOString().replace(".000Z", "Z");

const components = (): ScoreComponent[] => [
  { factor: "DISPLACEMENT", status: "EVALUATED", points: 25, maxPoints: 25, detail: "STRONG" },
  { factor: "STRUCTURE", status: "EVALUATED", points: 0, maxPoints: 25, detail: "none" },
  { factor: "LIQUIDITY", status: "EVALUATED", points: 20, maxPoints: 20, detail: "SSL" },
  { factor: "FVG", status: "EVALUATED", points: 0, maxPoints: 15, detail: "none" },
  { factor: "TREND", status: "EVALUATED", points: 15, maxPoints: 15, detail: "BULLISH" },
  { factor: "SESSION", status: "NOT_EVALUATED", points: 0, maxPoints: 0, detail: "Phase 6" },
  { factor: "NEWS", status: "NOT_EVALUATED", points: 0, maxPoints: 0, detail: "Phase 13" },
];

const event = (over: Partial<NoWickEvent> = {}): NoWickEvent => ({
  id: "NW:BULLISH:a",
  direction: "BULLISH",
  shape: "TRUE_BULLISH_MARUBOZU",
  classification: "TRUE_BULLISH_MARUBOZU",
  tags: ["NO_ORIGIN_SIDE_WICK", "NO_DESTINATION_SIDE_WICK"],
  strength: "STRONG",
  time: T(10),
  open: 2030,
  high: 2032,
  low: 2030,
  close: 2032,
  bodyPct: 1,
  upperWickPct: 0,
  lowerWickPct: 0,
  bodyAtr: 1.3,
  closeLocationPct: 100,
  insideBar: false,
  candleQualityScore: 90,
  contextScore: 60,
  relevanceScore: 78,
  contextComponents: components(),
  zoneId: "NWZ:BULLISH:a",
  ...over,
});

const zone = (over: Partial<NoWickZone> = {}): NoWickZone => ({
  id: "NWZ:BULLISH:a",
  eventId: "NW:BULLISH:a",
  direction: "BULLISH",
  closeLevel: 2032,
  level25: 2031.5,
  level50: 2031,
  level75: 2030.5,
  openLevel: 2030,
  originExtreme: 2030,
  fvgOverlapIds: ["FVG:BULLISH:x"],
  obOverlap: "NOT_EVALUATED",
  state: "PARTIAL",
  rebalancePct: 30,
  createdAt: T(10),
  knownAt: T(15),
  stateChangedAt: T(20),
  ageBars: 4,
  active: true,
  relevanceScore: 78,
  ...over,
});

function analysis(over: Partial<NoWickAnalysis> = {}): NoWickAnalysis {
  return {
    symbol: "XAUUSD",
    timeframe: "M5",
    asOf: T(30),
    candleCount: 7,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    features: [],
    events: [
      event({ id: "NW:small", strength: "INSIGNIFICANT", classification: "INSIGNIFICANT_NO_WICK", zoneId: null, relevanceScore: 0, time: T(0) }),
      event(),
      event({ id: "NW:BEARISH:b", direction: "BEARISH", shape: "NEAR_BEARISH_MARUBOZU", classification: "NEAR_BEARISH_MARUBOZU", strength: "MEANINGFUL", time: T(15), zoneId: "NWZ:BEARISH:b" }),
    ],
    zones: [
      zone(),
      zone({ id: "NWZ:BEARISH:b", eventId: "NW:BEARISH:b", direction: "BEARISH", state: "INVALIDATED", active: false, createdAt: T(15) }),
    ],
    zoneEvents: [{ id: "ze", zoneId: "NWZ:BULLISH:a", direction: "BULLISH", type: "REBALANCE_25", time: T(20), price: 2031.4, detail: "" }],
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: T(31),
    ...over,
  };
}

const candles: ChartCandle[] = [0, 5, 10, 15, 20, 25, 30].map((m) => ({
  time: T(m), open: 2030, high: 2032, low: 2029, close: 2031, volume: 1, isClosed: true,
}));
const chart: ChartState = {
  status: "READY", symbol: "XAUUSD", timeframe: "M5", quality: "CURRENT", marketStatus: "OPEN", isSynthetic: false,
  provider: "p", sourceTimeframe: "M5", candles, issues: [],
};

describe("reconcileNoWick", () => {
  it("accepts a valid analysis", () => {
    expect(reconcileNoWick("xauusd", "M5", analysis()).status).toBe("READY");
  });
  const a = analysis();
  it.each([
    ["unreachable", null],
    ["timeframe mismatch", analysis({ timeframe: "H1" })],
    ["news-driven label (no calendar yet)", { ...a, events: [event({ classification: "NEWS_DRIVEN_NO_WICK" })] }],
    ["news-driven tag", { ...a, events: [event({ tags: ["NEWS_DRIVEN_NO_WICK"] })] }],
    ["score > 100", { ...a, events: [event({ relevanceScore: 140 })] }],
    ["unevaluated component with points", { ...a, events: [event({ contextComponents: [{ ...components()[5]!, points: 5 }] })] }],
    ["unknown strength", { ...a, events: [event({ strength: "HUGE" as never })] }],
    ["zone for unknown event", { ...a, zones: [zone({ eventId: "nope" })] }],
    ["active flag inconsistent with state", { ...a, zones: [zone({ state: "REACTED" })] }],
    ["rebalance > 100", { ...a, zones: [zone({ rebalancePct: 120 })] }],
    ["zone event for unknown zone", { ...a, zoneEvents: [{ ...a.zoneEvents[0]!, zoneId: "nope" }] }],
    ["withheld", analysis({ events: [], zones: [], zoneEvents: [], candleCount: 0, quality: "INVALID" })],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileNoWick("XAUUSD", "M5", payload).status).toBe("UNAVAILABLE");
  });
});

describe("No Wick overlay", () => {
  it("marks MEANINGFUL+ candles only and draws close/open edges for active zones", () => {
    const o = buildNoWickOverlay(analysis(), candles);
    expect(o.markers.map((m) => m.text)).toEqual(["NW S", "NW M"]);
    expect(o.segments.map((s) => s.id)).toEqual(["NWZ:BULLISH:a:close", "NWZ:BULLISH:a:open"]);
    expect(o.segments.map((s) => s.dashed)).toEqual([false, true]);
    expect(o.segments.every((s) => s.color === NW_BULL && s.to === Date.parse(T(30)) / 1000)).toBe(true);
  });

  it("caps drawn zones", () => {
    const many = Array.from({ length: 20 }, (_, i) => zone({ id: `z${i}` }));
    expect(buildNoWickOverlay(analysis({ zones: many }), candles).segments).toHaveLength(MAX_NW_ZONES * 2);
  });

  it("hides unless trusted and in sync; off by default", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolveNoWickOverlay(chart, ready, true).overlay).not.toBeNull();
    expect(resolveNoWickOverlay(chart, ready, false).overlay).toBeNull();
    expect(resolveNoWickOverlay(chart, { status: "UNAVAILABLE", reason: "x" }, true).note).toMatch(/No Wick hidden/);
    expect(resolveNoWickOverlay({ ...chart, candles: candles.slice(3) }, ready, true).note).toMatch(/refreshing/);
    expect(noWickSyncError(analysis(), candles)).toBeNull();
  });
});

describe("loadNoWick", () => {
  it("requests the no-wick endpoint for the chart timeframe", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadNoWick("XAUUSD", "M5", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/no-wick\/XAUUSD\?timeframe=M5&limit=300$/);
  });
});

describe("NO_WICK tab and decision state", () => {
  const base = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T(31)), "0.19.0-phase19");
  const state = {
    timeframe: "M15", time: T(10), direction: "BULLISH", classification: "TRUE_BULLISH_MARUBOZU", strength: "STRONG",
    candleQualityScore: 90, contextScore: 60, relevanceScore: 78, zoneState: "PARTIAL", authority: "CONTEXT_ONLY",
  } as const;
  const decision = { ...base, noWickState: state };

  it("validates the decision's noWickState", () => {
    expect(readNoWickState(decision)).not.toBeNull();
    expect(readNoWickState({ ...base, noWickState: { ...state, authority: "TRADE" } as never })).toBeNull();
    expect(readNoWickState({ ...base, noWickState: { ...state, strength: "HUGE" } as never })).toBeNull();
    expect(readNoWickState(base)).toBeNull();
  });

  it("lists MEANINGFUL+ candles, components with n/e, and active zones", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted noWick={{ status: "READY", analysis: analysis() }} />);
    expect(screen.getByText(/TRUE_BULLISH_MARUBOZU STRONG · relevance 78/)).toBeTruthy(); // overview
    fireEvent.click(screen.getByRole("tab", { name: "NO_WICK" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    const events = screen.getByTestId("nw-events").textContent ?? "";
    expect(events).toContain("NEAR_BEARISH_MARUBOZU MEANINGFUL");
    expect(events).not.toContain("INSIGNIFICANT");
    expect(events.indexOf("NEAR_BEARISH")).toBeLessThan(events.indexOf("TRUE_BULLISH")); // newest first
    expect(screen.getByTestId("nw-components").textContent).toContain("SESSION n/e");
    const zones = screen.getByTestId("nw-zones").textContent ?? "";
    expect(zones).toContain("PARTIAL 30%");
    expect(zones).toContain("FVG×1");
    expect(zones).not.toContain("INVALIDATED");
    expect(screen.getByText(/never authorizes a trade/)).toBeTruthy();
  });

  it("fails safe when the payload is unavailable", () => {
    render(<IntelligencePanel decision={base} data={null} status={null} trusted={false}
      noWick={{ status: "UNAVAILABLE", reason: "No Wick API unreachable" }} />);
    fireEvent.click(screen.getByRole("tab", { name: "NO_WICK" }));
    expect(screen.getByTestId("nw-unavailable").textContent).toContain("No Wick API unreachable");
  });
});
