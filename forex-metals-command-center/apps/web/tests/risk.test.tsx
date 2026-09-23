// @vitest-environment jsdom
import type { DecisionEvaluation, EntryPlan, RiskAssessment } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { RiskTab } from "../src/components/RiskTab";
import { StatusBar } from "../src/components/StatusBar";
import { reconcileEvaluation } from "../src/lib/evaluation";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildCalculationRequest, calculateRisk, EMPTY_FORM, reconcileRisk, riskError, type CalculatorForm } from "../src/lib/risk";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const T = "2024-01-09T16:00:00Z";

function risk(over: Partial<RiskAssessment> = {}): RiskAssessment {
  return {
    symbol: "XAUUSD",
    status: "WITHIN_LIMITS",
    profile: "STANDARD",
    currency: "USD",
    profileError: null,
    limits: {
      riskPerTradePct: 1, dailyRiskLimitPct: 3, weeklyRiskLimitPct: 6, maxOpenRiskPct: 2, maxTradesPerDay: 3, maxPositions: 2,
      maxConsecutiveLosses: 3,
    },
    budget: {
      riskPerTradeAmount: 100, dailyRemaining: 300, weeklyRemaining: 600, openRisk: 0, openRiskRemaining: 200, propRemaining: null,
      effectiveRiskAmount: 100,
    },
    locks: [],
    warnings: ["USER_SUPPLIED_SPEC", "NEWS_NOT_EVALUATED"],
    volatility: { timeframe: "M15", atr: 1.1, baselineAtr: 1, ratio: 1.1, lockRatio: 2.5 },
    position: {
      direction: "BULLISH", entry: 2032, stop: 2028.8, priceDistance: 3.2, points: 320, pips: 32, spread: 0.3, sizingDistance: 3.5,
      riskPerVolume: 350, volume: 0.28, minVolume: 0.01, volumeStep: 0.01, riskAmount: 98, riskAmountWithoutSpread: 89.6, riskPct: 0.98,
      marginRequired: 568.96, detail: "0.28 volume risks 98.00 USD at stop",
    },
    sizeStatus: "SIZED_FROM_USER_SPEC",
    blockers: [],
    news: "NOT_EVALUATED",
    authority: "NOT_AUTHORIZED",
    strategyVersion: "0.20.0-phase20",
    generatedAt: T,
    ...over,
  };
}

const notConfigured = risk({
  status: "NOT_CONFIGURED", profile: null, currency: null, limits: null, budget: null, volatility: null, position: null, sizeStatus: null,
  warnings: ["NEWS_NOT_EVALUATED"], blockers: ["RISK_PROFILE_MISSING"], profileError: "RISK_PROFILE_PATH is not set",
});
const locked = risk({
  status: "LOCKED", locks: [{ lock: "DAILY_LOSS_LIMIT", detail: "loss today 310.00 reach the daily limit" }], blockers: ["RISK_LOCKED"],
  position: { ...risk().position!, volume: null, riskAmount: null, riskPct: null, detail: "no risk budget left" },
  budget: { ...risk().budget!, effectiveRiskAmount: 0 },
});

