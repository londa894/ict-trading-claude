// @vitest-environment jsdom
import type { JournalEntry, JournalEntryRow, JournalListResponse, JournalOutcome } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JournalView, type JournalApi } from "../src/components/JournalView";
import { viewForHash } from "../src/components/MarketsViews";
import {
  createJournalEntry,
  entryRequest,
  outcomeRequest,
  reconcileJournalEntry,
  reconcileJournalList,
  rMultiple,
  type EntryForm,
} from "../src/lib/journal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const T = "2024-04-18T15:01:00Z";
const HASH = "a".repeat(64);

function outcome(over: Partial<JournalOutcome> = {}): JournalOutcome {
  return {
    revision: 1, recordedAt: T, exitPrice: 2400, exitedAt: T, exitReason: "TARGET", mfePrice: 2402, maePrice: 2376,
    extremeSource: "MANUAL", extremesSynthetic: false, extremesDetail: null, result: "PARTIAL_WIN", rMultiple: 2, plannedR: 3,
    mfeR: 2.2, maeR: -0.4, entryEfficiency: 0.846, exitEfficiency: 0.923, durationMinutes: 62, reportedViolations: [],
    violations: ["NO_CONFIRMED_PLAN"], classification: "BAD_PROCESS_WIN", notes: "", integrity: "VERIFIED", ...over,
  };
}

function entry(over: Partial<JournalEntry> = {}): JournalEntry {
  const o = outcome();
  return {
    id: "11111111-1111-1111-1111-111111111111", kind: "TRADE", status: "CLOSED", symbol: "XAUUSD", createdAt: T,
    trade: { direction: "BULLISH", entry: 2380, stop: 2370, targets: [2410], volume: 0.5, riskPct: 0.5, openedAt: "2024-04-18T14:59:00Z" },
    notes: "took the sweep", detectedViolations: ["NO_CONFIRMED_PLAN"], result: o.result, classification: o.classification, rMultiple: o.rMultiple,
    summary: {
      capturedAt: T, dayOfWeek: "Thursday", activeSessions: ["NEW_YORK"], activeKillZones: [], timeQuality: "IDEAL", verdict: "WAIT",
      dataQuality: "CURRENT", engineAuthorization: "NOT_AUTHORIZED", executionTimeframe: "M5", htfBias: "BULLISH", primaryDol: null,
      liquidityEvent: null, structureEvent: null, displacement: null, pdArray: null, noWick: null, newsState: "CLEAR", macroBias: null,
      macroState: null, setupType: null, setupState: "NO_SETUP", setupScore: null, setupGrade: null, confidence: "LOW", planEntry: null,
      planStop: null, planTargets: [], planRr: null, riskStatus: "NOT_CONFIGURED", blockers: [], isSynthetic: false, strategyVersion: "0.19.0-phase19",
    },
    snapshot: { capturedAt: T, timing: "PRE_ENTRY", integrity: "VERIFIED", hash: HASH, decision: { verdict: "WAIT" }, evaluation: null, data: null },
    outcome: o, outcomeRevisions: [o], strategyVersion: "0.19.0-phase19", ...over,
  };
}

const open = () => entry({ status: "OPEN", result: null, classification: null, rMultiple: null, outcome: null, outcomeRevisions: [] });

function row(e: JournalEntry): JournalEntryRow {
  return {
    id: e.id, kind: e.kind, status: e.status, symbol: e.symbol, createdAt: e.createdAt, direction: e.trade?.direction ?? null,
    entry: e.trade?.entry ?? null, result: e.result, classification: e.classification, rMultiple: e.rMultiple,
    detectedViolations: e.detectedViolations, snapshotTiming: e.snapshot.timing, integrity: e.snapshot.integrity, isSynthetic: false,
    setupType: null, verdict: "WAIT",
  };
}

const listOf = (entries: JournalEntry[], available = true): JournalListResponse => ({
  entries: entries.map(row), nextCursor: null, generatedAt: T,
  store: { available, backend: available ? "database:sqlite" : "unconfigured", reason: available ? null : "no journal store is configured (JOURNAL_STORE): nothing is saved" },
});

