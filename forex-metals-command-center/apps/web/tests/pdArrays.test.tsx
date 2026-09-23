// @vitest-environment jsdom
import type { ChartCandle, DisplacementEvent, PdArrayAnalysis, PdArrayZone } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { resolvePdArrayOverlay } from "../src/components/ChartPanel";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { loadPdArrays } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildPdArrayOverlay, IFVG_COLOR, MAX_ZONES, pdArraySyncError, reconcilePdArrays } from "../src/lib/pdArrays";

afterEach(cleanup);

const T = (min: number) => new Date(Date.UTC(2024, 0, 9, 10, min)).toISOString().replace(".000Z", "Z");

const zone = (over: Partial<PdArrayZone> = {}): PdArrayZone => ({
  id: "FVG:BULLISH:a",
  type: "FVG",
  direction: "BULLISH",
  top: 2031,
  bottom: 2030,
  midpoint: 2030.5,
  sizeAtr: 0.5,
  sourceTimes: [T(0), T(5), T(10)],
  createdAt: T(10),
  knownAt: T(15),
  state: "PARTIAL",
  fillPct: 20,
  ifvgStatus: null,
  parentId: null,
  displacementGrade: "MODERATE",
  stateChangedAt: T(20),
  invalidatedAt: null,
  ageBars: 3,
  active: true,
  qualityScore: 55,
  ...over,
});

const disp = (over: Partial<DisplacementEvent> = {}): DisplacementEvent => ({
  id: "d1",
  direction: "BULLISH",
  grade: "STRONG",
  magnitudeAtr: 2.2,
  legStart: T(5),
  time: T(5),
  candleCount: 1,
  avgBodyPct: 0.9,
  ...over,
});

function analysis(over: Partial<PdArrayAnalysis> = {}): PdArrayAnalysis {
  return {
    symbol: "XAUUSD",
    timeframe: "M5",
    asOf: T(30),
    candleCount: 7,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    displacements: [disp({ id: "w", grade: "WEAK", time: T(0), legStart: T(0) }), disp()],
    zones: [
      zone(),
      zone({ id: "FVG:BEARISH:old", direction: "BEARISH", state: "INVALIDATED", active: false, qualityScore: null, fillPct: 100 }),
      zone({ id: "IFVG:x", type: "IFVG", direction: "BEARISH", ifvgStatus: "CONFIRMED_IFVG", parentId: "FVG:BEARISH:old",
        state: "FRESH", fillPct: 0, qualityScore: 70, createdAt: T(15), sourceTimes: [T(0), T(5), T(10)] }),
      zone({ id: "IFVG:p", type: "IFVG", direction: "BULLISH", ifvgStatus: "POTENTIAL_IFVG", active: false, qualityScore: null,
        state: "FRESH", fillPct: 0, createdAt: T(25) }),
    ],
    events: [{ id: "e", zoneId: "FVG:BULLISH:a", zoneType: "FVG", direction: "BULLISH", type: "CREATED", time: T(10), price: 2030.5, detail: "" }],
    providerError: null,
    strategyVersion: "0.20.0-phase20",
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

describe("reconcilePdArrays", () => {
  it("accepts a valid analysis", () => {
    expect(reconcilePdArrays("xauusd", "M5", analysis()).status).toBe("READY");
  });
  const a = analysis();
  it.each([
    ["unreachable", null],
    ["timeframe mismatch", analysis({ timeframe: "H1" })],
    ["inverted bounds", { ...a, zones: [zone({ top: 2029 })] }],
    ["fill > 100", { ...a, zones: [zone({ fillPct: 120 })] }],
    ["FVG with IFVG status", { ...a, zones: [zone({ ifvgStatus: "CONFIRMED_IFVG" })] }],
    ["IFVG without status", { ...a, zones: [zone({ type: "IFVG", ifvgStatus: null })] }],
    ["quality on inactive zone", { ...a, zones: [zone({ active: false })] }],
    ["event for unknown zone", { ...a, events: [{ ...a.events[0], zoneId: "nope" }] }],
    ["unknown grade", { ...a, displacements: [disp({ grade: "HUGE" as never })] }],
    ["withheld", analysis({ zones: [], events: [], displacements: [], candleCount: 0, quality: "INVALID" })],
  ])("rejects %s", (_n, payload) => {
    expect(reconcilePdArrays("XAUUSD", "M5", payload).status).toBe("UNAVAILABLE");
  });
});

describe("PD array overlay", () => {
  it("draws edges only for active zones (IFVG dashed/purple) and markers only for STRONG+ displacement", () => {
    const o = buildPdArrayOverlay(analysis(), candles);
    expect(o.segments.map((s) => s.id)).toEqual(["FVG:BULLISH:a:top", "FVG:BULLISH:a:bottom", "IFVG:x:top", "IFVG:x:bottom"]);
    const ifvg = o.segments.filter((s) => s.id.startsWith("IFVG"));
    expect(ifvg.every((s) => s.dashed && s.color === IFVG_COLOR)).toBe(true);
    expect(o.segments.every((s) => s.to === Date.parse(T(30)) / 1000)).toBe(true);
    expect(o.markers.map((m) => m.text)).toEqual(["Disp S"]);
  });

  it("caps drawn zones", () => {
    const many = Array.from({ length: 25 }, (_, i) => zone({ id: `z${i}` }));
    expect(buildPdArrayOverlay(analysis({ zones: many }), candles).segments).toHaveLength(MAX_ZONES * 2);
  });

  it("hides unless trusted and in sync", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolvePdArrayOverlay(chart, ready, true).overlay).not.toBeNull();
    expect(resolvePdArrayOverlay(chart, ready, false).overlay).toBeNull();
    expect(resolvePdArrayOverlay(chart, { status: "UNAVAILABLE", reason: "x" }, true).note).toMatch(/FVG hidden/);
    expect(resolvePdArrayOverlay({ ...chart, candles: candles.slice(4) }, ready, true).note).toMatch(/refreshing/);
    expect(pdArraySyncError(analysis(), candles)).toBeNull();
  });
});

describe("loadPdArrays", () => {
  it("requests the pd-arrays endpoint for the chart timeframe", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadPdArrays("XAUUSD", "M5", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/pd-arrays\/XAUUSD\?timeframe=M5&limit=300$/);
  });
});

describe("PD_ARRAYS tab", () => {
  const decision = {
    ...unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T(31)), "0.20.0-phase20"),
    displacement: "M15 BULLISH STRONG displacement 2.2 ATR over 1 candle(s) (2024-01-09T10:05:00+00:00)",
  };

  it("lists active zones by quality, potential IFVGs and MODERATE+ displacement", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted pdArrays={{ status: "READY", analysis: analysis() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "PD_ARRAYS" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    const rows = screen.getByTestId("active-zones").textContent ?? "";
    expect(rows.indexOf("IFVG")).toBeLessThan(rows.indexOf("Bull FVG")); // quality 70 before 55
    expect(rows).not.toContain("INVALIDATED");
    expect(screen.getByTestId("potential-ifvg").textContent).toContain("needs displacement or acceptance");
    expect(screen.getByTestId("displacements").textContent).toContain("STRONG");
    expect(screen.getByTestId("displacements").textContent).not.toContain("WEAK");
    expect(screen.getByText(/not a/)).toBeTruthy();
  });

  it("fails safe and overview shows the decision's displacement", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false}
      pdArrays={{ status: "UNAVAILABLE", reason: "PD array API unreachable" }} />);
    expect(screen.getByText(decision.displacement)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "PD_ARRAYS" }));
    expect(screen.getByTestId("pd-unavailable").textContent).toContain("PD array API unreachable");
  });
});
