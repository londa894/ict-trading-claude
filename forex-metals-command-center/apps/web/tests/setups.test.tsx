// @vitest-environment jsdom
import type { ChartCandle, Setup, SetupAnalysis, SetupEvent } from "@fmcc/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { resolveSetupOverlay } from "../src/components/ChartPanel";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { loadSetups } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildSetupOverlay, reconcileSetups, setupSyncError } from "../src/lib/setups";

afterEach(cleanup);

const T = (min: number) => new Date(Date.UTC(2024, 0, 9, 7, min)).toISOString().replace(".000Z", "Z");

const steps = (): Setup["steps"] => [
  { step: "HTF_BIAS", status: "DONE", detail: "BULLISH" },
  { step: "DOL_TARGET", status: "DONE", detail: "PDH" },
  { step: "LIQUIDITY_EVENT", status: "DONE", detail: "SWEEP ASIA_LOW" },
  { step: "DISPLACEMENT", status: "DONE", detail: "with the break" },
  { step: "MSS", status: "DONE", detail: "INTERNAL MSS" },
  { step: "PD_ARRAY", status: "DONE", detail: "1 leg FVG(s)" },
  { step: "RETRACEMENT", status: "PENDING", detail: "none" },
  { step: "LTF_CONFIRMATION", status: "PENDING", detail: "M15_CLOSE confirmation (STANDARD)" },
  { step: "RISK", status: "NOT_EVALUATED", detail: "Risk engine (Phase 9)" },
];

const setup = (over: Partial<Setup> = {}): Setup => ({
  id: "SETUP:BULLISH:a",
  setupType: "LIQUIDITY_SWEEP_MSS",
  direction: "BULLISH",
  state: "WAITING_FOR_RETRACEMENT",
  terminal: false,
  tradingDay: "2024-01-09",
  discoveredAt: T(0),
  stateChangedAt: T(45),
  target: { poolId: "PDH:x", poolType: "PDH", label: "PDH 2024-01-08", price: 2040 },
  liquidityEvent: { poolId: "ASIA_LOW:x", poolType: "ASIA_LOW", eventType: "SWEEP", time: T(15), extreme: 2028.9 },
  mss: { eventId: "e", type: "MSS", level: "INTERNAL", time: T(30), price: 2031, displacementQualifier: "PRESENT" },
  protectiveLevel: 2028.9,
  zoneIds: ["FVG:x"],
  touchedZoneId: null,
  reason: null,
  nextRequiredEvent: "retracement into the displacement leg's FVG",
  steps: steps(),
  entryPlan: null,
  ...over,
});

const ev = (state: SetupEvent["state"], min: number, over: Partial<SetupEvent> = {}): SetupEvent => ({
  id: `SETUP:BULLISH:a:${state}:${min}`, setupId: "SETUP:BULLISH:a", direction: "BULLISH", state, time: T(min), price: 2030, detail: "", ...over,
});

function analysis(over: Partial<SetupAnalysis> = {}): SetupAnalysis {
  const old = setup({ id: "SETUP:BEARISH:old", direction: "BEARISH", state: "EXPIRED", terminal: true, reason: "trading day ended", nextRequiredEvent: null });
  return {
    symbol: "XAUUSD",
    timeframe: "M15",
    asOf: T(60),
    candleCount: 300,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    bias: { direction: "BULLISH", timeframes: ["H4", "H1"], latest: [] },
    currentState: "WAITING_FOR_RETRACEMENT",
    current: setup(),
    setups: [old, setup()],
    events: [
      ev("EXPIRED", 0, { setupId: "SETUP:BEARISH:old", direction: "BEARISH" }),
      ev("DISCOVERED", 0),
      ev("WATCH", 0),
      ev("LIQUIDITY_EVENT", 15),
      ev("SETUP_ARMED", 30),
      ev("WAITING_FOR_RETRACEMENT", 45),
    ],
    po3: { tradingDay: "2024-01-09", phase: "MANIPULATION", dailyOpen: 2031, adr: 20, detail: "" },
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: T(61),
    ...over,
  };
}

const candles: ChartCandle[] = [0, 15, 30, 45, 60].map((m) => ({
  time: T(m), open: 2030, high: 2031, low: 2029, close: 2030.5, volume: 1, isClosed: true,
}));
const chart: ChartState = {
  status: "READY", symbol: "XAUUSD", timeframe: "M15", quality: "CURRENT", marketStatus: "OPEN", isSynthetic: false,
  provider: "p", sourceTimeframe: "M15", candles, issues: [],
};