describe("journal record trust", () => {
  it("accepts consistent closed, open and non-trade entries", () => {
    expect(reconcileJournalEntry(entry()).status).toBe("READY");
    expect(reconcileJournalEntry(open()).status).toBe("READY");
    const missed = entry({ kind: "MISSED_ENTRY", trade: null, result: "MISSED_ENTRY", classification: null, rMultiple: null, outcome: null, outcomeRevisions: [], detectedViolations: [] });
    expect(reconcileJournalEntry(missed).status).toBe("READY");
  });

  it.each([
    [entry({ rMultiple: 3 }), "does not match the latest outcome"],
    [entry({ outcome: outcome({ rMultiple: 2.5 }), outcomeRevisions: [outcome({ rMultiple: 2.5 })], rMultiple: 2.5 }), "R does not match"],
    [entry({ outcome: outcome({ classification: "VALID_WIN" }), outcomeRevisions: [outcome({ classification: "VALID_WIN" })], classification: "VALID_WIN" }), "process class"],
    [entry({ status: "OPEN" }), "status does not match"],
    [entry({ outcome: null }), "latest outcome does not match"],
    [entry({ kind: "NO_TRADE", result: "NO_TRADE" }), "non-trade entry with trade data"],
    [entry({ snapshot: { ...entry().snapshot, hash: "x" } }), "hash invalid"],
    [entry({ detectedViolations: ["CHASED_ENTRY"] }), "detected violations missing"],
    [entry({ result: "FULL_WIN" as never, outcome: outcome({ result: "BIG_WIN" as never }), outcomeRevisions: [outcome({ result: "BIG_WIN" as never })] }), "outcome enum"],
    [null, "unreachable"],
  ])("rejects %#", (payload, reason) => {
    const r = reconcileJournalEntry(payload);
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });

  it("lists are refused when an unavailable store returns entries", () => {
    expect(reconcileJournalList(listOf([entry()])).status).toBe("READY");
    expect(reconcileJournalList({ ...listOf([entry()]), store: listOf([], false).store }).status).toBe("UNAVAILABLE");
  });

  it("computes R the way the server does", () => {
    expect(rMultiple("BULLISH", 2380, 2370, 2400)).toBe(2);
    expect(rMultiple("BEARISH", 2000, 2010, 1975)).toBe(2.5);
    expect(rMultiple("BULLISH", 2000, null, 2010)).toBeNull();
  });
});

const form = (over: Partial<EntryForm> = {}): EntryForm => ({
  symbol: "XAUUSD", kind: "TRADE", direction: "BULLISH", entry: "2380", stop: "2370", targets: "2410, 2420", volume: "0.5", riskPct: "",
  openedAt: "2024-04-18T10:59", notes: " sweep ", ...over,
});

describe("form to request", () => {
  it("builds trade and non-trade requests with UTC times", () => {
    const built = entryRequest(form());
    expect("body" in built && built.body.trade).toMatchObject({ entry: 2380, stop: 2370, targets: [2410, 2420], volume: 0.5, riskPct: null });
    expect("body" in built && built.body.trade?.openedAt.endsWith("Z")).toBe(true);
    expect("body" in built && built.body.notes).toBe("sweep");
    expect(entryRequest(form({ kind: "NO_TRADE", entry: "" }))).toEqual({ body: { symbol: "XAUUSD", kind: "NO_TRADE", notes: "sweep", trade: null } });
    expect("body" in entryRequest(form({ stop: "" })) && (entryRequest(form({ stop: "" })) as { body: { trade: { stop: null } } }).body.trade.stop).toBeNull();
  });

  it("returns the first problem", () => {
    expect(entryRequest(form({ entry: "" }))).toEqual({ error: "Entry price is required" });
    expect(entryRequest(form({ targets: "1,2,3,4" }))).toEqual({ error: "Targets: up to 3 prices, comma separated" });
    expect(entryRequest(form({ volume: "abc" }))).toEqual({ error: "Volume and risk % must be numbers" });
    const o = { exitPrice: "", exitedAt: "2024-04-18T11:00", exitReason: "TARGET" as const, mfePrice: "", maePrice: "", reportedViolations: [], notes: "" };
    expect(outcomeRequest(o)).toEqual({ error: "Exit price is required" });
    expect(outcomeRequest({ ...o, exitPrice: "2400", reportedViolations: ["MOVED_STOP", "MOVED_STOP"] })).toMatchObject({
      body: { exitPrice: 2400, mfePrice: null, reportedViolations: ["MOVED_STOP"] },
    });
  });

  it("surfaces the server's safe error detail and never stores anything in the browser", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const fetcher = (async () => new Response(JSON.stringify({ detail: "journal unavailable: nothing is saved" }), { status: 503 })) as unknown as typeof fetch;
    const res = await createJournalEntry({ symbol: "XAUUSD", kind: "NO_TRADE", trade: null, notes: "" }, fetcher);
    expect(res).toEqual({ ok: false, error: "journal unavailable: nothing is saved" });
    expect(setItem).not.toHaveBeenCalled();
  });
});

