// @vitest-environment jsdom
import type { DecisionEvaluation, EntryPlan, EventView, NewsAssessment, RiskAssessment } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { NewsTab } from "../src/components/NewsTab";
import { StatusBar } from "../src/components/StatusBar";
import type { ChartState } from "../src/lib/candles";
import { reconcileEvaluation } from "../src/lib/evaluation";
import { unavailableDecision } from "../src/lib/failsafe";
import { buildNewsOverlay, newsLabel, readNewsState, reconcileNews } from "../src/lib/news";

afterEach(cleanup);

const T = "2024-04-18T15:01:00Z";

function event(over: Partial<EventView> = {}): EventView {
  return {
    id: "US-CPI", country: "United States", currency: "USD", name: "CPI m/m", scheduledTime: "2024-04-18T15:05:00Z", importance: "HIGH",
    status: "IMMINENT", actual: null, forecast: "0.3%", previous: "0.4%", revisedPrevious: null, surprise: null, surprisePct: null,
    blackoutStart: "2024-04-18T14:50:00Z", blackoutEnd: "2024-04-18T15:20:00Z", minutesToEvent: 4, ...over,
  };
}

function news(over: Partial<NewsAssessment> = {}): NewsAssessment {
  return {
    symbol: "XAUUSD", state: "BLACKOUT", relevantCurrencies: ["USD"], activeEvent: event(), nextEvent: event(),
    windowStart: "2024-04-18T14:50:00Z", windowEnd: "2024-04-18T15:20:00Z",
    events: [event(), event({ id: "US-PPI", name: "PPI m/m", scheduledTime: "2024-04-18T12:30:00Z", status: "COMPLETED", actual: "0.5%", surprise: 0.2, surprisePct: 66.67, minutesToEvent: -151 })],
    calendar: { provider: "file", source: "test", isSynthetic: false, available: true, fetchedAt: T, coverageStart: T, coverageEnd: T, reason: null },
    blockers: ["NEWS_BLACKOUT"], warnings: [], strategyVersion: "0.19.0-phase19", generatedAt: T, ...over,
  };
}

