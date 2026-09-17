// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TradingViewTimeframes } from "../src/components/TradingViewTimeframes";
import {
  isTvTimeframeCode,
  labelForResolution,
  loadTvState,
  normalizeResolution,
  reconcileTvState,
  setTvTimeframe,
  TV_TIMEFRAMES,
  type TvChartState,
} from "../src/lib/tradingview";

afterEach(cleanup);

const connected = (resolution: string): TvChartState => ({
  connected: true,
  symbol: "FOREXCOM:XAUUSD",
  resolution,
  error: null,
});

describe("timeframe catalogue", () => {
  it("matches the TradingView toolbar order", () => {
    expect(TV_TIMEFRAMES.map((t) => t.label)).toEqual([
      "1m", "2m", "5m", "15m", "30m", "1h", "2h", "3h", "4h", "6h", "8h", "10h", "12h", "16h", "D", "2D", "3D", "W", "M",
    ]);
    expect(TV_TIMEFRAMES.find((t) => t.label === "16h")?.code).toBe("960");
  });

  it("normalises TradingView's 1D/1W/1M and validates codes", () => {
    expect(normalizeResolution("1D")).toBe("D");
    expect(normalizeResolution("1W")).toBe("W");
    expect(normalizeResolution("2D")).toBe("2D");
    expect(normalizeResolution("15")).toBe("15");
    expect(labelForResolution("240")).toBe("4h");
    expect(labelForResolution("1M")).toBe("M");
    expect(isTvTimeframeCode("60")).toBe(true);
    expect(isTvTimeframeCode("45")).toBe(false);
    expect(isTvTimeframeCode(15)).toBe(false);
  });
});

describe("reconcileTvState", () => {
  it.each([null, "x", {}, { connected: "yes" }])("malformed %j -> disconnected", (payload) => {
    expect(reconcileTvState(payload).connected).toBe(false);
  });
  it("connected without a resolution is not trusted", () => {
    expect(reconcileTvState({ connected: true, resolution: "" })).toMatchObject({ connected: false });
  });
  it("keeps the bridge error when disconnected", () => {
    expect(reconcileTvState({ connected: false, error: "CDP down" }).error).toBe("CDP down");
  });
});

describe("client loaders", () => {
  it("GET and POST the local route", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify(connected("15")), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await loadTvState(fetcher)).resolution).toBe("15");
    await setTvTimeframe("15", fetcher);
    expect(calls[0]![0]).toBe("/api/tradingview/timeframe");
    expect(calls[1]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ timeframe: "15" }) });
  });
  it("network failure -> disconnected", async () => {
    const failing = (async () => {
      throw new TypeError("down");
    }) as unknown as typeof fetch;
    expect((await loadTvState(failing)).connected).toBe(false);
  });
});

describe("TradingViewTimeframes", () => {
  beforeEach(() => vi.useRealTimers());

  it("highlights the timeframe TradingView reports and uses the existing tf button style", async () => {
    render(<TradingViewTimeframes load={async () => connected("1D")} set={vi.fn()} />);
    const group = screen.getByRole("group", { name: "TradingView timeframe" });
    await waitFor(() => expect(within(group).getByText("D").getAttribute("aria-pressed")).toBe("true"));
    expect(within(group).getAllByRole("button")).toHaveLength(19);
    expect(within(group).getByText("D").className).toBe("tf active");
    expect(within(group).getByText("1h").className).toBe("tf");
    expect(screen.getByTestId("tv-status").textContent).toBe("CONNECTED");
    expect(screen.getByTestId("tv-active").textContent).toBe("FOREXCOM:XAUUSD · D");
  });

  it("clicking calls set with the TradingView code, then shows the reported timeframe", async () => {
    let resolve!: (s: TvChartState) => void;
    const set = vi.fn(() => new Promise<TvChartState>((r) => (resolve = r)));
    render(<TradingViewTimeframes load={async () => connected("30")} set={set} />);
    const group = screen.getByRole("group", { name: "TradingView timeframe" });
    await waitFor(() => expect(within(group).getByText("30m").getAttribute("aria-pressed")).toBe("true"));

    fireEvent.click(within(group).getByText("4h"));
    expect(set).toHaveBeenCalledWith("240");
    expect(screen.getByText("Switching to 4h…")).toBeTruthy();
    expect(within(group).getAllByRole("button").every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    // Highlight doesn't move until TradingView confirms.
    expect(within(group).getByText("30m").getAttribute("aria-pressed")).toBe("true");

    await act(async () => resolve(connected("240")));
    expect(within(group).getByText("4h").getAttribute("aria-pressed")).toBe("true");
    expect(within(group).getByText("30m").getAttribute("aria-pressed")).toBe("false");
  });

  it("not connected: buttons disabled, nothing highlighted, reason shown", async () => {
    render(
      <TradingViewTimeframes
        load={async () => ({ connected: false, symbol: null, resolution: null, error: "CDP connection refused" })}
        set={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("tv-status").textContent).toBe("NOT CONNECTED"));
    const buttons = within(screen.getByRole("group", { name: "TradingView timeframe" })).getAllByRole("button");
    expect(buttons.every((b) => (b as HTMLButtonElement).disabled && b.getAttribute("aria-pressed") === "false")).toBe(true);
    expect(screen.getByTestId("tv-error").textContent).toBe("CDP connection refused");
  });

  it("a timeframe changed inside TradingView shows up on the next poll", async () => {
    let current = "5";
    render(<TradingViewTimeframes load={async () => connected(current)} set={vi.fn()} refreshMs={50} />);
    const group = screen.getByRole("group", { name: "TradingView timeframe" });
    await waitFor(() => expect(within(group).getByText("5m").getAttribute("aria-pressed")).toBe("true"));
    current = "960";
    await waitFor(() => expect(within(group).getByText("16h").getAttribute("aria-pressed")).toBe("true"));
  });
});