describe("reconcileRisk", () => {
  it("accepts sized, unconfigured and locked assessments", () => {
    for (const r of [risk(), notConfigured, locked, risk({ status: "CLEAR", position: null, sizeStatus: null })]) {
      expect(riskError("xauusd", r)).toBeNull();
    }
  });

  const r = risk();
  it.each([
    ["authority claimed", { ...r, authority: "AUTHORIZED" }],
    ["unknown news state", { ...r, news: "QUIET" }],
    ["LOCKED without locks", { ...r, status: "LOCKED", blockers: ["RISK_LOCKED"] }],
    ["locks on a clean status", { ...r, locks: locked.locks }],
    ["a lock without its blocker", { ...locked, blockers: [] }],
    ["sized risk above the budget", { ...r, position: { ...r.position!, riskAmount: 150 } }],
    ["risk % above the limit", { ...r, position: { ...r.position!, riskPct: 1.5 } }],
    ["sized without a spec", { ...r, sizeStatus: "POSITION_SIZE_UNVERIFIED" }],
    ["unverified size with a volume", { ...r, status: "SIZE_UNVERIFIED", blockers: ["POSITION_SIZE_UNVERIFIED"] }],
    ["unconfigured with a budget", { ...notConfigured, budget: r.budget }],
    ["unknown lock", { ...locked, locks: [{ lock: "GO_ALL_IN", detail: "" }] }],
    ["effective above per-trade", { ...r, budget: { ...r.budget!, effectiveRiskAmount: 200 } }],
    ["symbol mismatch", { ...r, symbol: "XAGUSD" }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileRisk("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});

const plan: EntryPlan = {
  model: "M15_CLOSE", mode: "STANDARD", direction: "BULLISH", confirmedAt: T, zoneId: "FVG:x", entry: 2032, stop: 2028.8, risk: 3.2,
  tp1: 2040, tp2: null, tp3: null, rr1: 2.5, rr2: null, rr3: null, minRr: 2, researchOnly: false, detail: "",
};

function evaluation(over: Partial<DecisionEvaluation> = {}): DecisionEvaluation {
  return {
    symbol: "XAUUSD", asOf: T, eligibleForDecision: true, ineligibility: [], outcome: "CONFIRMED_PENDING_GATES", direction: "BULLISH",
    setupId: "s", setupType: "LIQUIDITY_SWEEP_MSS", setupState: "BLOCKED", score: 85, evaluatedMax: 95, grade: "A", confidence: "MODERATE",
    conflictScore: 0, dataQualityScore: 100, components: [], adjustments: [], hardBlockers: [], missingGates: ["NEWS_GATE_MISSING"],
    warnings: [], evidenceFor: [], evidenceAgainst: [], devilsAdvocate: [], plan, risk: risk(), news: null, macro: null, authority: "NOT_AUTHORIZED",
    strategyVersion: "0.20.0-phase20", generatedAt: T, ...over,
  };
}

describe("evaluation with the risk gate", () => {
  it("accepts a sized pending plan and a risk-vetoed NO_TRADE plan", () => {
    expect(reconcileEvaluation("XAUUSD", evaluation()).status).toBe("READY");
    expect(reconcileEvaluation("XAUUSD", evaluation({ outcome: "NO_TRADE", risk: locked, hardBlockers: ["RISK_LOCKED"] })).status).toBe("READY");
  });
  const e = evaluation();
  it.each([
    ["NO_TRADE plan without a risk lock", { ...e, outcome: "NO_TRADE" }],
    ["pending plan while risk is locked", { ...e, risk: locked }],
    ["risk present but its gate listed missing", { ...e, missingGates: ["RISK_GATE_MISSING", "NEWS_GATE_MISSING"] }],
    ["no risk and no missing risk gate", { ...e, risk: null }],
    ["the news gate claimed", { ...e, missingGates: [] }],
    ["an untrusted nested risk", { ...e, risk: { ...risk(), authority: "AUTHORIZED" } }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileEvaluation("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});

const filled: CalculatorForm = {
  ...EMPTY_FORM, entry: "2032", stop: "2028.8", balance: "10000", leverage: "100", contractSize: "100", tickSize: "0.01", tickValue: "1",
  minVolume: "0.01", volumeStep: "0.01", typicalSpread: "0.3", platformPipSize: "0.1",
};

describe("buildCalculationRequest", () => {
  it("builds a request with a full spec, or none when the spec is empty", () => {
    const full = buildCalculationRequest("xauusd", filled);
    expect(full.ok && full.request.instrumentSpec?.contractSize).toBe(100);
    expect(full.ok && full.request).toMatchObject({ symbol: "XAUUSD", entry: 2032, stop: 2028.8, state: null, account: { balance: 10000, leverage: 100 } });
    const empty = buildCalculationRequest("XAUUSD", { ...EMPTY_FORM, entry: "2032", stop: "2028.8", balance: "10000" });
    expect(empty.ok && empty.request.instrumentSpec).toBeNull();
  });

  it.each([
    ["missing entry", { ...filled, entry: "" }, "required"],
    ["negative stop", { ...filled, stop: "-1" }, "positive"],
    ["partial spec", { ...filled, tickValue: "" }, "every contract spec field"],
    ["custom profile", { ...filled, profile: "CUSTOM" as const }, "preset"],
    ["bad currency", { ...filled, currency: "US" }, "3-letter"],
    ["bad leverage", { ...filled, leverage: "abc" }, "Leverage"],
    ["negative spread", { ...filled, typicalSpread: "-0.1" }, "Spread"],
  ])("rejects %s", (_n, form, message) => {
    const r = buildCalculationRequest("XAUUSD", form);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.error).toContain(message);
  });
});

describe("calculateRisk", () => {
  it("POSTs the request and validates the response", async () => {
    const built = buildCalculationRequest("XAUUSD", filled);
    if (!built.ok) throw new Error(built.error);
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(risk()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await calculateRisk("http://api", built.request, fetcher)).status).toBe("READY");
    expect(calls[0]!.url).toBe("http://api/api/v1/risk/calculate");
    expect(calls[0]!.init.method).toBe("POST");
    expect(JSON.parse(String(calls[0]!.init.body)).account.balance).toBe(10000);

    const rejected = (async () => new Response("{}", { status: 422 })) as unknown as typeof fetch;
    expect(await calculateRisk("http://api", built.request, rejected)).toEqual({
      status: "UNAVAILABLE", reason: "Risk calculator rejected the request (HTTP 422)",
    });
    const lying = (async () => new Response(JSON.stringify({ ...risk(), authority: "AUTHORIZED" }), { status: 200 })) as unknown as typeof fetch;
    expect((await calculateRisk("http://api", built.request, lying)).status).toBe("UNAVAILABLE");
    const down = (async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    expect((await calculateRisk("http://api", built.request, down)).status).toBe("UNAVAILABLE");
  });
});

describe("RISK tab", () => {
  const decision = { ...unavailableDecision("XAUUSD", ["RISK_PROFILE_MISSING"], "x", new Date(T), "0.20.0-phase20"), riskStatus: "LOCKED" };

  it("is enabled and shows the live assessment", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted evaluation={{ status: "READY", evaluation: evaluation() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "RISK" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("risk-authority").textContent).toContain("NOT AUTHORIZED");
    expect(screen.getByTestId("risk-live-status").textContent).toBe("WITHIN_LIMITS");
    expect(screen.getByTestId("risk-live-volume").textContent).toContain("0.28");
    expect(screen.getByTestId("risk-live-budget").textContent).toContain("100.00 USD");
    expect(screen.getByTestId("risk-live-position").textContent).toContain("320 · 32");
  });

  it("explains how to configure a missing profile and lists locks", () => {
    render(<RiskTab symbol="XAUUSD" evaluation={{ status: "READY", evaluation: evaluation({ plan: null, outcome: "WAIT", risk: notConfigured }) }} />);
    expect(screen.getByTestId("risk-live-setup").textContent).toContain("RISK_PROFILE_PATH");
    cleanup();
    render(<RiskTab symbol="XAUUSD" evaluation={{ status: "READY", evaluation: evaluation({ outcome: "NO_TRADE", risk: locked }) }} />);
    expect(screen.getByTestId("risk-live-locks").textContent).toContain("DAILY_LOSS_LIMIT");
    cleanup();
    render(<RiskTab symbol="XAUUSD" evaluation={{ status: "UNAVAILABLE", reason: "Evaluation API unreachable" }} />);
    expect(screen.getByTestId("risk-unavailable").textContent).toContain("unreachable");
  });

  it("opens from the #risk deep link", () => {
    window.location.hash = "#risk";
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted evaluation={null} />);
    expect(screen.getByTestId("risk-tab")).toBeTruthy();
  });

  it("calculator shows form errors, calls the API and stores nothing", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const fetcher = vi.fn(async () => new Response(JSON.stringify(risk()), { status: 200 })) as unknown as typeof fetch;
    render(<RiskTab symbol="XAUUSD" evaluation={null} fetcher={fetcher} />);
    fireEvent.submit(screen.getByTestId("risk-form"));
    expect(screen.getByTestId("risk-form-error").textContent).toContain("required");
    for (const [name, value] of Object.entries({ entry: "2032", stop: "2028.8", balance: "10000" })) {
      fireEvent.change(screen.getByRole("textbox", { name: new RegExp(`^${name}`, "i") }), { target: { value } });
    }
    fireEvent.submit(screen.getByTestId("risk-form"));
    await waitFor(() => expect(screen.getByTestId("risk-calc-volume").textContent).toContain("0.28"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("status bar shows the decision's risk status", () => {
    render(<StatusBar decision={decision} data={null} chart={null} />);
    expect(screen.getByText("LOCKED")).toBeTruthy();
  });
});
