// @vitest-environment jsdom
import type { BacktestListResponse, BacktestRun, GroupStats, VariantResult } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestView, type BacktestApi } from "../src/components/BacktestView";
import { viewForHash } from "../src/components/MarketsViews";
import { backtestRequest, reconcileBacktestList, reconcileBacktestRun, type BacktestForm } from "../src/lib/backtest";

afterEach(cleanup);

const T = "2024-04-18T15:01:00Z";

const stats = (over: Partial<GroupStats> = {}): GroupStats => ({
  key: "ALL", count: 2, label: "INSUFFICIENT", wins: 1, losses: 1, breakeven: 0, winRate: 0.5, rCount: 2, avgR: 0.5, totalR: 1,
  avgWinR: 2, avgLossR: -1, expectancyR: 0.5, profitFactor: 2, ...over,
});

function variant(over: Partial<VariantResult> = {}): VariantResult {
  return {
    variant: { name: "A", entryMode: "STANDARD", costMultiplier: 1 },
    funnel: { steps: 32, ineligibleSteps: 0, ineligibleReasons: {}, setupsDiscovered: 3, statesReached: { DISCOVERED: 3, BLOCKED: 3 }, plansConfirmed: 3, plansSkippedOverlap: 0, fills: 2, expired: 1, closed: 2, openAtEnd: 0 },
    stats: stats(), drawdown: { maxDrawdownR: 1, peakAt: T, troughAt: T, recoveredAt: null, recoveryTrades: null },
    equityCurve: [], breakdowns: [], segments: [], inSample: null, outOfSample: null,
    monteCarlo: { resamples: 1000, seed: 18, trades: 2, label: "INSUFFICIENT", totalRP05: -2, totalRP50: 1, totalRP95: 4, maxDrawdownRP50: 1, maxDrawdownRP95: 2, detail: "" },
    ambiguousTrades: 0,
    trades: [{
      variant: "A", status: "CLOSED", setupId: "S1", setupType: "LIQUIDITY_SWEEP_MSS", model: "M15_CLOSE", direction: "BULLISH", confirmedAt: T,
      limitPrice: 2000, stop: 1990, target: 2020, fillPrice: 2000, filledAt: T, exitPrice: 2020, exitedAt: T, exitReason: "TARGET", result: "FULL_WIN",
      rMultiple: 2, netRMultiple: 2, mfeR: 2.1, maeR: -0.2, ambiguous: false, detail: "",
    }],
    ...over,
  };
}

function run(over: Partial<BacktestRun> = {}): BacktestRun {
  return {
    id: "33333333-3333-3333-3333-333333333333", status: "COMPLETED",
    request: { symbol: "XAUUSD", start: T, end: T, variants: [{ name: "A", entryMode: "STANDARD", costMultiplier: 1 }], segments: 1, outOfSampleFrom: null },
    progress: { variant: null, stepsDone: 32, stepsTotal: 32, pct: 100 }, error: null,
    result: {
      variants: [variant()], data: { provider: "stub-real", isSynthetic: true, executionBars: 96, stepBars: 32, firstBar: T, lastBar: T },
      disclosures: ["Risk locks, the news gate and verdict authority are NOT applied.", "Past simulated results are not a forecast."], configHash: "a".repeat(64), completedAt: T,
    },
    createdAt: T, startedAt: T, finishedAt: T, integrity: "VERIFIED", authority: "RESEARCH_ONLY", strategyVersion: "0.20.0-phase20", ...over,
  };
}

const running = () => run({ status: "RUNNING", result: null, progress: { variant: "A", stepsDone: 10, stepsTotal: 32, pct: 31.2 }, integrity: "NOT_APPLICABLE", finishedAt: null });

const listOf = (runs: BacktestRun[], available = true): BacktestListResponse => ({
  runs: runs.map((r) => ({ id: r.id, symbol: r.request.symbol, start: r.request.start, end: r.request.end, status: r.status, pct: r.progress.pct, variants: ["A"], closedTrades: { A: 2 }, netTotalR: { A: 1 }, createdAt: r.createdAt, strategyVersion: r.strategyVersion })),
  store: { available, backend: available ? "database:sqlite" : "unconfigured", reason: available ? null : "no backtest store is configured (BACKTEST_STORE): nothing is run or saved", runningId: null },
  generatedAt: T,
});

