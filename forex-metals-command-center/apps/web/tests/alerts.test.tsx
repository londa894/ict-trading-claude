// @vitest-environment jsdom
import type { Alert, AlertFeed, ReadyWatch } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlertsView, filterAlerts } from "../src/components/AlertsView";
import { appendEvents } from "../src/components/EventLog";
import { createReadyWatch, deleteReadyWatch, loadAlerts } from "../src/lib/api";
import {
  alertEvents,
  DISMISSED_KEY,
  mergeAlerts,
  readDismissed,
  reconcileAlertFeed,
  reconcileWatches,
  saveDismissed,
  watchRequestBody,
} from "../src/lib/alerts";

afterEach(cleanup);

const T = "2024-01-09T16:00:00Z";

function alert(seq: number, over: Partial<Alert> = {}): Alert {
  return {
    id: `ALERT:${seq}`, seq, dedupeKey: `XAUUSD:LIQUIDITY_SWEEP:L${seq}`, symbol: "XAUUSD", type: "LIQUIDITY_SWEEP", category: "WATCH",
    priority: "MEDIUM", title: "XAUUSD SSL SWING_LOW SWEEP", message: "SWING_LOW @ 2040.21, close 2040.61", direction: null, price: 2040.21,
    occurredAt: T, createdAt: T, strategyVersion: "0.19.0-phase19", ...over,
  };
}

const monitor: AlertFeed["monitor"] = {
  enabled: true, running: true, symbols: ["XAUUSD"], pollSeconds: 30, maxQuietSeconds: 300, cycles: 4, lastCycleAt: T, lastCycleMs: 2600,
  lastError: null, baselined: ["XAUUSD"],
};

function feed(alerts: Alert[], over: Partial<AlertFeed> = {}): AlertFeed {
  return {
    alerts, nextCursor: Math.max(0, ...alerts.map((a) => a.seq)), suppressed: { LIQUIDITY_SWEEP: 2 }, monitor, authority: "NOT_AUTHORIZED",
    generatedAt: T, ...over,
  };
}

function watch(over: Partial<ReadyWatch> = {}): ReadyWatch {
  return {
    id: "WATCH:abcdef123456", symbol: "XAUUSD", direction: "BULLISH", state: "GATES_PENDING",
    conditions: [
      { name: "LTF_CONFIRMATION", status: "MET", detail: "M15_CLOSE" },
      { name: "NEWS_GATE", status: "MISSING", detail: "Phase 13" },
    ],
    nextRequiredEvent: "NEWS_GATE: economic calendar arrives in Phase 13", createdAt: T, lastCheckedAt: T, firedAt: null, ...over,
  };
}

describe("reconcileAlertFeed", () => {
  it("accepts a newest-first feed", () => {
    expect(reconcileAlertFeed(feed([alert(3), alert(2)]), "FAIL_SAFE_ONLY").status).toBe("READY");
  });

  const good = feed([alert(3), alert(2)]);
  it.each([
    ["unreachable", null],
    ["authority claimed", { ...good, authority: "AUTHORIZED" }],
    ["unknown type", feed([alert(1, { type: "BUY_SIGNAL" as never })])],
    ["unknown priority", feed([alert(1, { priority: "URGENT" as never })])],
    ["a READY alert under FAIL_SAFE_ONLY", feed([alert(1, { type: "READY", category: "ENTRY", priority: "CRITICAL", title: "XAUUSD READY: LONG" })])],
    ["directional wording in a non-READY alert", feed([alert(1, { message: "BUY now" })])],
    ["alerts out of order", feed([alert(2), alert(3)])],
    ["cursor behind the alerts", { ...good, nextCursor: 1 }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileAlertFeed(payload, "FAIL_SAFE_ONLY").status).toBe("UNAVAILABLE");
  });

  it("accepts a READY alert only with FULL authority", () => {
    const ready = feed([alert(1, { type: "READY", category: "ENTRY", priority: "CRITICAL", title: "XAUUSD READY: LONG" })]);
    expect(reconcileAlertFeed(ready, "FULL").status).toBe("READY");
    expect(reconcileAlertFeed(ready, null).status).toBe("UNAVAILABLE");
  });
});

describe("ready watches", () => {
  it("validates watches and refuses FIRED without FULL authority", () => {
    expect(reconcileWatches([watch()], "FAIL_SAFE_ONLY").status).toBe("READY");
    expect(reconcileWatches([watch({ state: "FIRED", firedAt: T })], "FAIL_SAFE_ONLY").status).toBe("UNAVAILABLE");
    expect(reconcileWatches([watch({ state: "FIRED", firedAt: T })], "FULL").status).toBe("READY");
    expect(reconcileWatches([watch({ conditions: [{ name: "X", status: "DONE" as never, detail: "" }] })], "FULL").status).toBe("UNAVAILABLE");
    expect(reconcileWatches({}, "FULL").status).toBe("UNAVAILABLE");
    expect(watchRequestBody("xauusd", "ANY")).toEqual({ symbol: "XAUUSD", direction: null });
  });

  it("creates and deletes watches through the API", async () => {
    const calls: Array<[string, RequestInit]> = [];
    const ok = (async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return new Response(init.method === "POST" ? "{}" : null, { status: init.method === "POST" ? 201 : 204 });
    }) as unknown as typeof fetch;
    expect(await createReadyWatch("XAUUSD", "BEARISH", ok)).toBeNull();
    expect(calls[0]![0]).toMatch(/\/api\/v1\/alerts\/ready-watches$/);
    expect(JSON.parse(String(calls[0]![1].body))).toEqual({ symbol: "XAUUSD", direction: "BEARISH" });
    expect(await deleteReadyWatch("WATCH:abcdef123456", ok)).toBeNull();
    expect(calls[1]![0]).toMatch(/ready-watches\/WATCH%3Aabcdef123456$/);
    const limited = (async () => new Response("{}", { status: 422 })) as unknown as typeof fetch;
    expect(await createReadyWatch("XAUUSD", "ANY", limited)).toBe("Watch not created (HTTP 422)");
    const down = (async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    expect(await deleteReadyWatch("WATCH:abcdef123456", down)).toBe("Alerts API unreachable");
  });
});

