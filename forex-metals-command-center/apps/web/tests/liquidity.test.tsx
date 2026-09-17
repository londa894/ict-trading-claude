// @vitest-environment jsdom
import type { ChartCandle, LiquidityAnalysis, LiquidityEvent, LiquidityPool } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { resolveLiquidityOverlay } from "../src/components/ChartPanel";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { loadLiquidity } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildLiquidityOverlay, DOL_COLOR, liquiditySyncError, reconcileLiquidity } from "../src/lib/liquidity";
import { mergeOverlays } from "../src/lib/structure";

afterEach(cleanup);

const T = (min: number) => new Date(Date.UTC(2024, 0, 9, 10, min)).toISOString().replace(".000Z", "Z");

const pool = (over: Partial<LiquidityPool> = {}): LiquidityPool => ({
  id: "PDH:x",
  type: "PDH",
  side: "BSL",
  scope: "EXTERNAL",
  label: "PDH 2024-01-08",
  price: 2035,
  formedAt: T(0),
  knownAt: T(0),
  sourceTimes: [],
  state: "FRESH",
  touches: 0,
  stateChangedAt: null,
  taken: false,
  distanceAtr: 2.5,
  magnetScore: 61.5,
  ...over,
});

const event = (over: Partial<LiquidityEvent> = {}): LiquidityEvent => ({
  id: "e1",
  poolId: "SWING:SSL:y",
  poolType: "SWING_LOW",
  side: "SSL",
  type: "SWEEP",
  price: 2025,
  time: T(10),
  extreme: 2024.5,
  close: 2026,
  ...over,
});

function analysis(over: Partial<LiquidityAnalysis> = {}): LiquidityAnalysis {
  const pools = [
    pool(),
    pool({ id: "PDH:older", knownAt: "2024-01-07T22:00:00Z", price: 2040, label: "PDH older" }),
    pool({ id: "PWL:w", type: "PWL", side: "SSL", label: "PWL wk", price: 2010, magnetScore: 40, distanceAtr: 8 }),
    pool({ id: "SWING:SSL:y", type: "SWING_LOW", side: "SSL", label: "Swing low 2025", price: 2025, state: "SWEPT",
      taken: true, magnetScore: null, distanceAtr: 2 }),
    pool({ id: "EQH:z", type: "EQH", side: "BSL", label: "EQH x2", price: 2033, magnetScore: 70, distanceAtr: 1.5 }),
  ];
  return {
    symbol: "XAUUSD",
    timeframe: "M5",
    asOf: T(30),
    candleCount: 7,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    keyLevelsAvailable: true,
    pools,
    events: [event({ id: "t", type: "TOUCH", time: T(5) }), event(), event({ id: "r", type: "RUN", time: T(20), side: "BSL",
      poolId: "PDH:older", poolType: "PDH" })],
    dol: {
      primary: { poolId: "EQH:z", type: "EQH", side: "BSL", label: "EQH x2", price: 2033, magnetScore: 70, distanceAtr: 1.5 },
      secondary: { poolId: "PDH:x", type: "PDH", side: "BSL", label: "PDH 2024-01-08", price: 2035, magnetScore: 61.5, distanceAtr: 2.5 },
      confidence: "MODERATE",
      margin: 30,
      reason: "EQH x2 leads the opposite side by 30 points",
    },
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: T(31),
    ...over,
  };
}

const candles: ChartCandle[] = [0, 5, 10, 15, 20, 25, 30].map((m) => ({
  time: T(m), open: 2030, high: 2031, low: 2029, close: 2030.5, volume: 1, isClosed: true,
}));
const chart: ChartState = {
  status: "READY", symbol: "XAUUSD", timeframe: "M5", quality: "CURRENT", marketStatus: "OPEN", isSynthetic: false,
  provider: "p", sourceTimeframe: "M5", candles, issues: [],
};

describe("reconcileLiquidity", () => {
  it("accepts a valid analysis", () => {
    expect(reconcileLiquidity("xauusd", "M5", analysis()).status).toBe("READY");
  });

  const a = analysis();
  it.each([
    ["unreachable", null],
    ["timeframe mismatch", analysis({ timeframe: "H1" })],
    ["unknown pool state", { ...a, pools: [{ ...a.pools[0], state: "GONE" }] }],
    ["magnet score on a taken pool", { ...a, pools: a.pools.map((p) => (p.taken ? { ...p, magnetScore: 10 } : p)) }],
    ["magnet score > 100", { ...a, pools: [{ ...a.pools[0], magnetScore: 120 }] }],
    ["event for unknown pool", { ...a, events: [event({ poolId: "nope" })] }],
    ["events not chronological", { ...a, events: [event({ time: T(20) }), event({ id: "b", time: T(10) })] }],
    ["DOL points at unknown pool", { ...a, dol: { ...a.dol!, primary: { ...a.dol!.primary!, poolId: "nope" } } }],
    ["unknown DOL confidence", { ...a, dol: { ...a.dol!, confidence: "SURE" } }],
    ["withheld (no pools, no DOL)", analysis({ pools: [], events: [], dol: null, quality: "INVALID" })],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileLiquidity("XAUUSD", "M5", payload).status).toBe("UNAVAILABLE");
  });
});

