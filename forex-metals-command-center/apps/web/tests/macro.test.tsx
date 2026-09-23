// @vitest-environment jsdom
import type { DecisionEvaluation, MacroAssessment } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { MacroSection } from "../src/components/MacroSection";
import { StatusBar } from "../src/components/StatusBar";
import { loadMacro } from "../src/lib/api";
import { reconcileEvaluation } from "../src/lib/evaluation";
import { unavailableDecision } from "../src/lib/failsafe";
import { biasFor, macroLabel, readMacroState, reconcileMacro, stateFor } from "../src/lib/macro";

afterEach(cleanup);

const T = "2024-04-18T15:01:00Z";

function macro(over: Partial<MacroAssessment> = {}): MacroAssessment {
  return {
    symbol: "XAUUSD",
    bias: "BULLISH",
    score: 0.75,
    state: "STRONGLY_SUPPORTIVE",
    direction: "BULLISH",
    drivers: [
      { series: "DXY", configured: "DXY", relationship: -1, weight: 3, direction: "DOWN", contribution: 3, detail: "DXY DOWN (z -2.1)" },
      { series: "US10Y", configured: "US10Y_REAL", relationship: -1, weight: 3, direction: "DOWN", contribution: 3, detail: "fallback" },
      { series: "VIX", configured: "VIX", relationship: 1, weight: 1, direction: null, contribution: null, detail: "missing or stale: not evaluated" },
    ],
    series: [],
    correlation: { series: "DXY", observations: 20, coefficient: -0.41, regime: "ALIGNED", detail: "moving with the configured relationship" },
    provider: "file",
    source: "test macro",
    isSynthetic: false,
    available: true,
    fetchedAt: T,
    reason: null,
    warnings: ["US10Y used instead of US10Y_REAL"],
    thresholds: { supportive: 0.2, stronglySupportive: 0.6 },
    strategyVersion: "0.20.0-phase20",
    generatedAt: T,
    ...over,
  };
}

const unavailable = macro({
  bias: "UNAVAILABLE", score: null, state: "UNAVAILABLE", available: false, drivers: [], correlation: null,
  reason: "no macro data provider is configured (MACRO_PROVIDER)", warnings: [],
});

describe("macro payload validation", () => {
  it("accepts consistent payloads", () => {
    expect(reconcileMacro("XAUUSD", macro()).status).toBe("READY");
    expect(reconcileMacro("xauusd", macro({ direction: null, state: null })).status).toBe("READY");
    expect(reconcileMacro("XAUUSD", unavailable).status).toBe("READY");
    expect(reconcileMacro("XAUUSD", macro({ score: -0.3, bias: "BEARISH", direction: "BEARISH", state: "SUPPORTIVE" })).status).toBe("READY");
  });

  it.each([
    [macro({ symbol: "EURUSD" }), "another market"],
    [macro({ score: 1.5 }), "out of range"],
    [macro({ bias: "BEARISH" }), "bias does not match"],
    [macro({ state: "CONFLICT" }), "state does not match"],
    [macro({ direction: null }), "state without a direction"],
    [{ ...unavailable, bias: "BULLISH" }, "without macro data"],
    [{ ...macro(), blockers: ["MACRO"] }, "never carry blockers"],
    [macro({ correlation: { series: "DXY", observations: 1, coefficient: 1, regime: "MAGIC" as never, detail: "" } }), "regime unknown"],
    [null, "unreachable"],
  ])("rejects %#", (payload, reason) => {
    const r = reconcileMacro("XAUUSD", payload);
    expect(r.status).toBe("UNAVAILABLE");
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });

  it("mirrors the engine thresholds", () => {
    expect(biasFor(0.2, 0.2)).toBe("BULLISH");
    expect(biasFor(0.19, 0.2)).toBe("NEUTRAL");
    expect(biasFor(-0.2, 0.2)).toBe("BEARISH");
    expect(stateFor(-0.2, "BULLISH", 0.2, 0.6)).toBe("CONFLICT");
    expect(stateFor(-0.6, "BULLISH", 0.2, 0.6)).toBe("STRONG_CONFLICT");
    expect(stateFor(-0.6, "BEARISH", 0.2, 0.6)).toBe("STRONGLY_SUPPORTIVE");
  });

  it("loads with the setup direction in the query", async () => {
    const urls: string[] = [];
    const fetcher = (async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify(macro()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadMacro("XAUUSD", "BULLISH", fetcher)).status).toBe("READY");
    await loadMacro("XAUUSD", null, fetcher);
    expect(urls[0]).toContain("/api/v1/macro/XAUUSD?direction=BULLISH");
    expect(urls[1]).toMatch(/\/api\/v1\/macro\/XAUUSD$/);
  });
});

