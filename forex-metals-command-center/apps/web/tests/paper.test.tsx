// @vitest-environment jsdom
import type { PaperListResponse, PaperSim, PaperSimRow } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JournalView, type JournalApi } from "../src/components/JournalView";
import type { PaperApi } from "../src/components/PaperSection";
import { viewForHash } from "../src/components/MarketsViews";
import { paperRequest, reconcilePaperList, reconcilePaperSim, type PaperForm } from "../src/lib/paper";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const T = "2024-04-18T15:01:00Z";
const ev = (seq: number, type: PaperSim["events"][number]["type"], price: number | null = null) => ({
  seq, type, at: T, price, ambiguous: false, detail: type.toLowerCase(), integrity: "VERIFIED" as const,
});

function sim(over: Partial<PaperSim> = {}): PaperSim {
  return {
    id: "22222222-2222-2222-2222-222222222222", symbol: "XAUUSD", source: "MANUAL", direction: "BULLISH", entryType: "MARKET",
    referencePrice: 2047.5, limitPrice: null, stop: 2040, target: 2060, costs: { spread: 0.3, slippage: 0.05, commission: 0 },
    status: "CLOSED", createdAt: T, fillPrice: 2047.9, filledAt: "2024-04-18T15:05:00Z", exitPrice: 2040 - 0.05, exitedAt: "2024-04-18T15:20:00Z",
    processedThrough: "2024-04-18T15:15:00Z", dataQuality: "CURRENT", dataNote: null, isSynthetic: false, detectedViolations: ["NO_CONFIRMED_PLAN"],
    result: {
      result: "FULL_LOSS", exitReason: "STOP", rMultiple: -1.006, netRMultiple: -1.006, plannedR: 1.53, mfeR: 0.1, maeR: -1.1,
      entryEfficiency: 0.1, exitEfficiency: 0, durationMinutes: 15, violations: ["NO_CONFIRMED_PLAN"], classification: "PROCESS_ERROR",
    },
    events: [ev(1, "CREATED", 2047.5), ev(2, "FILLED", 2047.9), ev(3, "STOP_HIT", 2039.95)],
    summary: {
      capturedAt: T, dayOfWeek: "Thursday", activeSessions: [], activeKillZones: [], timeQuality: null, verdict: "WAIT", dataQuality: "CURRENT",
      engineAuthorization: "NOT_AUTHORIZED", executionTimeframe: "M5", htfBias: null, primaryDol: null, liquidityEvent: null, structureEvent: null,
      displacement: null, pdArray: null, noWick: null, newsState: null, macroBias: null, macroState: null, setupType: null, setupState: null,
      setupScore: null, setupGrade: null, confidence: null, planEntry: null, planStop: null, planTargets: [], planRr: null, riskStatus: null,
      blockers: [], isSynthetic: false, strategyVersion: "0.20.0-phase20",
    },
    integrity: "VERIFIED", notes: "", authority: "SIMULATION_ONLY", strategyVersion: "0.20.0-phase20", ...over,
  };
}

const openSim = () => sim({ status: "OPEN", exitPrice: null, exitedAt: null, result: null, events: [ev(1, "CREATED", 2047.5), ev(2, "FILLED", 2047.9)] });
const pendingSim = () => sim({ status: "PENDING", fillPrice: null, filledAt: null, exitPrice: null, exitedAt: null, result: null, events: [ev(1, "CREATED", 2047.5)] });

const row = (s: PaperSim): PaperSimRow => ({
  id: s.id, symbol: s.symbol, source: s.source, direction: s.direction, entryType: s.entryType, status: s.status, createdAt: s.createdAt,
  fillPrice: s.fillPrice, exitPrice: s.exitPrice, result: s.result?.result ?? null, netRMultiple: s.result?.netRMultiple ?? null,
  classification: s.result?.classification ?? null, isSynthetic: s.isSynthetic, integrity: s.integrity,
});

const listOf = (sims: PaperSim[], available = true): PaperListResponse => ({
  sims: sims.map(row), nextCursor: null, generatedAt: T,
  store: { available, backend: available ? "database:sqlite" : "unconfigured", reason: available ? null : "no paper store is configured (PAPER_STORE): nothing is simulated or saved", monitorEnabled: available },
});