describe("reconcileNews", () => {
  it("accepts consistent news", () => {
    expect(reconcileNews("xauusd", news()).status).toBe("READY");
    const unavailable = news({ state: "UNAVAILABLE", blockers: ["NEWS_DATA_UNAVAILABLE"], events: [], activeEvent: null, nextEvent: null, calendar: { ...news().calendar, available: false, reason: "no calendar" } });
    expect(reconcileNews("XAUUSD", unavailable).status).toBe("READY");
  });

  const n = news();
  it.each([
    ["unreachable", null],
    ["another market", { ...n, symbol: "EURUSD" }],
    ["BLACKOUT without its blocker", { ...n, blockers: [] }],
    ["CLEAR claimed without a calendar", { ...n, state: "CLEAR", blockers: [], calendar: { ...n.calendar, available: false } }],
    ["an unflagged synthetic calendar", { ...n, calendar: { ...n.calendar, isSynthetic: true } }],
    ["an unrelated currency", { ...n, events: [event({ currency: "JPY" })] }],
    ["unknown state", { ...n, state: "QUIET" }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileNews("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});

describe("decision news state, status bar and chart markers", () => {
  const newsState = { state: "BLACKOUT", relevantCurrencies: ["USD"], activeEvent: null, calendarAvailable: true, calendarSynthetic: false,
    nextEvent: { name: "CPI m/m", currency: "USD", importance: "HIGH", scheduledTime: "2024-04-18T15:05:00Z", minutesToEvent: 4 }, windowEnd: null };

  it("labels the news state and never assumes clear", () => {
    const decision = { ...unavailableDecision("XAUUSD", ["NEWS_BLACKOUT"], "x", new Date(T)), newsState };
    expect(newsLabel(readNewsState(decision))).toBe("BLACKOUT · HIGH USD in 4m");
    expect(newsLabel(readNewsState({ ...decision, newsState: { ...newsState, state: "UNAVAILABLE", calendarSynthetic: true } }))).toBe("UNAVAILABLE · synthetic");
    expect(newsLabel(readNewsState({ ...decision, newsState: { state: "WHATEVER" } }))).toBe("UNAVAILABLE");
    render(<StatusBar decision={decision} data={null} chart={null} />);
    expect(screen.getByTestId("news").textContent).toBe("BLACKOUT · HIGH USD in 4m");
  });

  it("places markers for high-impact events inside the drawn candles only", () => {
    const chart: ChartState = {
      status: "READY", symbol: "XAUUSD", timeframe: "M15", quality: "CURRENT", marketStatus: "OPEN", isSynthetic: false, provider: "p", sourceTimeframe: "M15", issues: [],
      candles: ["12:15", "12:30", "12:45", "13:00"].map((h) => ({ time: `2024-04-18T${h}:00Z`, open: 1, high: 2, low: 0.5, close: 1.5, volume: 1, isClosed: true })),
    };
    const overlay = buildNewsOverlay(news({ events: [
      event({ scheduledTime: "2024-04-18T12:37:00Z" }),
      event({ id: "low", importance: "MEDIUM", scheduledTime: "2024-04-18T12:40:00Z" }),
      event({ id: "old", scheduledTime: "2024-04-17T12:40:00Z" }),
    ] }), chart);
    expect(overlay.markers.map((m) => [m.time, m.text])).toEqual([[Date.parse("2024-04-18T12:30:00Z") / 1000, "USD HIGH"]]);
  });
});

describe("MACRO tab news calendar", () => {
  it("is enabled and shows the gate, the calendar and events with surprise", () => {
    const decision = unavailableDecision("XAUUSD", ["NEWS_BLACKOUT"], "x", new Date(T));
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted news={{ status: "READY", news: news() }} />);
    fireEvent.click(screen.getByRole("tab", { name: "MACRO" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("news-state").textContent).toBe("BLACKOUT");
    expect(screen.getByTestId("news-next").textContent).toContain("HIGH USD CPI m/m");
    expect(screen.getByTestId("news-events").textContent).toContain("0.2 (66.67%)");
    expect(screen.getByTestId("news-tab").textContent).toContain("News gate");
  });

  it("shows why news is unavailable", () => {
    render(<NewsTab news={{ status: "UNAVAILABLE", reason: "Rejected news: BLACKOUT without NEWS_BLACKOUT" }} />);
    expect(screen.getByTestId("news-unavailable").textContent).toContain("BLACKOUT without");
  });
});

const plan: EntryPlan = {
  model: "M15_CLOSE", mode: "STANDARD", direction: "BULLISH", confirmedAt: T, zoneId: "z", entry: 2032, stop: 2028.8, risk: 3.2,
  tp1: 2040, tp2: null, tp3: null, rr1: 2.5, rr2: null, rr3: null, minRr: 2, researchOnly: false, detail: "",
};
const risk: RiskAssessment = {
  symbol: "XAUUSD", status: "NOT_CONFIGURED", profile: null, currency: null, profileError: null, limits: null, budget: null, locks: [],
  warnings: [], volatility: null, position: null, sizeStatus: null, blockers: ["RISK_PROFILE_MISSING"], news: "CLEAR", authority: "NOT_AUTHORIZED",
  strategyVersion: "0.19.0-phase19", generatedAt: T,
};
const clear = news({ state: "CLEAR", blockers: [], activeEvent: null, windowStart: null, windowEnd: null });

function evaluation(over: Partial<DecisionEvaluation> = {}): DecisionEvaluation {
  return {
    symbol: "XAUUSD", asOf: T, eligibleForDecision: true, ineligibility: [], outcome: "CONFIRMED_AWAITING_AUTHORITY", direction: "BULLISH",
    setupId: "s", setupType: "LIQUIDITY_SWEEP_MSS", setupState: "BLOCKED", score: 85, evaluatedMax: 95, grade: "A", confidence: "MODERATE",
    conflictScore: 0, dataQualityScore: 100, components: [], adjustments: [], hardBlockers: [], missingGates: [], warnings: [],
    evidenceFor: [], evidenceAgainst: [], devilsAdvocate: [], plan, risk, news: clear, macro: null, authority: "NOT_AUTHORIZED",
    strategyVersion: "0.19.0-phase19", generatedAt: T, ...over,
  };
}

describe("evaluation outcomes with the news gate", () => {
  it("accepts awaiting-authority, pending and blackout-vetoed plans", () => {
    expect(reconcileEvaluation("XAUUSD", evaluation()).status).toBe("READY");
    expect(reconcileEvaluation("XAUUSD", evaluation({ outcome: "CONFIRMED_PENDING_GATES", hardBlockers: ["RISK_PROFILE_MISSING"] })).status).toBe("READY");
    expect(reconcileEvaluation("XAUUSD", evaluation({ outcome: "NO_TRADE", news: news(), hardBlockers: ["NEWS_BLACKOUT"] })).status).toBe("READY");
  });

  const e = evaluation();
  it.each([
    ["awaiting authority with a hard blocker", { ...e, hardBlockers: ["RISK_PROFILE_MISSING"] }],
    ["pending while every gate is clear", { ...e, outcome: "CONFIRMED_PENDING_GATES" }],
    ["a blackout plan not vetoed", { ...e, news: news(), hardBlockers: ["NEWS_BLACKOUT"], outcome: "CONFIRMED_PENDING_GATES" }],
    ["news present but its gate listed missing", { ...e, missingGates: ["NEWS_GATE_MISSING"], outcome: "CONFIRMED_PENDING_GATES" }],
    ["no news and no missing news gate", { ...e, news: null }],
    ["an untrusted nested news payload", { ...e, news: { ...clear, symbol: "EURUSD" } }],
    ["awaiting authority without a plan", { ...e, plan: null }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileEvaluation("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});