describe("liquidity overlay", () => {
  it("draws DOL lines, latest untaken key/EQ levels once, and taking-event markers (no touches)", () => {
    const o = buildLiquidityOverlay(analysis());
    expect(o.priceLines.map((l) => l.title)).toEqual(["DOL EQH x2", "DOL2 PDH 2024-01-08", "PWL"]);
    expect(o.priceLines.slice(0, 2).every((l) => l.color === DOL_COLOR && l.style === "solid")).toBe(true);
    expect(o.priceLines.some((l) => l.title === "PDH")).toBe(false); // PDH drawn as DOL2; older PDH not repeated
    expect(o.markers.map((m) => m.text)).toEqual(["Sweep", "Run PDH"]);
    expect(o.markers[0]!.position).toBe("belowBar");
  });

  it("never labels an older level as the current one when the latest is taken", () => {
    const base = analysis({ dol: null });
    const pools = [
      pool({ id: "PDL:new", type: "PDL", side: "SSL", price: 2020, knownAt: T(0), state: "SWEPT", taken: true, magnetScore: null }),
      pool({ id: "PDL:old", type: "PDL", side: "SSL", price: 2015, knownAt: "2024-01-07T22:00:00Z", magnetScore: 30 }),
    ];
    const o = buildLiquidityOverlay({ ...base, pools, events: [] });
    expect(o.priceLines).toEqual([]);
  });

  it("is hidden unless chart and liquidity are trusted and in sync", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolveLiquidityOverlay(chart, ready, true).overlay).not.toBeNull();
    expect(resolveLiquidityOverlay(chart, ready, false).overlay).toBeNull();
    expect(resolveLiquidityOverlay(chart, { status: "UNAVAILABLE", reason: "x" }, true).note).toMatch(/Liquidity hidden/);
    expect(resolveLiquidityOverlay({ ...chart, candles: candles.slice(3) }, ready, true).note).toMatch(/refreshing/);
    expect(liquiditySyncError(analysis(), candles)).toBeNull();
  });

  it("merges with the structure overlay in time order", () => {
    const merged = mergeOverlays(
      { markers: [{ time: 50, position: "aboveBar", shape: "circle", color: "x", text: "HH", size: 1 }], segments: [], priceLines: [] },
      buildLiquidityOverlay(analysis()),
    );
    expect(merged!.markers.map((m) => m.time)).toEqual([...merged!.markers.map((m) => m.time)].sort((x, y) => x - y));
    expect(mergeOverlays(null, null)).toBeNull();
  });
});

describe("loadLiquidity", () => {
  it("requests the liquidity endpoint for the chart timeframe", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadLiquidity("XAUUSD", "M5", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/liquidity\/XAUUSD\?timeframe=M5&limit=300$/);
  });
});

describe("LIQUIDITY tab", () => {
  const decision = {
    ...unavailableDecision("XAUUSD", ["DOL_UNCLEAR"], "x", new Date(T(31)), "0.19.0-phase19"),
    primaryDol: "H1 BSL EQH x2 @ 2033 (magnet 70, 1.5 ATR)",
  };

  it("shows DOL, key levels, nearest pools and events", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted liquidity={{ status: "READY", analysis: analysis() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "LIQUIDITY" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("dol").textContent).toContain("MODERATE");
    expect(screen.getByTestId("dol").textContent).toContain("EQH x2 @ 2033");
    const keys = screen.getByTestId("key-levels").textContent ?? "";
    expect(keys).toContain("PDH2035"); // latest PDH, not the older 2040
    expect(keys).toContain("PWH unavailable");
    expect(screen.getByTestId("nearest-bsl").textContent).toMatch(/^EQH x2/);
    expect(screen.getByTestId("nearest-ssl").textContent).toContain("PWL wk"); // taken swing low excluded
    expect(screen.getByTestId("liquidity-events").textContent).not.toContain("TOUCH");
    expect(screen.getByText(/not a probability/)).toBeTruthy();
  });

  it("fails safe when liquidity is unavailable, and overview shows the decision's DOL", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false}
      liquidity={{ status: "UNAVAILABLE", reason: "Liquidity API unreachable" }} />);
    expect(screen.getByText(decision.primaryDol)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "LIQUIDITY" }));
    expect(screen.getByTestId("liquidity-unavailable").textContent).toContain("Liquidity API unreachable");
  });
});