describe("reconcileSetups", () => {
  it("accepts a valid analysis and a blocked one", () => {
    expect(reconcileSetups("xauusd", analysis()).status).toBe("READY");
    expect(
      reconcileSetups("XAUUSD", analysis({ eligibleForDecision: false, ineligibility: ["DATA_SYNTHETIC"], currentState: "BLOCKED" })).status,
    ).toBe("READY");
  });
  const a = analysis();
  it.each([
    ["unreachable", null],
    ["LONG_READY setup (no entry engine yet)", { ...a, setups: [setup({ state: "LONG_READY" })], current: setup({ state: "LONG_READY" }), currentState: "LONG_READY" }],
    ["SHORT_READY event", { ...a, events: [...a.events, ev("SHORT_READY", 60)] }],
    ["BLOCKED setup without a plan", { ...a, setups: [setup({ state: "BLOCKED" })], current: setup({ state: "BLOCKED" }), currentState: "BLOCKED" }],
    ["risk step evaluated", { ...a, setups: [setup({ steps: steps().map((s) => (s.step === "RISK" ? { ...s, status: "DONE" as const } : s)) })] }],
    ["two open setups", { ...a, setups: [setup(), setup({ id: "SETUP:BULLISH:b" })] }],
    ["current is not the open setup", { ...a, current: setup({ id: "other" }) }],
    ["BLOCKED current state for a non-blocked setup", { ...a, currentState: "BLOCKED" }],
    ["ineligible data not BLOCKED", { ...a, eligibleForDecision: false, ineligibility: ["DATA_STALE"] }],
    ["eligible data with a mismatched state", { ...a, currentState: "SETUP_ARMED" }],
    ["terminal flag inconsistent", { ...a, setups: [setup({ terminal: true })] }],
    ["event for unknown setup", { ...a, events: [ev("WATCH", 0, { setupId: "nope" })] }],
    ["unknown PO3 phase", { ...a, po3: { ...a.po3, phase: "SPRING" as never } }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileSetups("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});

describe("setup overlay", () => {
  it("marks milestones and draws the open setup's DOL and invalidation levels only", () => {
    const o = buildSetupOverlay(analysis());
    expect(o.markers.map((m) => m.text)).toEqual(["Setup exp", "Setup sweep", "Armed"]);
    expect(o.priceLines.map((p) => [p.title, p.price])).toEqual([
      ["Setup DOL PDH 2024-01-08", 2040],
      ["Setup invalidation", 2028.9],
    ]);
    expect(buildSetupOverlay(analysis({ current: null, currentState: null, setups: [analysis().setups[0]!] })).priceLines).toEqual([]);
  });

  it("is off by default, M15 only, hidden when out of sync or unavailable", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolveSetupOverlay(chart, ready, false).overlay).toBeNull();
    expect(resolveSetupOverlay(chart, ready, true).overlay).not.toBeNull();
    expect(resolveSetupOverlay({ ...chart, timeframe: "M5" }, ready, true).note).toMatch(/M15 only/);
    expect(resolveSetupOverlay({ ...chart, candles: candles.slice(2) }, ready, true).note).toMatch(/refreshing/);
    expect(resolveSetupOverlay(chart, { status: "UNAVAILABLE", reason: "x" }, true).note).toMatch(/Setups hidden/);
    expect(setupSyncError(analysis(), candles)).toBeNull();
  });
});

describe("loadSetups", () => {
  it("requests the setups endpoint", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadSetups("XAUUSD", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/setups\/XAUUSD$/);
  });
});

describe("setup progress panel (OVERVIEW)", () => {
  const decision = { ...unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T(61)), "0.19.0-phase19"), setupState: "WAITING_FOR_RETRACEMENT", setupType: "LIQUIDITY_SWEEP_MSS" };

  it("shows state, missing next step, checklist and the last closed setup", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted setups={{ status: "READY", analysis: analysis() }} />);
    expect(screen.getByTestId("setup-eligibility").textContent).toContain("WAITING_FOR_RETRACEMENT");
    expect(screen.getByTestId("setup-eligibility").textContent).toContain("PO3 MANIPULATION");
    expect(screen.getByTestId("setup-next").textContent).toContain("retracement into the displacement leg's FVG");
    const steps = screen.getByTestId("setup-steps").textContent ?? "";
    expect(steps).toContain("✓ MSS");
    expect(steps).toContain("· RETRACEMENT");
    expect(steps).toContain("n/e RISK");
    expect(steps).toContain("· LTF_CONFIRMATION");
    expect(screen.getByTestId("setup-last").textContent).toContain("EXPIRED · trading day ended");
    expect(screen.getByText("WAITING_FOR_RETRACEMENT (LIQUIDITY_SWEEP_MSS)")).toBeTruthy();
  });

  it("no open setup and fail-safe", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted
      setups={{ status: "READY", analysis: analysis({ current: null, currentState: null, setups: [analysis().setups[0]!], bias: { direction: null, timeframes: ["H4", "H1"], latest: [] } }) }} />);
    expect(screen.getByTestId("setup-none").textContent).toContain("no common HTF bias");
    cleanup();
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false} setups={{ status: "UNAVAILABLE", reason: "Setup API unreachable" }} />);
    expect(screen.getByTestId("setup-unavailable").textContent).toContain("Setup API unreachable");
  });
});
