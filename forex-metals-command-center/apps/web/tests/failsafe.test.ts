import { describe, expect, it } from "vitest";

import { loadDashboard } from "../src/lib/api";
import { reconcileMarketState, unavailableDecision } from "../src/lib/failsafe";

const NOW = new Date("2024-01-09T10:00:00Z");

function payload(overrides: Record<string, unknown> = {}) {
  const decision = {
    ...unavailableDecision("XAUUSD", ["ANALYSIS_GATES_NOT_IMPLEMENTED"], "x", NOW, "0.0.0-phase0"),
    verdict: "WAIT",
    dataQuality: "CURRENT",
    ...overrides,
  };
  return { decision, data: { provider: "p", isSynthetic: false, issues: [] } };
}

describe("reconcileMarketState", () => {
  it("unreachable API renders UNAVAILABLE", () => {
    const r = reconcileMarketState("XAUUSD", null, null, NOW);
    expect(r.decision.verdict).toBe("UNAVAILABLE");
    expect(r.decision.blockers).toContain("PROVIDER_UNAVAILABLE");
    expect(r.trusted).toBe(false);
  });

  it("accepts a well-formed WAIT under FAIL_SAFE_ONLY", () => {
    const r = reconcileMarketState("XAUUSD", payload(), "FAIL_SAFE_ONLY", NOW);
    expect(r.trusted).toBe(true);
    expect(r.decision.verdict).toBe("WAIT");
  });

  it.each([
    ["not an object", "garbage"],
    ["missing decision", { data: {} }],
    ["unknown verdict", payload({ verdict: "BUY" })],
    ["unknown blocker", payload({ blockers: ["MADE_UP"] })],
    ["unknown data quality", payload({ dataQuality: "GREAT" })],
  ])("malformed payload (%s) is an integrity failure", (_name, body) => {
    const r = reconcileMarketState("XAUUSD", body, "FAIL_SAFE_ONLY", NOW);
    expect(r.decision.verdict).toBe("UNAVAILABLE");
    expect(r.decision.blockers).toEqual(["SYSTEM_INTEGRITY_FAILURE"]);
  });

  it("symbol mismatch never leaks another asset's decision", () => {
    const r = reconcileMarketState("XAUUSD", payload({ symbol: "EURUSD" }), "FAIL_SAFE_ONLY", NOW);
    expect(r.decision.symbol).toBe("XAUUSD");
    expect(r.decision.verdict).toBe("UNAVAILABLE");
  });

  it.each(["LONG", "SHORT", "NO_TRADE"])("%s under FAIL_SAFE_ONLY or unknown authority is refused", (v) => {
    for (const authority of ["FAIL_SAFE_ONLY", null] as const) {
      const r = reconcileMarketState("XAUUSD", payload({ verdict: v, direction: "LONG" }), authority, NOW);
      expect(r.decision.verdict).toBe("UNAVAILABLE");
      expect(r.decision.direction).toBeNull();
    }
  });

  it("directional verdict with unusable-data blocker is refused even under FULL authority", () => {
    const r = reconcileMarketState("XAUUSD", payload({ verdict: "LONG", blockers: ["DATA_STALE"] }), "FULL", NOW);
    expect(r.decision.verdict).toBe("UNAVAILABLE");
  });
});

describe("loadDashboard", () => {
  it("network failure fails safe", async () => {
    const failing = (async () => {
      throw new TypeError("network down");
    }) as unknown as typeof fetch;
    const s = await loadDashboard("XAUUSD", failing);
    expect(s.status).toBeNull();
    expect(s.market.decision.verdict).toBe("UNAVAILABLE");
  });

  it("HTTP 500 fails safe", async () => {
    const erroring = (async () => new Response("{}", { status: 500 })) as unknown as typeof fetch;
    const s = await loadDashboard("XAUUSD", erroring);
    expect(s.market.decision.verdict).toBe("UNAVAILABLE");
  });

  it("uses backend status authority when reachable", async () => {
    const ok = (async (url: string) => {
      const body = url.includes("system/status")
        ? { verdictAuthority: "FAIL_SAFE_ONLY", phase: 0 }
        : payload({ verdict: "SHORT" });
      return new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;
    const s = await loadDashboard("XAUUSD", ok);
    expect(s.status?.verdictAuthority).toBe("FAIL_SAFE_ONLY");
    expect(s.market.decision.verdict).toBe("UNAVAILABLE");
    expect(s.market.decision.blockers).toContain("SYSTEM_INTEGRITY_FAILURE");
  });
});