function api(over: Partial<JournalApi> = {}): JournalApi {
  return {
    list: vi.fn(async () => ({ status: "READY" as const, list: listOf([entry()]) })),
    get: vi.fn(async () => ({ status: "READY" as const, entry: entry() })),
    create: vi.fn(async () => ({ ok: true as const, entry: open() })),
    outcome: vi.fn(async () => ({ ok: true as const, entry: entry() })),
    remove: vi.fn(async () => ({ ok: true as const, entry: null })),
    confirm: () => true,
    ...over,
  };
}

describe("Journal view", () => {
  it("is reachable from the #journal hash", () => {
    expect(viewForHash("#journal")).toBe("JOURNAL");
  });

  it("explains an unconfigured store and shows no form", async () => {
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api({ list: vi.fn(async () => ({ status: "READY" as const, list: listOf([], false) })) })} />);
    expect((await screen.findByTestId("journal-unavailable")).textContent).toContain("JOURNAL_STORE");
    expect(screen.queryByTestId("journal-form")).toBeNull();
  });

  it("lists entries, opens the snapshot detail and records an outcome", async () => {
    const a = api();
    render(<JournalView symbols={["XAUUSD", "EURUSD"]} activeSymbol="XAUUSD" api={a} />);
    const table = await screen.findByTestId("journal-table");
    expect(table.textContent).toContain("PARTIAL_WIN");
    expect(table.textContent).toContain("+2R");
    expect(table.textContent).toContain("VERIFIED");
    fireEvent.click(screen.getByText("2024-04-18 15:01Z"));
    const detail = await screen.findByTestId("journal-detail");
    expect(detail.textContent).toContain("WAIT (NOT_AUTHORIZED)");
    expect(screen.getByTestId("journal-violations").textContent).toContain("NO_CONFIRMED_PLAN");
    expect(screen.getByTestId("journal-outcome").textContent).toContain("BAD_PROCESS_WIN");
    const outcomeForm = screen.getByTestId("journal-outcome-form");
    fireEvent.change(outcomeForm.querySelector("input")!, { target: { value: "2370" } });
    fireEvent.click(screen.getByText("Record corrected outcome (new revision)"));
    await waitFor(() => expect(a.outcome).toHaveBeenCalledOnce());
    expect(vi.mocked(a.outcome).mock.calls[0]![1]).toMatchObject({ exitPrice: 2370, exitReason: "TARGET" });
  });

  it("records a new entry and shows validation errors", async () => {
    const a = api();
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={a} />);
    await screen.findByTestId("journal-form");
    fireEvent.click(screen.getByText("Record entry (captures the engine snapshot now)"));
    expect((await screen.findByTestId("journal-error")).textContent).toBe("Entry price is required");
    expect(a.create).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Record"), { target: { value: "NO_TRADE" } });
    fireEvent.click(screen.getByText("Record entry (captures the engine snapshot now)"));
    await waitFor(() => expect(a.create).toHaveBeenCalledOnce());
    expect(await screen.findByTestId("journal-detail")).toBeTruthy();
  });

  it("flags tampered records without an outcome form and deletes only after confirmation", async () => {
    const tampered = entry({ snapshot: { ...entry().snapshot, integrity: "TAMPERED" } });
    const a = api({ get: vi.fn(async () => ({ status: "READY" as const, entry: tampered })), confirm: vi.fn(() => false) });
    render(<JournalView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={a} />);
    fireEvent.click(await screen.findByText("2024-04-18 15:01Z"));
    expect((await screen.findByTestId("journal-tampered")).textContent).toContain("INTEGRITY CHECK FAILED");
    expect(screen.queryByTestId("journal-outcome-form")).toBeNull();
    fireEvent.click(screen.getByTestId("journal-delete"));
    expect(a.confirm).toHaveBeenCalled();
    expect(a.remove).not.toHaveBeenCalled();
  });
});