describe("paper sim trust", () => {
  it("accepts consistent closed, open and pending sims", () => {
    for (const s of [sim(), openSim(), pendingSim()]) expect(reconcilePaperSim(s).status).toBe("READY");
  });

  it.each([
    [sim({ authority: "AUTHORIZED" }), "claimed authority"],
    [sim({ stop: 2050 }), "wrong side"],
    [sim({ status: "PENDING" }), "pending sim with a fill"],
    [sim({ result: null }), "without fill, exit or result"],
    [sim({ events: [ev(1, "CREATED"), ev(2, "FILLED"), ev(3, "EXPIRED")] }), "events do not match"],
    [sim({ events: [ev(2, "CREATED")] }), "out of order"],
    [sim({ result: { ...sim().result!, classification: "VALID_LOSS" } }), "process class"],
    [sim({ result: { ...sim().result!, netRMultiple: -0.5 } }), "net R above gross R"],
    [sim({ status: "EXPIRED", result: null, exitPrice: null }), "expired/cancelled sim with a fill"],
    [null, "unreachable"],
  ])("rejects %#", (payload, reason) => {
    const r = reconcilePaperSim(payload);
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });

  it("refuses sims listed by an unavailable store", () => {
    expect(reconcilePaperList(listOf([sim()])).status).toBe("READY");
    expect(reconcilePaperList({ ...listOf([sim()]), store: listOf([], false).store }).status).toBe("UNAVAILABLE");
  });

  it("builds manual and engine-plan requests", () => {
    const f: PaperForm = { symbol: "XAUUSD", source: "MANUAL", direction: "BEARISH", entryType: "LIMIT", limitPrice: "2050", stop: "2060", target: "2030", notes: " x " };
    expect(paperRequest(f)).toEqual({ body: { symbol: "XAUUSD", source: "MANUAL", direction: "BEARISH", entryType: "LIMIT", limitPrice: 2050, stop: 2060, target: 2030, notes: "x" } });
    expect(paperRequest({ ...f, limitPrice: "" })).toEqual({ error: "Limit price is required" });
    expect(paperRequest({ ...f, stop: "" })).toEqual({ error: "Stop and target prices are required" });
    expect(paperRequest({ ...f, source: "ENGINE_PLAN", stop: "" })).toMatchObject({ body: { source: "ENGINE_PLAN", direction: null, stop: null } });
  });
});

const journalApi: JournalApi = {
  list: vi.fn(async () => ({ status: "READY" as const, list: { entries: [], nextCursor: null, generatedAt: T, store: { available: true, backend: "database:sqlite", reason: null } } })),
  get: vi.fn(), create: vi.fn(), outcome: vi.fn(), remove: vi.fn(), confirm: () => true,
};

function paperApi(over: Partial<PaperApi> = {}): PaperApi {
  return {
    list: vi.fn(async () => ({ status: "READY" as const, list: listOf([sim(), openSim()]) })),
    get: vi.fn(async () => ({ status: "READY" as const, sim: openSim() })),
    create: vi.fn(async () => ({ ok: true as const, sim: pendingSim() })),
    close: vi.fn(async () => ({ ok: true as const, sim: sim() })),
    cancel: vi.fn(async () => ({ ok: true as const, sim: pendingSim() })),
    remove: vi.fn(async () => ({ ok: true as const, sim: null })),
    confirm: () => true,
    ...over,
  };
}

describe("Paper sims in the Journal view", () => {
  it("opens from the #paper hash on the paper tab", async () => {
    expect(viewForHash("#paper")).toBe("JOURNAL");
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} paperApi={paperApi()} initialTab="PAPER" />);
    expect(await screen.findByTestId("paper-table")).toBeTruthy();
    expect(screen.getByTestId("paper-section").textContent).toContain("nothing is sent to any broker");
  });

  it("explains an unconfigured store", async () => {
    const api = paperApi({ list: vi.fn(async () => ({ status: "READY" as const, list: listOf([], false) })) });
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} paperApi={api} initialTab="PAPER" />);
    expect((await screen.findByTestId("paper-unavailable")).textContent).toContain("PAPER_STORE");
    expect(screen.queryByTestId("paper-form")).toBeNull();
  });

  it("switches tabs, shows the detail timeline and closes an open sim", async () => {
    const api = paperApi();
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} paperApi={api} />);
    fireEvent.click(screen.getByRole("tab", { name: "Paper sims" }));
    const table = await screen.findByTestId("paper-table");
    expect(table.textContent).toContain("FULL_LOSS");
    expect(table.textContent).toContain("-1.006R");
    fireEvent.click(screen.getAllByText("2024-04-18 15:01Z")[1]!);
    const detail = await screen.findByTestId("paper-detail");
    expect(detail.textContent).toContain("SIMULATION_ONLY");
    expect(detail.textContent).toContain("assumptions, not broker specs");
    expect(screen.getByTestId("paper-events").textContent).toContain("FILLED @ 2047.9");
    fireEvent.click(screen.getByText("Close simulation at last bar"));
    await waitFor(() => expect(api.close).toHaveBeenCalledWith(openSim().id));
    expect((await screen.findByTestId("paper-result")).textContent).toContain("PROCESS_ERROR");
  });

  it("validates the form, starts a sim and shows server errors", async () => {
    const api = paperApi({ create: vi.fn(async () => ({ ok: false as const, error: "there is no confirmed plan to forward-test" })) });
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} paperApi={api} initialTab="PAPER" />);
    await screen.findByTestId("paper-form");
    fireEvent.click(screen.getByText("Start paper simulation"));
    expect((await screen.findByTestId("paper-error")).textContent).toBe("Stop and target prices are required");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "ENGINE_PLAN" } });
    fireEvent.click(screen.getByText("Start paper simulation"));
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
    expect((await screen.findByTestId("paper-error")).textContent).toContain("no confirmed plan");
  });

  it("offers cancel only for pending sims and flags tampering", async () => {
    const tampered = { ...pendingSim(), integrity: "TAMPERED" as const };
    const api = paperApi({ get: vi.fn(async () => ({ status: "READY" as const, sim: tampered })) });
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={journalApi} paperApi={api} initialTab="PAPER" />);
    fireEvent.click((await screen.findAllByText("2024-04-18 15:01Z"))[0]!);
    expect((await screen.findByTestId("paper-tampered")).textContent).toContain("INTEGRITY CHECK FAILED");
    expect(screen.queryByText("Close simulation at last bar")).toBeNull();
    fireEvent.click(screen.getByText("Cancel pending simulation"));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledOnce());
  });
});
