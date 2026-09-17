// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChartPanel } from "../src/components/ChartPanel";
import { appendEvents } from "../src/components/EventLog";
import { IntelligencePanel, PANEL_TABS } from "../src/components/IntelligencePanel";
import { LeftNav } from "../src/components/LeftNav";
import { StatusBar } from "../src/components/StatusBar";
import type { ChartState } from "../src/lib/candles";
import { unavailableDecision } from "../src/lib/failsafe";

// The canvas renderer cannot run in jsdom; the panel's job is deciding WHETHER to mount it.
vi.mock("../src/components/CandleChart", () => ({
  CandleChart: ({ candles }: { candles: unknown[] }) => <div data-testid="candle-chart">{candles.length} candles</div>,
}));

afterEach(cleanup);

const NOW = new Date("2024-01-09T10:07:00Z");
const ready: ChartState = {
  status: "READY",
  symbol: "XAUUSD",
  timeframe: "M5",
  quality: "CURRENT",
  marketStatus: "OPEN",
  isSynthetic: false,
  provider: "p",
  sourceTimeframe: "M5",
  candles: [
    { time: "2024-01-09T10:00:00Z", open: 2030, high: 2031, low: 2029, close: 2030.5, volume: 1, isClosed: true },
  ],
  issues: [],
};

describe("ChartPanel", () => {
  it("mounts the renderer only for READY data", () => {
    render(<ChartPanel symbol="XAUUSD" timeframe="M5" state={ready} onTimeframeChange={() => {}} />);
    expect(screen.getByTestId("candle-chart").textContent).toBe("1 candles");
    expect(screen.queryByTestId("chart-unavailable")).toBeNull();
  });

  it("shows an unavailable overlay and no renderer when data cannot be trusted", () => {
    render(
      <ChartPanel
        symbol="XAUUSD"
        timeframe="M5"
        state={{
          status: "UNAVAILABLE",
          symbol: "XAUUSD",
          timeframe: "M5",
          quality: "INVALID",
          isSynthetic: false,
          reason: "Rejected chart payload: candle 0 has impossible OHLC",
          issues: [],
        }}
        onTimeframeChange={() => {}}
      />,
    );
    expect(screen.getByTestId("chart-unavailable").textContent).toContain("impossible OHLC");
    expect(screen.queryByTestId("candle-chart")).toBeNull();
    expect(screen.getByTestId("chart-quality").textContent).toBe("INVALID");
  });

  it("labels synthetic and stale data", () => {
    render(
      <ChartPanel
        symbol="XAUUSD"
        timeframe="D1"
        state={{ ...ready, timeframe: "D1", isSynthetic: true, quality: "STALE" }}
        onTimeframeChange={() => {}}
      />,
    );
    const alerts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(alerts.some((t) => t?.includes("SYNTHETIC"))).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("STALE");
    expect(screen.getByText(/New York 17:00 close/)).toBeTruthy();
  });

  it("switches timeframe through the toolbar", () => {
    const onChange = vi.fn();
    render(<ChartPanel symbol="XAUUSD" timeframe="M5" state={null} onTimeframeChange={onChange} />);
    const group = screen.getByRole("group", { name: "Timeframe" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["M5", "M15", "H1", "H4", "D1"]);
    fireEvent.click(within(group).getByText("H4"));
    expect(onChange).toHaveBeenCalledWith("H4");
    expect(screen.getByText(/Loading M5 candles/)).toBeTruthy();
  });
});

describe("StatusBar", () => {
  it("shows the Master Decision verdict and marks unbuilt fields N/A instead of inventing values", () => {
    const decision = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", NOW, "0.19.0-phase19");
    render(<StatusBar decision={decision} data={null} chart={ready} />);
    expect(screen.getByTestId("verdict").textContent).toBe("UNAVAILABLE");
    expect(screen.getByTestId("last-close").textContent).toBe("2030.5");
    expect(screen.getAllByText(/N\/A/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("news").textContent).toBe("UNAVAILABLE"); // no news state: never assumed clear
    expect(screen.getByTestId("score").textContent).toBe("—");
    expect(screen.getByTestId("session").textContent).toBe("UNAVAILABLE");
  });

  it("last close is UNAVAILABLE without a READY series", () => {
    const decision = unavailableDecision("XAUUSD", ["PROVIDER_UNAVAILABLE"], "x", NOW);
    render(<StatusBar decision={decision} data={null} chart={null} />);
    expect(screen.getByTestId("last-close").textContent).toBe("UNAVAILABLE");
  });
});

describe("IntelligencePanel", () => {
  const decision = unavailableDecision("XAUUSD", ["DATA_STALE", "ANALYSIS_GATES_NOT_IMPLEMENTED"], "Restore data", NOW);

  it("has the ten STEP 11 tabs and OVERVIEW shows the blockers", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(PANEL_TABS.map((t) => t.id));
    expect(screen.getByTestId("blockers").textContent).toContain("DATA_STALE");
  });

  it("unbuilt engine tabs show NOT AVAILABLE and no numbers", () => {
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false} />);
    for (const t of PANEL_TABS.filter((x) => x.availableInPhase !== null)) {
      fireEvent.click(screen.getByRole("tab", { name: t.id }));
      const body = screen.getByTestId("tab-not-available").textContent ?? "";
      expect(body).toContain("NOT AVAILABLE");
      expect(body.replace(`Phase ${t.availableInPhase}`, "")).not.toMatch(/\d/);
    }
  });
});