describe("feed helpers", () => {
  it("loads incrementally, merges without duplicates and feeds the event log", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(feed([alert(5), alert(4)])), { status: 200 });
    }) as unknown as typeof fetch;
    const result = await loadAlerts(3, "FAIL_SAFE_ONLY", fetcher);
    expect(result.status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/alerts\?since=3&limit=200$/);
    const merged = mergeAlerts([alert(4), alert(3)], [alert(5), alert(4)]);
    expect(merged.map((a) => a.seq)).toEqual([5, 4, 3]);
    const events = appendEvents([], alertEvents([alert(5, { priority: "CRITICAL" }), alert(4)]));
    expect(events.map((e) => e.key)).toEqual(["alert:ALERT:5", "alert:ALERT:4"]);
    expect(events[0]!.severity).toBe("ERROR");
  });

  it("filters by category, priority, market and dismissal; dismissals survive broken storage", () => {
    const list = [alert(1), alert(2, { category: "CRITICAL", priority: "CRITICAL", type: "DATA_UNAVAILABLE" }), alert(3, { symbol: "EURUSD" })];
    expect(filterAlerts(list, { category: "", minPriority: "HIGH", symbol: "" }, new Set()).map((a) => a.seq)).toEqual([2]);
    expect(filterAlerts(list, { category: "WATCH", minPriority: "LOW", symbol: "XAUUSD" }, new Set(["ALERT:9"])).map((a) => a.seq)).toEqual([1]);
    expect(filterAlerts(list, { category: "", minPriority: "LOW", symbol: "" }, new Set(["ALERT:1"])).map((a) => a.seq)).toEqual([2, 3]);
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveDismissed(["ALERT:1"], storage);
    expect(store.get(DISMISSED_KEY)).toBe('["ALERT:1"]');
    expect(readDismissed(storage)).toEqual(["ALERT:1"]);
    const throwing = { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("x"); } };
    expect(readDismissed(throwing)).toEqual([]);
    expect(() => saveDismissed(["a"], throwing)).not.toThrow();
  });
});

describe("AlertsView", () => {
  const props = {
    alerts: [alert(2, { category: "CRITICAL", priority: "CRITICAL", type: "DATA_UNAVAILABLE", title: "XAUUSD decision UNAVAILABLE", message: "DATA_STALE" }), alert(1)] as Alert[],
    feedError: null,
    monitor,
    suppressed: { LIQUIDITY_SWEEP: 2 },
    dismissed: new Set<string>(),
    onDismiss: vi.fn(),
    onRestore: vi.fn(),
    watches: { status: "READY" as const, watches: [watch()] },
    symbols: ["XAUUSD", "EURUSD"],
    onCreateWatch: vi.fn(),
    onDeleteWatch: vi.fn(),
    watchError: null,
  };

  it("shows the disclaimer, monitor status, watches with their checklist and the feed", () => {
    render(<AlertsView {...props} />);
    expect(screen.getByTestId("alerts-view").textContent).toContain("never trade instructions");
    expect(screen.getByTestId("monitor-status").textContent).toContain("running");
    expect(screen.getByTestId("monitor-status").textContent).toContain("2 duplicates suppressed");
    const w = screen.getByTestId("watch-WATCH:abcdef123456").textContent ?? "";
    expect(w).toContain("GATES_PENDING");
    expect(w).toContain("✓ LTF_CONFIRMATION");
    expect(w).toContain("✗ NEWS_GATE");
    expect(screen.getByTestId("alert-2").textContent).toContain("CRITICAL");
  });

  it("creates, removes and dismisses", () => {
    render(<AlertsView {...props} />);
    fireEvent.change(screen.getByLabelText("Watch symbol"), { target: { value: "EURUSD" } });
    fireEvent.change(screen.getByLabelText("Watch direction"), { target: { value: "BEARISH" } });
    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    expect(props.onCreateWatch).toHaveBeenCalledWith("EURUSD", "BEARISH");
    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    expect(props.onDeleteWatch).toHaveBeenCalledWith("WATCH:abcdef123456");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss XAUUSD SSL SWING_LOW SWEEP" }));
    expect(props.onDismiss).toHaveBeenCalledWith("ALERT:1");
    fireEvent.change(screen.getByLabelText("Minimum priority"), { target: { value: "CRITICAL" } });
    expect(screen.queryByTestId("alert-1")).toBeNull();
  });

  it("fails safe when the feed is untrusted", () => {
    render(<AlertsView {...props} alerts={[]} feedError="Rejected alert feed: authority claimed" watches={{ status: "UNAVAILABLE", reason: "Ready watches unreachable" }} />);
    expect(screen.getByTestId("alerts-unavailable").textContent).toContain("authority claimed");
    expect(screen.getByTestId("alerts-empty")).toBeTruthy();
  });
});