function evaluation(over: Partial<DecisionEvaluation> = {}): DecisionEvaluation {
  return {
    symbol: "XAUUSD", asOf: T, eligibleForDecision: true, ineligibility: [], outcome: "WAIT", direction: "BULLISH",
    setupId: "s", setupType: "LIQUIDITY_SWEEP_MSS", setupState: "WAITING_FOR_MSS", score: 44, evaluatedMax: 80, grade: "D", confidence: "LOW",
    conflictScore: 0, dataQualityScore: 100,
    components: [{ factor: "MACRO", status: "EVALUATED", points: 5, maxPoints: 5, detail: "macro STRONGLY_SUPPORTIVE" }],
    adjustments: [], hardBlockers: [], missingGates: ["RISK_GATE_MISSING", "NEWS_GATE_MISSING"], warnings: [],
    evidenceFor: [], evidenceAgainst: [], devilsAdvocate: [], plan: null, risk: null, news: null, macro: macro(),
    authority: "NOT_AUTHORIZED", strategyVersion: "0.20.0-phase20", generatedAt: T, ...over,
  };
}

describe("evaluation with macro", () => {
  it("accepts a scored macro factor only with trusted macro data for the same direction", () => {
    expect(reconcileEvaluation("XAUUSD", evaluation()).status).toBe("READY");
    for (const m of [macro({ isSynthetic: true }), unavailable, macro({ direction: "BEARISH", state: "STRONG_CONFLICT" }), null]) {
      const r = reconcileEvaluation("XAUUSD", evaluation({ macro: m }));
      expect(r.status === "UNAVAILABLE" && r.reason).toContain("macro scored without trusted macro data");
    }
    expect(reconcileEvaluation("XAUUSD", evaluation({ macro: macro({ bias: "BEARISH" }) })).status).toBe("UNAVAILABLE");
  });
});

describe("MACRO tab and status bar", () => {
  it("shows bias, state, drivers and correlation above the news gate", () => {
    const decision = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T));
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted macro={{ status: "READY", macro: macro() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "MACRO" }));
    expect(screen.getByTestId("macro-bias").textContent).toBe("BULLISH (score +0.75) · STRONGLY_SUPPORTIVE vs the BULLISH setup");
    expect(screen.getByTestId("macro-correlation").textContent).toBe("DXY ALIGNED (r -0.41)");
    const drivers = screen.getByTestId("macro-drivers").textContent ?? "";
    expect(drivers).toContain("US10Y (for US10Y_REAL)");
    expect(drivers).toContain("NOT EVALUATED");
    expect(screen.getByTestId("macro-section").textContent).toContain("never blocks");
  });

  it("explains unavailable, rejected and synthetic macro", () => {
    render(<MacroSection macro={{ status: "READY", macro: unavailable }} />);
    expect(screen.getByTestId("macro-not-available").textContent).toContain("MACRO_PROVIDER");
    cleanup();
    render(<MacroSection macro={{ status: "UNAVAILABLE", reason: "Rejected macro: macro score out of range" }} />);
    expect(screen.getByTestId("macro-unavailable").textContent).toContain("out of range");
    cleanup();
    render(<MacroSection macro={{ status: "READY", macro: macro({ isSynthetic: true, direction: null, state: null }) }} />);
    expect(screen.getByTestId("macro-data").textContent).toContain("SYNTHETIC (not scored)");
    expect(screen.getByTestId("macro-bias").textContent).toContain("no open setup direction");
  });

  it("status bar reads the decision macro state and never assumes one", () => {
    const decision = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T));
    expect(macroLabel(readMacroState(decision))).toBe("UNAVAILABLE");
    const withMacro = {
      ...decision,
      macroState: { authority: "CONTEXT_ONLY", state: "CONFLICT", bias: "BEARISH", score: -0.3, available: true, synthetic: true, correlationRegime: null },
    };
    render(<StatusBar decision={withMacro} data={null} chart={null} />);
    expect(screen.getByTestId("macro").textContent).toContain("BEARISH · CONFLICT · synthetic");
    expect(readMacroState({ ...decision, macroState: { bias: "BULLISH", available: true } })).toBeNull(); // no CONTEXT_ONLY
  });
});
