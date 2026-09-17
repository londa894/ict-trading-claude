import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

const fake = (variant = "") =>
  fileURLToPath(new URL(`./fixtures/fake-tradingview-mcp${variant ? `-${variant}` : ""}.mjs`, import.meta.url));

async function freshBridge(path: string) {
  vi.resetModules();
  delete (globalThis as { __fmccTvClient?: unknown }).__fmccTvClient;
  vi.stubEnv("TRADINGVIEW_MCP_SERVER_PATH", path);
  return import("../src/lib/server/tradingviewBridge");
}

afterEach(async () => {
  const pending = (globalThis as { __fmccTvClient?: Promise<{ close(): Promise<void> }> }).__fmccTvClient;
  delete (globalThis as { __fmccTvClient?: unknown }).__fmccTvClient;
  await pending?.then((c) => c.close()).catch(() => undefined);
  vi.unstubAllEnvs();
});

describe("TradingView MCP bridge client (real stdio MCP round-trip against a fake bridge)", () => {
  it("reads chart state", async () => {
    const bridge = await freshBridge(fake());
    expect(await bridge.getTvChartState()).toEqual({
      connected: true,
      symbol: "FOREXCOM:XAUUSD",
      resolution: "30",
      error: null,
    });
  }, 30_000);

  it("sets the timeframe via chart_set_timeframe and reflects what TradingView reports back", async () => {
    const bridge = await freshBridge(fake());
    expect((await bridge.setTvChartTimeframe("240")).resolution).toBe("240");
    const daily = await bridge.setTvChartTimeframe("D"); // fake reports "1D", normalised to "D"
    expect(daily).toMatchObject({ connected: true, resolution: "D", error: null });
  }, 30_000);

  it("reports a mismatch if TradingView did not change", async () => {
    const bridge = await freshBridge(fake("stuck"));
    const state = await bridge.setTvChartTimeframe("5");
    expect(state).toMatchObject({ connected: true, resolution: "30" });
    expect(state.error).toMatch(/Requested 5, TradingView shows 30/);
  }, 30_000);

  it("tool errors (e.g. CDP down) become NOT CONNECTED with the bridge's message", async () => {
    const bridge = await freshBridge(fake("error"));
    expect(await bridge.getTvChartState()).toMatchObject({ connected: false, error: "CDP connection refused" });
  }, 30_000);

  it("unconfigured path fails safe without spawning anything", async () => {
    const bridge = await freshBridge("");
    expect(await bridge.getTvChartState()).toMatchObject({
      connected: false,
      error: "TRADINGVIEW_MCP_SERVER_PATH is not configured",
    });
  });
});
