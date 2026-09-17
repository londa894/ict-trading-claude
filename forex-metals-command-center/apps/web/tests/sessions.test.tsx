// @vitest-environment jsdom
import type { ChartCandle, JudasSwing, SessionAnalysis, SessionDecisionState, SessionInstance } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { resolveSessionOverlay } from "../src/components/ChartPanel";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { StatusBar } from "../src/components/StatusBar";
import { loadSessions } from "../src/lib/api";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";
import {
  buildSessionOverlay,
  clockLabel,
  dailyChangeLabel,
  formatChartTime,
  formatTick,
  readSessionState,
  reconcileSessions,
  SESSION_COLORS,
} from "../src/lib/sessions";

afterEach(cleanup);

// Tuesday 2024-01-09 (EST): ASIA 01:00-05:00Z, LONDON 07:00-10:00Z.
const Z = (h: number, m = 0) => new Date(Date.UTC(2024, 0, 9, h, m)).toISOString().replace(".000Z", "Z");

const instance = (over: Partial<SessionInstance> = {}): SessionInstance => ({
  id: "ASIA:2024-01-09",
  session: "ASIA",
  tradingDay: "2024-01-09",
  start: Z(1),
  end: Z(5),
  state: "COMPLETE",
  high: 2035,
  low: 2025,
  midpoint: 2030,
  range: 10,
  highTime: Z(2),
  lowTime: Z(3),
  candleCount: 16,
  expectedCount: 16,
  knownAt: Z(5),
  asianRangeState: "NORMAL",
  asianRangeRatio: 1.02,
  ...over,
});

const judas = (over: Partial<JudasSwing> = {}): JudasSwing => ({
  id: "JUDAS:LONDON:2024-01-09",
  tradingDay: "2024-01-09",
  session: "LONDON",
  direction: "BEARISH",
  status: "CONFIRMED",
  asianHigh: 2035,
  asianLow: 2025,
  asianMidpoint: 2030,
  sweepTime: Z(7, 15),
  sweepExtreme: 2036,
  resolvedAt: Z(8),
  detail: "closed beyond the Asian midpoint",
  ...over,
});

function analysis(over: Partial<SessionAnalysis> = {}): SessionAnalysis {
  return {
    symbol: "XAUUSD",
    sourceTimeframe: "M15",
    asOf: Z(9),
    candleCount: 400,
    quality: "CURRENT",
    isSynthetic: false,
    eligibleForDecision: true,
    ineligibility: [],
    clock: {
      now: Z(8, 30),
      newYorkTime: "2024-01-09T03:30:00-05:00",
      londonTime: "2024-01-09T08:30:00+00:00",
      tradingDay: "2024-01-09",
      marketStatus: "OPEN",
      activeSessions: ["LONDON"],
      activeKillZones: ["LONDON_KZ"],
      timeQuality: "IDEAL",
      nextSession: "NY_AM",
      nextSessionStart: Z(13, 30),
    },
    sessionQuality: "IDEAL",
    instances: [
      instance(),
      instance({
        id: "LONDON:2024-01-09", session: "LONDON", start: Z(7), end: Z(10), state: "FORMING", high: 2036, low: 2028,
        midpoint: 2032, range: 8, candleCount: 8, expectedCount: 12, knownAt: null, asianRangeState: null, asianRangeRatio: null,
      }),
      instance({
        id: "NY_AM:2024-01-09", session: "NY_AM", start: Z(13, 30), end: Z(17), state: "NOT_STARTED", high: null, low: null,
        midpoint: null, range: null, highTime: null, lowTime: null, candleCount: 0, expectedCount: 14, knownAt: null,
        asianRangeState: null, asianRangeRatio: null,
      }),
    ],
    opens: {
      dailyOpen: 2031, dailyOpenTime: Z(0), nyMidnightOpen: 2030, nyMidnightOpenTime: Z(5), weeklyOpen: 2020,
      weeklyOpenTime: Z(0), lastClose: 2033.5, dailyChange: 2.5, dailyChangePct: 0.1231,
    },
    previousSession: { instanceId: "ASIA:2024-01-09", session: "ASIA", high: 2035, low: 2025, end: Z(5) },
    adr: { adr: 20, periodDays: 14, currentRange: 11, pctUsed: 55, expansion: "ACTIVE" },
    judas: [judas()],
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: Z(8, 30),
    ...over,
  };
}

