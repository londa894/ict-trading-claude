// @vitest-environment jsdom
import type { MarketRow, ScanResponse, ScanRow } from "@fmcc/shared-types";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChartPanel } from "../src/components/ChartPanel";
import { LeftNav } from "../src/components/LeftNav";
import { MarketsView, ScannerView, viewForHash, WatchlistView } from "../src/components/MarketsViews";
import { loadScan } from "../src/lib/api";
import {
  ageLabel,
  progressLabel,
  readWatchlist,
  reconcileMarkets,
  reconcileScan,
  sanitizeWatchlist,
  saveWatchlist,
  WATCHLIST_KEY,
} from "../src/lib/scanner";

vi.mock("../src/components/CandleChart", () => ({
  CandleChart: () => <div data-testid="candle-chart" />,
}));

afterEach(() => {
  cleanup();
  window.location.hash = "";
  vi.unstubAllGlobals();
});

const T = "2024-01-09T16:00:00Z";
const CATALOG = ["XAUUSD", "XAGUSD", "EURUSD"];

function row(symbol: string, over: Partial<ScanRow> = {}): ScanRow {
  const validated = symbol === "XAUUSD";
  return {
    rank: 1, symbol, assetClass: symbol.startsWith("XA") ? "METAL" : "FX_MAJOR", priority: validated ? 1 : 2, deeplyValidated: validated,
    marketStatus: "OPEN", verdict: "WAIT", dataQuality: "CURRENT", htfBias: "BULLISH", setupState: "WATCH", setupType: null, setupProgress: 2,
    setupScore: 24, setupGrade: "D", decisionConfidence: "LOW", riskStatus: "NOT_CONFIGURED", primaryDol: null,
    blockers: validated ? ["ANALYSIS_GATES_NOT_IMPLEMENTED"] : ["MARKET_NOT_VALIDATED", "ANALYSIS_GATES_NOT_IMPLEMENTED"],
    nextRequiredEvent: "Wait for a liquidity event", latestClosedOpenTime: T, evaluatedAt: T, cacheAgeSeconds: 0, error: null, ...over,
  };
}

function scan(rows: ScanRow[], over: Partial<ScanResponse> = {}): ScanResponse {
  return {
    rows: rows.map((r, i) => ({ ...r, rank: i + 1 })), requestedSymbols: rows.map((r) => r.symbol), minScore: null, onlySetups: false,
    ranking: ["usable data first (verdict not UNAVAILABLE)"], cacheSeconds: 60, durationMs: 1500, verdictAuthority: "FAIL_SAFE_ONLY",
    authority: "NOT_AUTHORIZED", strategyVersion: "0.20.0-phase20", scannedAt: T, ...over,
  };
}

const markets: MarketRow[] = CATALOG.map((symbol, i) => ({
  symbol, assetClass: symbol.startsWith("XA") ? "METAL" : "FX_MAJOR", base: symbol.slice(0, 3), quote: "USD", priority: i + 1,
  deeplyValidated: symbol === "XAUUSD", marketStatus: "OPEN", positionSizeStatus: "POSITION_SIZE_UNVERIFIED",
}));

describe("reconcileScan", () => {
  it("accepts a fail-safe ranked scan", () => {
    const s = reconcileScan(scan([row("XAUUSD"), row("EURUSD")]), ["XAUUSD", "EURUSD"]);
    expect(s.status).toBe("READY");
  });

  const good = scan([row("XAUUSD"), row("EURUSD")]);
  it.each([
    ["unreachable", null],
    ["authority claimed", { ...good, authority: "AUTHORIZED" }],
    ["a directional verdict under FAIL_SAFE_ONLY", scan([row("XAUUSD", { verdict: "LONG" })])],
    ["a READY setup state", scan([row("XAUUSD", { setupState: "LONG_READY" })])],
    ["a research-only market without its blocker", scan([row("EURUSD", { blockers: ["ANALYSIS_GATES_NOT_IMPLEMENTED"] })])],
    ["a fail-safe verdict without blockers", scan([row("XAUUSD", { blockers: [] })])],
    ["grade not matching the score", scan([row("XAUUSD", { setupGrade: "A" })])],
    ["progress on unusable data", scan([row("XAUUSD", { verdict: "UNAVAILABLE" })])],
    ["duplicate symbols", { ...good, rows: [good.rows[0], { ...good.rows[0], rank: 2 }] }],
    ["an unrequested symbol", { ...good, requestedSymbols: ["XAUUSD"] }],
    ["ranks out of order", { ...good, rows: [{ ...good.rows[0], rank: 2 }, { ...good.rows[1], rank: 1 }] }],
    ["unknown blocker", scan([row("XAUUSD", { blockers: ["BUY_NOW"] as never })])],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileScan(payload).status).toBe("UNAVAILABLE");
  });

  it("rejects a scan for other symbols than requested", () => {
    expect(reconcileScan(good, ["XAUUSD", "XAGUSD"]).status).toBe("UNAVAILABLE");
  });

  it("allows directional verdicts only when the backend reports FULL authority", () => {
    expect(reconcileScan(scan([row("XAUUSD", { verdict: "LONG", blockers: [] })], { verdictAuthority: "FULL" })).status).toBe("READY");
  });
});

