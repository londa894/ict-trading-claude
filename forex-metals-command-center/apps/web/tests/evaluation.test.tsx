// @vitest-environment jsdom
import type { DecisionEvaluation, EntryPlan, Setup, SetupAnalysis } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { StatusBar } from "../src/components/StatusBar";
import { loadEvaluation } from "../src/lib/api";
import { reconcileEvaluation, gradeFor, scoreLabel } from "../src/lib/evaluation";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildSetupOverlay, reconcileSetups } from "../src/lib/setups";

afterEach(cleanup);

const T = "2024-01-09T14:15:00Z";

const plan = (over: Partial<EntryPlan> = {}): EntryPlan => ({
  model: "M15_CLOSE", mode: "STANDARD", direction: "BULLISH", confirmedAt: T, zoneId: "FVG:x", entry: 2032, stop: 2028.8,
  risk: 3.2, tp1: 2040, tp2: 2046, tp3: null, rr1: 2.5, rr2: 4.38, rr3: null, minRr: 2, researchOnly: false, detail: "", ...over,
});

function evaluation(over: Partial<DecisionEvaluation> = {}): DecisionEvaluation {
  return {
    symbol: "XAUUSD",
    asOf: T,
    eligibleForDecision: true,
    ineligibility: [],
    outcome: "CONFIRMED_PENDING_GATES",
    direction: "BULLISH",
    setupId: "SETUP:BULLISH:a",
    setupType: "LIQUIDITY_SWEEP_MSS",
    setupState: "BLOCKED",
    score: 85,
    evaluatedMax: 95,
    grade: "A",
    confidence: "MODERATE",
    conflictScore: 10,
    dataQualityScore: 100,
    components: [
      { factor: "HTF", status: "EVALUATED", points: 15, maxPoints: 15, detail: "aligned" },
      { factor: "MACRO", status: "NOT_EVALUATED", points: 0, maxPoints: 5, detail: "macro not wired" },
    ],
    adjustments: [{ name: "CORRELATED_EVIDENCE", points: -5, detail: "same candle" }],
    hardBlockers: ["INSTRUMENT_SPEC_MISSING"],
    missingGates: ["RISK_GATE_MISSING", "NEWS_GATE_MISSING"],
    warnings: [],
    evidenceFor: ["HTF: aligned"],
    evidenceAgainst: ["Time quality is LOW_QUALITY"],
    devilsAdvocate: ["News is not evaluated: a blackout could apply (Phase 13)"],
    plan: plan(),
    risk: null,
    news: null,
    macro: null,
    authority: "NOT_AUTHORIZED",
    strategyVersion: "0.20.0-phase20",
    generatedAt: T,
    ...over,
  };
}

describe("reconcileEvaluation", () => {
  it("accepts a valid confirmed-pending-gates evaluation and a no-setup one", () => {
    expect(reconcileEvaluation("xauusd", evaluation()).status).toBe("READY");
    expect(
      reconcileEvaluation("XAUUSD", evaluation({ outcome: "WAIT", plan: null, setupState: null, score: null, grade: null, components: [] })).status,
    ).toBe("READY");
  });
  const e = evaluation();
  it.each([
    ["unreachable", null],
    ["authority claimed", { ...e, authority: "AUTHORIZED" }],
    ["confidence above the cap", { ...e, confidence: "HIGH" }],
    ["LONG outcome", { ...e, outcome: "LONG" }],
    ["grade not matching score", { ...e, grade: "A+" }],
    ["score out of range", { ...e, score: 120, grade: "A+" }],
    ["confirmed without a plan", { ...e, plan: null }],
    ["plan with a WAIT outcome", { ...e, outcome: "WAIT" }],
    ["component points above max", { ...e, components: [{ factor: "HTF", status: "EVALUATED", points: 20, maxPoints: 15, detail: "" }] }],
    ["unknown warning", { ...e, warnings: ["ENTER_NOW"] }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileEvaluation("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });

  it("grades like the backend", () => {
    expect([90, 89.9, 70, 60, 59.9].map(gradeFor)).toEqual(["A+", "A", "B", "C", "D"]);
    expect(scoreLabel(85, "A")).toBe("85 (A)");
    expect(scoreLabel(null, null)).toBeNull();
  });
});

describe("ENTRY tab and status bar", () => {
  const decision = { ...unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T), "0.20.0-phase20"), setupScore: 85, setupGrade: "A" };

  it("shows NOT AUTHORIZED, the plan, the breakdown, evidence and the devil's advocate", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted evaluation={{ status: "READY", evaluation: evaluation() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "ENTRY" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("entry-authority").textContent).toContain("NOT AUTHORIZED · missing gates: RISK_GATE_MISSING, NEWS_GATE_MISSING");
    const planText = screen.getByTestId("entry-plan").textContent ?? "";
    expect(planText).toContain("2032 / 2028.8 (risk 3.2)");
    expect(planText).toContain("2040 / 2046 / —");
    const breakdown = screen.getByTestId("entry-components").textContent ?? "";
    expect(breakdown).toContain("HTF15/15");
    expect(breakdown).toContain("MACROn/e");
    expect(breakdown).toContain("CORRELATED_EVIDENCE-5");
    expect(screen.getByTestId("entry-evidence").textContent).toContain("− Time quality is LOW_QUALITY");
    expect(screen.getByTestId("entry-devils-advocate").textContent).toContain("blackout");
    expect(screen.getByText(/a ranking, not a/)).toBeTruthy();
  });

  it("research-only plans are flagged and unavailable payloads fail safe", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted
      evaluation={{ status: "READY", evaluation: evaluation({ plan: plan({ model: "LIMIT_RESEARCH", researchOnly: true }) }) }} />);
    fireEvent.click(screen.getByRole("tab", { name: "ENTRY" }));
    expect(screen.getByTestId("entry-plan").textContent).toContain("RESEARCH ONLY");
    cleanup();
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false} evaluation={{ status: "UNAVAILABLE", reason: "Evaluation API unreachable" }} />);
    fireEvent.click(screen.getByRole("tab", { name: "ENTRY" }));
    expect(screen.getByTestId("entry-unavailable").textContent).toContain("Evaluation API unreachable");
  });

  it("status bar shows score and grade from the Master Decision", () => {
    render(<StatusBar decision={decision} data={null} chart={null} />);
    expect(screen.getByTestId("score").textContent).toBe("85 (A)");
  });
});