describe("LeftNav", () => {
  it("only enables surfaces with implemented engines", () => {
    render(<LeftNav />);
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Command Center", "Chart", "Markets", "Watchlist", "Scanner", "Setups", "Alerts", "Risk", "Journal", "Backtest", "Replay"]);
    expect(screen.getByText("Scanner").getAttribute("href")).toBe("#scanner");
    expect(screen.getByText("Setups").getAttribute("href")).toBe("#setup-progress");
    expect(screen.getByText("Risk").getAttribute("href")).toBe("#risk");
    expect(screen.getByText("Journal").getAttribute("href")).toBe("#journal");
  });
});

describe("appendEvents", () => {
  it("dedupes by key, newest first, bounded", () => {
    const e = (key: string) => ({ key, at: "2024-01-09T10:00:00Z", severity: "INFO" as const, text: key });
    let log = appendEvents([], [e("a"), e("b")]);
    expect(log.map((x) => x.key)).toEqual(["b", "a"]);
    log = appendEvents(log, [e("a"), e("c")]);
    expect(log.map((x) => x.key)).toEqual(["c", "b", "a"]);
    const many = appendEvents([], Array.from({ length: 250 }, (_, i) => e(String(i))));
    expect(many).toHaveLength(200);
  });
});

describe("STRUCTURE tab (Phase 2)", () => {
  const decision = { ...unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", NOW, "0.19.0-phase19"), htfBias: "UNKNOWN" };
  const level = {
    level: "EXTERNAL" as const,
    pivotLength: 10,
    state: "TRANSITIONING" as const,
    trend: "BULLISH" as const,
    swings: [],
    events: [],
    protectedHigh: null,
    protectedLow: null,
    barsSinceLastBreak: 4,
  };
  const analysis = {
    symbol: "XAUUSD",
    timeframe: "M15" as const,
    asOf: "2024-01-09T10:15:00Z",
    candleCount: 300,
    quality: "STALE" as const,
    isSynthetic: true,
    eligibleForDecision: false,
    ineligibility: ["DATA_SYNTHETIC" as const, "DATA_STALE" as const],
    internal: { ...level, level: "INTERNAL" as const, pivotLength: 3, state: "BEARISH" as const },
    external: level,
    events: [
      {
        id: "e",
        level: "EXTERNAL" as const,
        type: "CHOCH" as const,
        direction: "BEARISH" as const,
        status: "CONFIRMED" as const,
        confirmation: "CANDLE_CLOSE" as const,
        price: 2025.5,
        time: "2024-01-09T10:00:00Z",
        brokenSwingId: "s",
        brokenSwingTime: "2024-01-09T09:00:00Z",
        trendBefore: "BULLISH" as const,
        ambiguous: false,
        liquidityQualifier: "NOT_EVALUATED" as const,
        displacementQualifier: "NOT_EVALUATED" as const,
      },
    ],
    providerError: null,
    strategyVersion: "0.19.0-phase19",
    generatedAt: "2024-01-09T10:16:00Z",
  };

  it("is enabled and shows levels, events and why it is not used by the decision", () => {
    render(
      <IntelligencePanel
        decision={decision}
        data={null}
        status={null}
        trusted={false}
        structure={{ status: "READY", analysis }}
        alignment={{
          symbol: "XAUUSD",
          alignment: "UNCLEAR",
          htfBias: "UNKNOWN",
          eligibleForDecision: false,
          timeframes: [
            { timeframe: "D1", state: null, trend: null, quality: "STALE", eligibleForDecision: false, ineligibility: ["INSUFFICIENT_CANDLES"] },
          ],
          strategyVersion: "0.19.0-phase19",
          generatedAt: "2024-01-09T10:16:00Z",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "STRUCTURE" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("structure-eligibility").textContent).toContain("NOT used by the decision: DATA_SYNTHETIC, DATA_STALE");
    expect(screen.getByTestId("level-EXTERNAL").textContent).toContain("TRANSITIONING");
    expect(screen.getByTestId("level-INTERNAL").textContent).toContain("BEARISH");
    expect(screen.getByTestId("structure-events").textContent).toContain("CHoCH BEARISH @ 2025.5");
    expect(screen.getByTestId("mtf-alignment").textContent).toContain("UNCLEAR");
  });

  it("shows a fail-safe message when structure cannot be trusted", () => {
    render(
      <IntelligencePanel
        decision={decision}
        data={null}
        status={null}
        trusted={false}
        structure={{ status: "UNAVAILABLE", reason: "Structure API unreachable" }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "STRUCTURE" }));
    expect(screen.getByTestId("structure-unavailable").textContent).toContain("Structure API unreachable");
  });
});

describe("ChartPanel structure overlay (Phase 2)", () => {
  it("hides the overlay with a reason when structure is out of sync", () => {
    render(
      <ChartPanel
        symbol="XAUUSD"
        timeframe="M5"
        state={ready}
        structure={{ status: "UNAVAILABLE", reason: "Structure API unreachable" }}
        onTimeframeChange={() => {}}
      />,
    );
    expect(screen.getByTestId("overlay-note").textContent).toContain("Structure hidden");
    expect(screen.getByTestId("candle-chart")).toBeTruthy();
  });
});
