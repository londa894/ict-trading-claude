import { beforeEach, describe, expect, it, vi } from "vitest";

const bridge = vi.hoisted(() => ({
  getTvChartState: vi.fn(),
  setTvChartTimeframe: vi.fn(),
}));
vi.mock("../src/lib/server/tradingviewBridge", () => bridge);

import { GET, POST } from "../src/app/api/tradingview/timeframe/route";

const ok = { connected: true, symbol: "FOREXCOM:XAUUSD", resolution: "60", error: null };
const post = (body: unknown, host = "localhost:3000") =>
  new Request(`http://${host}/api/tradingview/timeframe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

beforeEach(() => {
  bridge.getTvChartState.mockReset().mockResolvedValue(ok);
  bridge.setTvChartTimeframe.mockReset().mockResolvedValue(ok);
});

describe("/api/tradingview/timeframe", () => {
  it("GET returns the chart state", async () => {
    const res = await GET(new Request("http://127.0.0.1:3000/api/tradingview/timeframe"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(ok);
  });

  it("POST forwards an allow-listed timeframe to chart_set_timeframe", async () => {
    const res = await POST(post({ timeframe: "60" }));
    expect(res.status).toBe(200);
    expect(bridge.setTvChartTimeframe).toHaveBeenCalledWith("60");
  });

  it.each([{ timeframe: "45" }, { timeframe: 60 }, {}, "not json"])("POST rejects %j with 422", async (body) => {
    const res = await POST(post(body));
    expect(res.status).toBe(422);
    expect(bridge.setTvChartTimeframe).not.toHaveBeenCalled();
  });

  it("refuses non-local hosts (it drives the desktop app on this machine)", async () => {
    expect((await GET(new Request("http://192.168.1.20:3000/api/tradingview/timeframe"))).status).toBe(403);
    expect((await POST(post({ timeframe: "60" }, "example.com"))).status).toBe(403);
    expect(bridge.setTvChartTimeframe).not.toHaveBeenCalled();
    expect(bridge.getTvChartState).not.toHaveBeenCalled();
  });
});