describe("loadEvaluation", () => {
  it("requests the evaluation endpoint", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(evaluation()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadEvaluation("XAUUSD", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/evaluation\/XAUUSD$/);
  });
});

describe("setup payloads with Phase 8 states", () => {
  const blocked: Setup = {
    id: "SETUP:BULLISH:a", setupType: "LIQUIDITY_SWEEP_MSS", direction: "BULLISH", state: "BLOCKED", terminal: false,
    tradingDay: "2024-01-09", discoveredAt: T, stateChangedAt: T, target: { poolId: "p", poolType: "PDH", label: "PDH", price: 2040 },
    liquidityEvent: null, mss: null, protectiveLevel: 2028.9, zoneIds: ["FVG:x"], touchedZoneId: "FVG:x", reason: null,
    nextRequiredEvent: "risk (Phase 9) and news (Phase 13) gates", entryPlan: plan(),
    steps: [
      { step: "LTF_CONFIRMATION", status: "DONE", detail: "M15_CLOSE @ 2032" },
      { step: "RISK", status: "NOT_EVALUATED", detail: "Phase 9" },
    ],
  };
  const missed: Setup = { ...blocked, id: "SETUP:BULLISH:old", state: "ENTRY_MISSED", terminal: true, reason: "R:R 1.2 below 2.0", entryPlan: null };
  const analysis: SetupAnalysis = {
    symbol: "XAUUSD", timeframe: "M15", asOf: T, candleCount: 300, quality: "CURRENT", isSynthetic: false, eligibleForDecision: true,
    ineligibility: [], bias: { direction: "BULLISH", timeframes: ["H4", "H1"], latest: [] }, currentState: "BLOCKED", current: blocked,
    setups: [missed, blocked],
    events: [
      { id: "m", setupId: missed.id, direction: "BULLISH", state: "ENTRY_MISSED", time: T, price: 2032, detail: "" },
      { id: "b", setupId: blocked.id, direction: "BULLISH", state: "BLOCKED", time: T, price: 2032, detail: "" },
    ],
    po3: { tradingDay: null, phase: "UNCLEAR", dailyOpen: null, adr: null, detail: "" }, providerError: null,
    strategyVersion: "0.20.0-phase20", generatedAt: T,
  };

  it("accepts BLOCKED (with a plan) and ENTRY_MISSED, and draws not-authorized plan lines", () => {
    expect(reconcileSetups("XAUUSD", analysis).status).toBe("READY");
    const o = buildSetupOverlay(analysis);
    expect(o.markers.map((m) => m.text)).toEqual(["Entry missed", "Confirmed (pending gates)"]);
    expect(o.priceLines.map((p) => p.title)).toEqual([
      "Setup DOL PDH", "Setup invalidation", "Plan entry (not authorized)", "Plan stop (not authorized)", "Plan TP2 (not authorized)",
    ]);
  });

  it("still rejects authority states", () => {
    const ready = { ...blocked, state: "LONG_READY" as const };
    expect(reconcileSetups("XAUUSD", { ...analysis, setups: [missed, ready], current: ready, currentState: "LONG_READY" }).status).toBe("UNAVAILABLE");
  });
});