describe("markets and watchlist storage", () => {
  it("validates markets", () => {
    expect(reconcileMarkets(markets).status).toBe("READY");
    expect(reconcileMarkets([...markets, markets[0]]).status).toBe("UNAVAILABLE");
    expect(reconcileMarkets([{ ...markets[0], assetClass: "CRYPTO" }]).status).toBe("UNAVAILABLE");
    expect(reconcileMarkets(null).status).toBe("UNAVAILABLE");
  });

  it("sanitizes against the catalog and survives broken storage", () => {
    expect(sanitizeWatchlist(["eurusd", "BTCUSD", "EURUSD", 3], CATALOG)).toEqual(["EURUSD"]);
    expect(sanitizeWatchlist("nope", CATALOG)).toEqual(CATALOG);
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveWatchlist(["XAGUSD"], storage);
    expect(store.get(WATCHLIST_KEY)).toBe('["XAGUSD"]');
    expect(readWatchlist(CATALOG, storage)).toEqual(["XAGUSD"]);
    store.set(WATCHLIST_KEY, "{corrupt");
    expect(readWatchlist(CATALOG, storage)).toEqual(CATALOG);
    const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(readWatchlist(CATALOG, throwing)).toEqual(CATALOG);
    expect(() => saveWatchlist(["XAUUSD"], throwing)).not.toThrow();
  });

  it("labels", () => {
    expect(progressLabel(row("XAUUSD"))).toBe("WATCH · stage 2");
    expect(progressLabel(row("XAUUSD", { setupProgress: 0, setupState: "NO_SETUP" }))).toBe("NO_SETUP");
    expect([ageLabel(0), ageLabel(42.4)]).toEqual(["fresh", "42s cached"]);
    expect(["#markets", "#watchlist", "#scanner", "#risk", ""].map(viewForHash)).toEqual([
      "MARKETS", "WATCHLIST", "SCANNER", "COMMAND_CENTER", "COMMAND_CENTER",
    ]);
  });
});

describe("loadScan", () => {
  it("passes symbols and filters and validates the response", async () => {
    let url = "";
    const fetcher = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(scan([row("XAUUSD")])), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadScan(null, { minScore: 40, onlySetups: true }, fetcher)).status).toBe("READY");
    expect(url).toMatch(/\/api\/v1\/scanner\?minScore=40&onlySetups=true$/);
    expect((await loadScan(["XAUUSD"], {}, fetcher)).status).toBe("READY");
    expect(url).toMatch(/symbols=XAUUSD$/);
    expect((await loadScan(["EURUSD"], {}, fetcher)).status).toBe("UNAVAILABLE"); // answered for another symbol
  });
});

