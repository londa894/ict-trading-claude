// @vitest-environment jsdom
import type { AnalyticsReport, GroupStats } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsApi } from "../src/components/AnalyticsSection";
import { JournalView, type JournalApi } from "../src/components/JournalView";
import { viewForHash } from "../src/components/MarketsViews";
import { reconcileAnalytics, sampleLabel } from "../src/lib/analytics";

afterEach(cleanup);

const T = "2024-04-18T15:01:00Z";

function group(key: string, over: Partial<GroupStats> = {}): GroupStats {
  return {
    key, count: 4, label: "INSUFFICIENT", wins: 2, losses: 1, breakeven: 1, winRate: 0.5, rCount: 4, avgR: 1.0125, totalR: 4.05,
    avgWinR: 2.5, avgLossR: -0.475, expectancyR: 1.0125, profitFactor: 5.05, ...over,
  };
}

function report(over: Partial<AnalyticsReport> = {}): AnalyticsReport {
  return {
    filters: { source: "JOURNAL", symbol: null, start: null, end: null, includeSynthetic: false, strategyVersion: null },
    available: true, reason: null, overall: group("ALL"),
    drawdown: { maxDrawdownR: 1, peakAt: T, troughAt: T, recoveredAt: T, recoveryTrades: 1 },
    avgDurationMinutes: 30, medianDurationMinutes: 30, avgMfeR: 1.2, avgMaeR: -0.4, avgEntryEfficiency: 0.8, avgExitEfficiency: 0.7,
    breakdowns: [{ dimension: "ASSET", groups: [group("XAUUSD")], best: null, bestReason: "fewer than two groups to compare" }],
    process: { classifications: { BAD_PROCESS_WIN: 2, PROCESS_ERROR: 2 }, violationCounts: { NO_CONFIRMED_PLAN: 4 }, withViolations: group("WITH_VIOLATIONS"), withoutViolations: group("WITHOUT_VIOLATIONS", { count: 0, wins: 0, losses: 0, breakeven: 0, winRate: null, rCount: 0, avgR: null, totalR: null, avgWinR: null, avgLossR: null, expectancyR: null, profitFactor: null }) },
    dol: { evaluated: 0, reached: 0, rate: null, label: "INSUFFICIENT", aligned: group("ALIGNED_WITH_DOL", { count: 0, wins: 0, losses: 0, breakeven: 0, winRate: null }), opposed: group("AGAINST_DOL", { count: 0, wins: 0, losses: 0, breakeven: 0, winRate: null }), unknown: 4, detail: "" },
    alertUsefulness: { available: false, reason: "alerts are kept in memory and are not linked to journal or paper records yet" },
    decisionRecords: { NO_TRADE: 1, MISSED_ENTRY: 1 },
    excluded: { tampered: 0, synthetic: 0, notClosed: 1, filtered: 0 },
    includesSynthetic: false, strategyVersions: ["0.20.0-phase20"],
    equityCurve: [{ at: T, cumulativeR: 2 }, { at: T, cumulativeR: 1 }, { at: T, cumulativeR: 4.05 }],
    disclaimer: "Recorded history only. Not a probability, forecast, guarantee or trade signal; small samples are labelled.",
    authority: "DESCRIPTIVE_ONLY", strategyVersion: "0.20.0-phase20", generatedAt: T, ...over,
  };
}

describe("analytics trust rules", () => {
  it("mirrors the spec sample-size labels", () => {
    expect([0, 29, 30, 99, 100, 299, 300].map(sampleLabel)).toEqual(["INSUFFICIENT", "INSUFFICIENT", "LIMITED", "LIMITED", "MODERATE", "MODERATE", "STRONGER_EVIDENCE"]);
  });

  it("accepts a consistent report", () => {
    expect(reconcileAnalytics(report()).status).toBe("READY");
  });

  it.each([
    [report({ authority: "AUTHORIZED" }), "claimed authority"],
    [report({ disclaimer: "" }), "disclaimer"],
    [report({ overall: group("ALL", { label: "MODERATE" }) }), "sample label"],
    [report({ overall: group("ALL", { wins: 3 }) }), "do not add up"],
    [report({ overall: group("ALL", { winRate: 0.9 }) }), "win rate"],
    [report({ breakdowns: [{ dimension: "ASSET", groups: [group("XAUUSD"), group("EURUSD")], best: "XAUUSD", bestReason: "x" }] }), "insufficient sample"],
    [report({ available: false }), "unavailable store"],
    [null, "unreachable"],
  ])("rejects %#", (payload, reason) => {
    const r = reconcileAnalytics(payload);
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });
});

const journalApi: JournalApi = {
  list: vi.fn(async () => ({ status: "READY" as const, list: { entries: [], nextCursor: null, generatedAt: T, store: { available: true, backend: "database:sqlite", reason: null } } })),
  get: vi.fn(), create: vi.fn(), outcome: vi.fn(), remove: vi.fn(), confirm: () => true,
};

describe("Analytics tab", () => {
  it("is reachable from #analytics and shows labelled statistics with the disclaimer", async () => {
    expect(viewForHash("#analytics")).toBe("JOURNAL");
    const api: AnalyticsApi = { load: vi.fn(async () => ({ status: "READY" as const, report: report() })) };
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} analyticsApi={api} initialTab="ANALYTICS" />);
    expect((await screen.findByTestId("analytics-disclaimer")).textContent).toContain("Not a probability");
    const overall = screen.getByTestId("analytics-overall").textContent ?? "";
    expect(overall).toContain("INSUFFICIENT");
    expect(overall).toContain("win rate 50.0%");
    expect(overall).toContain("Alert usefulnessUNAVAILABLE");
    expect(screen.getByTestId("analytics-drawdown").textContent).toContain("recovered after 1 records");
    expect(screen.getByTestId("analytics-ASSET").textContent).toContain("best: none (fewer than two groups to compare)");
    expect(screen.getByTestId("analytics-equity")).toBeTruthy();
  });

  it("reloads with the source, symbol and synthetic filters", async () => {
    const load = vi.fn(async () => ({ status: "READY" as const, report: report() }));
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} analyticsApi={{ load }} initialTab="ANALYTICS" />);
    await screen.findByTestId("analytics-overall");
    fireEvent.change(screen.getByLabelText("Analytics source"), { target: { value: "PAPER" } });
    await waitFor(() => expect(load).toHaveBeenLastCalledWith({ source: "PAPER", symbol: undefined, includeSynthetic: false }));
    fireEvent.click(screen.getByLabelText(/XAUUSD only/));
    fireEvent.click(screen.getByLabelText(/include synthetic data/));
    await waitFor(() => expect(load).toHaveBeenLastCalledWith({ source: "PAPER", symbol: "XAUUSD", includeSynthetic: true }));
  });

  it("explains unavailable and rejected reports", async () => {
    const unavailable = report({ available: false, reason: "no journal store is configured (JOURNAL_STORE): nothing is saved", overall: group("ALL", { count: 0, wins: 0, losses: 0, breakeven: 0, winRate: null }) });
    const { unmount } = render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} analyticsApi={{ load: async () => ({ status: "READY", report: unavailable }) }} initialTab="ANALYTICS" />);
    expect((await screen.findByTestId("analytics-unavailable")).textContent).toContain("JOURNAL_STORE");
    unmount();
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} analyticsApi={{ load: async () => reconcileAnalytics(report({ authority: "AUTHORIZED" })) }} initialTab="ANALYTICS" />);
    expect((await screen.findByTestId("analytics-unavailable")).textContent).toContain("claimed authority");
  });
});