const candles: ChartCandle[] = Array.from({ length: 32 }, (_, i) => ({
  time: new Date(Date.UTC(2024, 0, 9, 0, 0) + i * 15 * 60_000).toISOString().replace(".000Z", "Z"),
  open: 2030, high: 2031, low: 2029, close: 2030.5, volume: 1, isClosed: true,
}));
const chart: ChartState = {
  status: "READY", symbol: "XAUUSD", timeframe: "M15", quality: "CURRENT", marketStatus: "OPEN", isSynthetic: false,
  provider: "p", sourceTimeframe: "M15", candles, issues: [],
};

describe("reconcileSessions", () => {
  it("accepts a valid analysis (and a withheld one: the clock is time-only)", () => {
    expect(reconcileSessions("xauusd", analysis()).status).toBe("READY");
    expect(reconcileSessions("XAUUSD", analysis({ instances: [], judas: [], adr: null, candleCount: 0, quality: "INVALID" })).status).toBe("READY");
  });
  const a = analysis();
  it.each([
    ["unreachable", null],
    ["symbol mismatch", analysis({ symbol: "EURUSD" })],
    ["unknown time quality", analysis({ clock: { ...a.clock, timeQuality: "GREAT" as never } })],
    ["levels before start", { ...a, instances: [instance({ state: "NOT_STARTED", knownAt: null, asianRangeState: null })] }],
    ["complete without all candles", { ...a, instances: [instance({ candleCount: 15 })] }],
    ["knownAt on a forming session", { ...a, instances: [instance({ state: "FORMING", asianRangeState: null })] }],
    ["range state on London", { ...a, instances: [instance({ session: "LONDON", id: "LONDON:2024-01-09" })] }],
    ["low above high", { ...a, instances: [instance({ low: 2040 })] }],
    ["Judas for unknown session", { ...a, judas: [judas({ tradingDay: "2024-01-10" })] }],
    ["unresolved confirmed Judas", { ...a, judas: [judas({ resolvedAt: null })] }],
    ["Judas midpoint outside range", { ...a, judas: [judas({ asianMidpoint: 2040 })] }],
    ["negative open", { ...a, opens: { ...a.opens, dailyOpen: -1 } }],
    ["unknown expansion", { ...a, adr: { ...a.adr!, expansion: "HUGE" as never } }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileSessions("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });
});

describe("session overlay", () => {
  it("draws complete (solid) and forming (dashed) sessions snapped to drawn candles", () => {
    const o = buildSessionOverlay(analysis(), candles);
    expect(o.segments.map((s) => s.id)).toEqual([
      "ASIA:2024-01-09:high", "ASIA:2024-01-09:low", "LONDON:2024-01-09:high", "LONDON:2024-01-09:low",
    ]);
    const [asiaHigh, , londonHigh] = o.segments;
    expect([asiaHigh!.from, asiaHigh!.to]).toEqual([Date.parse(Z(1)) / 1000, Date.parse(Z(4, 45)) / 1000]);
    expect(asiaHigh!.dashed).toBe(false);
    expect(asiaHigh!.color).toBe(SESSION_COLORS.ASIA);
    expect(londonHigh!.dashed).toBe(true);
    expect(londonHigh!.to).toBe(Date.parse(Z(7, 45)) / 1000); // last drawn candle inside the window
  });

  it("never extrapolates before the first drawn candle", () => {
    expect(buildSessionOverlay(analysis(), candles.slice(8)).segments.map((s) => s.id)).toEqual([
      "LONDON:2024-01-09:high", "LONDON:2024-01-09:low",
    ]);
  });

  it("is off by default, hidden on H4/D1 and when the payload is unavailable", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    expect(resolveSessionOverlay(chart, ready, false).overlay).toBeNull();
    expect(resolveSessionOverlay(chart, ready, true).overlay?.segments).toHaveLength(4);
    expect(resolveSessionOverlay({ ...chart, timeframe: "H4" }, ready, true).note).toMatch(/hidden on H4/);
    expect(resolveSessionOverlay(chart, { status: "UNAVAILABLE", reason: "x" }, true).note).toMatch(/Sessions hidden/);
  });
});

describe("time-zone formatting", () => {
  const t = Date.parse("2024-03-11T11:00:00Z") / 1000; // US on EDT, UK still on GMT
  it("formats crosshair and ticks in the chosen zone", () => {
    expect(formatChartTime(t, "UTC")).toBe("11 Mar 2024 11:00");
    expect(formatChartTime(t, "America/New_York")).toBe("11 Mar 2024 07:00");
    expect(formatChartTime(t, "Europe/London")).toBe("11 Mar 2024 11:00");
    expect(formatTick(Date.parse("2024-04-08T11:00:00Z") / 1000, 3, "Europe/London")).toBe("12:00");
    expect(formatTick(t, 2, "America/New_York")).toBe("11 Mar");
  });
});

describe("loadSessions", () => {
  it("requests the sessions endpoint", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(analysis()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadSessions("XAUUSD", fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/sessions\/XAUUSD$/);
  });
});

describe("SESSION tab, status bar and decision state", () => {
  const base = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(Z(9)), "0.19.0-phase19");
  const state: SessionDecisionState = {
    tradingDay: "2024-01-09", marketStatus: "OPEN", activeSessions: ["LONDON"], activeKillZones: ["LONDON_KZ"],
    timeQuality: "IDEAL", sessionQuality: "IDEAL", asianRangeState: "NORMAL", adrPctUsed: 55, expansion: "ACTIVE",
    judas: "LONDON BEARISH CONFIRMED", authority: "CONTEXT_ONLY",
  };
  const decision = { ...base, sessionState: state };

  it("validates the decision's sessionState", () => {
    expect(readSessionState(decision)).not.toBeNull();
    expect(readSessionState({ ...base, sessionState: { ...state, authority: "TRADE" } as never })).toBeNull();
    expect(readSessionState({ ...base, sessionState: { ...state, activeSessions: ["TOKYO"] } as never })).toBeNull();
    expect(readSessionState(base)).toBeNull();
  });

  it("status bar shows the clock and the daily change", () => {
    const ready = { status: "READY" as const, analysis: analysis() };
    render(<StatusBar decision={base} data={null} chart={chart} sessions={ready} />);
    expect(screen.getByTestId("session").textContent).toBe("LONDON + LONDON_KZ · IDEAL");
    expect(screen.getByTestId("daily-change").textContent).toBe("+2.50 (+0.12%)");
    expect(clockLabel(analysis({ clock: { ...analysis().clock, activeSessions: [], activeKillZones: [], marketStatus: "CLOSED", timeQuality: "AVOID" } }))).toBe("CLOSED · AVOID");
    expect(dailyChangeLabel(analysis({ opens: { ...analysis().opens, dailyChange: null } }))).toBeNull();
  });

  it("SESSION tab lists today's sessions, levels, ADR and Judas; overview shows the context", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted sessions={{ status: "READY", analysis: analysis() }} />);
    expect(screen.getByText(/LONDON \+ LONDON_KZ · time IDEAL · session IDEAL · ADR 55% ACTIVE/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "SESSION" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    const rows = screen.getByTestId("session-instances").textContent ?? "";
    expect(rows).toContain("ASIACOMPLETE2025–2035 (range 10)NORMAL");
    expect(rows).toContain("NY_AMNOT_STARTED—");
    expect(screen.getByTestId("session-levels").textContent).toContain("used 55% · ACTIVE");
    expect(screen.getByTestId("session-judas").textContent).toContain("LONDON BEARISH CONFIRMED");
    expect(screen.getByTestId("session-clock").textContent).toContain("03:30 / 08:30");
    expect(screen.getByText(/Time never creates a trade/)).toBeTruthy();
  });

  it("lists the latest data trading day when the clock has moved on (stale data)", () => {
    const stale = analysis({ clock: { ...analysis().clock, tradingDay: "2026-09-14" } });
    render(<IntelligencePanel decision={base} data={null} status={null} trusted sessions={{ status: "READY", analysis: stale }} />);
    fireEvent.click(screen.getByRole("tab", { name: "SESSION" }));
    expect(screen.getByText("Sessions · trading day 2024-01-09 (latest in data)")).toBeTruthy();
    expect(screen.getByTestId("session-instances").textContent).toContain("ASIACOMPLETE");
  });

  it("fails safe when the payload is unavailable", () => {
    render(<IntelligencePanel decision={base} data={null} status={null} trusted={false} sessions={{ status: "UNAVAILABLE", reason: "Session API unreachable" }} />);
    fireEvent.click(screen.getByRole("tab", { name: "SESSION" }));
    expect(screen.getByTestId("session-unavailable").textContent).toContain("Session API unreachable");
  });
});