describe("views", () => {
  it("scanner shows the disclaimer, rules, research badges and opens a market", () => {
    const onOpen = vi.fn();
    render(
      <ScannerView scan={{ status: "READY", scan: scan([row("XAUUSD"), row("EURUSD")]) }} busy={false}
        filters={{ minScore: "", onlySetups: false }} onFilters={() => {}} onRefresh={() => {}} onOpen={onOpen} />,
    );
    expect(screen.getByTestId("scanner-view").textContent).toContain("not a trade signal");
    expect(screen.getByTestId("ranking-rules").textContent).toContain("usable data first");
    expect(screen.getByTestId("scanner-table-row-EURUSD").textContent).toContain("RESEARCH ONLY");
    expect(screen.getByTestId("scanner-table-row-XAUUSD").textContent).toContain("VALIDATED");
    fireEvent.click(screen.getByRole("button", { name: "EURUSD" }));
    expect(onOpen).toHaveBeenCalledWith("EURUSD");
  });

  it("scanner changes filters and shows an unavailable scan", () => {
    const onFilters = vi.fn();
    render(
      <ScannerView scan={{ status: "UNAVAILABLE", reason: "Rejected scan: authority claimed" }} busy={false}
        filters={{ minScore: "", onlySetups: false }} onFilters={onFilters} onRefresh={() => {}} onOpen={() => {}} />,
    );
    expect(screen.getByTestId("scan-unavailable").textContent).toContain("authority claimed");
    fireEvent.change(screen.getByLabelText("Minimum score"), { target: { value: "60" } });
    expect(onFilters).toHaveBeenCalledWith({ minScore: "60", onlySetups: false });
  });

  it("markets toggles the watchlist; an empty watchlist explains itself", () => {
    const onToggle = vi.fn();
    render(<MarketsView markets={{ status: "READY", markets }} watchlist={["XAUUSD"]} activeSymbol="XAUUSD" onToggle={onToggle} onOpen={() => {}} />);
    expect((screen.getByLabelText("Watch XAUUSD") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText("Watch EURUSD"));
    expect(onToggle).toHaveBeenCalledWith("EURUSD");
    cleanup();
    render(<WatchlistView watchlist={[]} scan={null} busy={false} onRefresh={() => {}} onOpen={() => {}} />);
    expect(screen.getByTestId("watchlist-empty")).toBeTruthy();
  });

  it("chart panel flags research-only markets and nav marks the active view", () => {
    render(<ChartPanel symbol="EURUSD" timeframe="M5" state={null} onTimeframeChange={() => {}} researchOnly />);
    expect(screen.getByTestId("research-only").textContent).toContain("EURUSD is not deeply validated");
    cleanup();
    render(<LeftNav view="SCANNER" />);
    expect(screen.getByText("Scanner").getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Command Center").getAttribute("aria-current")).toBeNull();
  });
});

describe("page symbol switching", () => {
  it("opens a scanned market and never shows the previous market's context", async () => {
    const requests: string[] = [];
    const decision = (symbol: string) => ({
      symbol, verdict: "WAIT", direction: null, setupType: null, setupState: "WATCH", setupGrade: null, setupScore: null,
      decisionConfidence: "LOW", htfBias: symbol === "XAUUSD" ? "BULLISH" : "BEARISH", primaryDol: null, secondaryDol: null,
      liquidityEvent: null, structureEvent: null, displacement: null, pdArray: null, noWickState: null, sessionState: null, macroState: null,
      entryZone: null, preferredEntry: null, stop: null, tp1: null, tp2: null, tp3: null, rr: null, riskStatus: "NOT_CONFIGURED",
      blockers: symbol === "XAUUSD" ? ["ANALYSIS_GATES_NOT_IMPLEMENTED"] : ["MARKET_NOT_VALIDATED", "ANALYSIS_GATES_NOT_IMPLEMENTED"],
      nextRequiredEvent: "x", invalidation: null, dataQuality: "CURRENT", strategyVersion: "0.20.0-phase20", updatedAt: T,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const url = String(input);
      requests.push(url);
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url.endsWith("/api/v1/system/status")) return json({ verdictAuthority: "FAIL_SAFE_ONLY", phase: 10, phaseName: "Watchlist & Scanner" });
      if (url.endsWith("/api/v1/markets")) return json(markets);
      if (url.includes("/api/v1/scanner")) return json(scan([row("XAUUSD"), row("EURUSD")], { requestedSymbols: ["XAUUSD", "EURUSD"] }));
      const m = url.match(/\/api\/v1\/market-state\/([A-Z]+)/);
      if (m) return json({ decision: decision(m[1]!), data: null });
      return new Response("{}", { status: 404 });
    }));
    const { default: Page } = await import("../src/app/page");
    render(<Page />);
    await waitFor(() => expect(screen.getByTestId("verdict").textContent).toBe("WAIT"));
    expect(screen.queryByTestId("research-only")).toBeNull();

    await act(async () => {
      window.location.hash = "#scanner";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await waitFor(() => expect(screen.getByTestId("scanner-table-row-EURUSD")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "EURUSD" }));

    await waitFor(() => expect(screen.getByTestId("research-only")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("BEARISH")).toBeTruthy());
    expect(screen.queryByText("BULLISH")).toBeNull(); // the XAUUSD HTF bias is gone
    expect(requests.some((u) => u.endsWith("/api/v1/market-state/EURUSD"))).toBe(true);
    expect(requests.some((u) => /candles\/EURUSD\?timeframe=M5/.test(u))).toBe(true);
  });
});
