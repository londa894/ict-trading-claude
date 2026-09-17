// Minimal stand-in for the tradingview-mcp bridge (same tool names and JSON-in-text response format).
// FAKE_TV_MODE: "ok" (default) | "error" (tools report success:false) | "stuck" (ignores set requests)
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const mode = process.env.FAKE_TV_MODE ?? "ok";
let resolution = "30";
const json = (obj, isError = false) => ({ content: [{ type: "text", text: JSON.stringify(obj) }], ...(isError && { isError: true }) });

const server = new Server({ name: "fake-tradingview", version: "0.0.0" }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    { name: "chart_get_state", inputSchema: { type: "object", properties: {} } },
    { name: "chart_set_timeframe", inputSchema: { type: "object", properties: { timeframe: { type: "string" } } } },
  ],
}));
server.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (mode === "error") return json({ success: false, error: "CDP connection refused" }, true);
  if (req.params.name === "chart_get_state") {
    // Mirror TradingView: daily/weekly/monthly come back as 1D/1W/1M.
    const reported = ["D", "W", "M"].includes(resolution) ? `1${resolution}` : resolution;
    return json({ success: true, symbol: "FOREXCOM:XAUUSD", resolution: reported, chartType: 1, studies: [] });
  }
  if (req.params.name === "chart_set_timeframe") {
    if (mode !== "stuck") resolution = String(req.params.arguments?.timeframe);
    return json({ success: true, timeframe: req.params.arguments?.timeframe, chart_ready: true });
  }
  return json({ success: false, error: "unknown tool" }, true);
});
await server.connect(new StdioServerTransport());