describe("backtest trust rules", () => {
  it("accepts completed and running runs", () => {
    expect(reconcileBacktestRun(run()).status).toBe("READY");
    expect(reconcileBacktestRun(running()).status).toBe("READY");
    expect(reconcileBacktestRun(run({ result: null, integrity: "TAMPERED" })).status).toBe("READY");
  });

  it.each([
    [run({ authority: "AUTHORIZED" }), "claimed authority"],
    [run({ status: "RUNNING" }), "unfinished run"],
    [run({ result: null }), "without a result"],
    [run({ result: { ...run().result!, disclosures: ["fine print"] } }), "disclosures"],
    [run({ result: { ...run().result!, variants: [variant({ funnel: { ...variant().funnel, expired: 0 } })] } }), "every plan"],
    [run({ result: { ...run().result!, variants: [variant({ stats: stats({ count: 5, wins: 3, losses: 2 }) })] } }), "closed trades"],
    [run({ result: { ...run().result!, variants: [variant({ stats: stats({ label: "MODERATE" }) })] } }), "sample label"],
    [run({ result: { ...run().result!, variants: [variant({ monteCarlo: { ...variant().monteCarlo!, totalRP05: 9 } })] } }), "Monte Carlo"],
    [null, "unreachable"],
  ])("rejects %#", (payload, reason) => {
    const r = reconcileBacktestRun(payload);
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });

  it("refuses runs from an unavailable store and builds requests", () => {
    expect(reconcileBacktestList({ ...listOf([run()]), store: listOf([], false).store }).status).toBe("UNAVAILABLE");
    const f: BacktestForm = { symbol: "XAUUSD", start: "2024-04-15T08:00", end: "2024-04-18T08:00", modeA: "STANDARD", compare: true, modeB: "CONSERVATIVE", costMultiplierB: "2", segments: "3", outOfSampleFrom: "2024-04-17T08:00" };
    const built = backtestRequest(f);
    expect("body" in built && built.body.variants).toEqual([{ name: "A", entryMode: "STANDARD", costMultiplier: 1 }, { name: "B", entryMode: "CONSERVATIVE", costMultiplier: 2 }]);
    expect("body" in built && built.body.segments).toBe(3);
    expect(backtestRequest({ ...f, end: f.start })).toEqual({ error: "End must be after start" });
    expect(backtestRequest({ ...f, segments: "9" })).toEqual({ error: "Segments: 1 to 6" });
    expect(backtestRequest({ ...f, outOfSampleFrom: "2024-04-19T08:00" })).toEqual({ error: "Out-of-sample start must lie inside the range" });
  });
});

function api(over: Partial<BacktestApi> = {}): BacktestApi {
  return {
    list: vi.fn(async () => ({ status: "READY" as const, list: listOf([run()]) })),
    get: vi.fn(async () => ({ status: "READY" as const, run: run() })),
    start: vi.fn(async () => ({ ok: true as const, run: running() })),
    cancel: vi.fn(async () => ({ ok: true as const, run: running() })),
    remove: vi.fn(async () => ({ ok: true as const, run: null })),
    confirm: () => true,
    pollMs: 10,
    ...over,
  };
}

describe("Backtest view", () => {
  it("is reachable from #backtest and explains an unconfigured store", async () => {
    expect(viewForHash("#backtest")).toBe("BACKTEST");
    render(<BacktestView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api({ list: vi.fn(async () => ({ status: "READY" as const, list: listOf([], false) })) })} />);
    expect((await screen.findByTestId("backtest-unavailable")).textContent).toContain("BACKTEST_STORE");
  });

  it("shows a completed run with funnel, disclosures, Monte Carlo and trades", async () => {
    render(<BacktestView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api()} />);
    fireEvent.click(await screen.findByText("2024-04-18 15:01Z"));
    expect((await screen.findByTestId("backtest-detail")).textContent).toContain("RESEARCH_ONLY");
    expect(screen.getByTestId("backtest-disclosures").textContent).toContain("SYNTHETIC data");
    expect(screen.getByTestId("backtest-funnel-A").textContent).toContain("3 plans confirmed → 2 filled · 2 closed · 1 expired");
    expect(screen.getByTestId("backtest-mc-A").textContent).toContain("-2/1/4");
    expect(screen.getByTestId("backtest-trades-A").textContent).toContain("FULL_WIN");
  });

  it("starts a run, polls progress until completion and validates the form", async () => {
    const get = vi.fn().mockResolvedValueOnce({ status: "READY", run: running() }).mockResolvedValue({ status: "READY", run: run() });
    const a = api({ get });
    render(<BacktestView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={a} />);
    await screen.findByTestId("backtest-form");
    fireEvent.click(screen.getByText("Start backtest"));
    expect((await screen.findByTestId("backtest-form-error")).textContent).toBe("Start and end are required");
    const inputs = screen.getByTestId("backtest-form").querySelectorAll('input[type="datetime-local"]');
    fireEvent.change(inputs[0]!, { target: { value: "2024-04-15T08:00" } });
    fireEvent.change(inputs[1]!, { target: { value: "2024-04-16T08:00" } });
    fireEvent.click(screen.getByText("Start backtest"));
    await waitFor(() => expect(a.start).toHaveBeenCalledOnce());
    expect((await screen.findByTestId("backtest-progress")).textContent).toContain("31.2%");
    await waitFor(() => expect(screen.queryByTestId("backtest-progress")).toBeNull());
    expect(screen.getByTestId("backtest-funnel-A")).toBeTruthy();
    expect(get).toHaveBeenCalled();
  });

  it("offers cancel while running and delete only after confirmation", async () => {
    const a = api({ get: vi.fn(async () => ({ status: "READY" as const, run: running() })), pollMs: 60_000, confirm: vi.fn(() => false) });
    render(<BacktestView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={a} />);
    fireEvent.click(await screen.findByText("2024-04-18 15:01Z"));
    fireEvent.click(await screen.findByText("Cancel run"));
    await waitFor(() => expect(a.cancel).toHaveBeenCalledOnce());
    cleanup();
    const b = api({ confirm: vi.fn(() => false) });
    render(<BacktestView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={b} />);
    fireEvent.click(await screen.findByText("2024-04-18 15:01Z"));
    fireEvent.click(await screen.findByTestId("backtest-delete"));
    expect(b.confirm).toHaveBeenCalled();
    expect(b.remove).not.toHaveBeenCalled();
  });
});
