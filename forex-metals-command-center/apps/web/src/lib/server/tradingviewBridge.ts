/**
 * Server-only MCP client for the local TradingView bridge (github.com/tradesdontlie/tradingview-mcp).
 * Spawned once per server process over stdio and reused; reset after any failure.
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { normalizeResolution, TV_DISCONNECTED, type TvChartState, type TvTimeframeCode } from "@/lib/tradingview";

const CALL_TIMEOUT_MS = 20_000;

type BridgeGlobal = { __fmccTvClient?: Promise<Client> };
const holder = globalThis as BridgeGlobal; // survives dev hot reloads

function serverPath(): string | null {
  const p = process.env.TRADINGVIEW_MCP_SERVER_PATH;
  return p && p.trim() ? p.trim() : null;
}

async function connect(path: string): Promise<Client> {
  const client = new Client({ name: "fmcc-command-center", version: "0.2.0" });
  await client.connect(new StdioClientTransport({ command: "node", args: [path], stderr: "ignore" }));
  return client;
}

async function getClient(): Promise<Client> {
  const path = serverPath();
  if (!path) throw new Error("TRADINGVIEW_MCP_SERVER_PATH is not configured");
  holder.__fmccTvClient ??= connect(path);
  try {
    return await holder.__fmccTvClient;
  } catch (err) {
    holder.__fmccTvClient = undefined;
    throw err;
  }
}

async function callTool(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  try {
    const client = await getClient();
    const result = await client.callTool({ name, arguments: args }, undefined, { timeout: CALL_TIMEOUT_MS });
    const content = Array.isArray(result.content) ? result.content : [];
    const text = content.find((c): c is { type: "text"; text: string } => c?.type === "text")?.text;
    const parsed: unknown = text ? JSON.parse(text) : null;
    if (typeof parsed !== "object" || parsed === null) throw new Error(`${name} returned no data`);
    const body = parsed as Record<string, unknown>;
    if (result.isError || body.success === false) {
      throw new Error(typeof body.error === "string" ? body.error : `${name} failed`);
    }
    return body;
  } catch (err) {
    // Drop the client so the next request re-spawns a fresh bridge.
    const stale = holder.__fmccTvClient;
    holder.__fmccTvClient = undefined;
    void stale?.then((c) => c.close()).catch(() => undefined);
    throw err;
  }
}

function disconnected(err: unknown): TvChartState {
  return { ...TV_DISCONNECTED, error: err instanceof Error ? err.message : TV_DISCONNECTED.error };
}

export async function getTvChartState(): Promise<TvChartState> {
  try {
    const state = await callTool("chart_get_state", {});
    const resolution = typeof state.resolution === "string" ? normalizeResolution(state.resolution) : null;
    return {
      connected: resolution !== null,
      symbol: typeof state.symbol === "string" ? state.symbol : null,
      resolution,
      error: resolution === null ? "TradingView reported no timeframe" : null,
    };
  } catch (err) {
    return disconnected(err);
  }
}

/** Sets the timeframe, then reads the chart back so the UI reflects what TradingView actually shows. */
export async function setTvChartTimeframe(code: TvTimeframeCode): Promise<TvChartState> {
  try {
    await callTool("chart_set_timeframe", { timeframe: code });
  } catch (err) {
    return disconnected(err);
  }
  const state = await getTvChartState();
  if (state.connected && state.resolution !== code) {
    return { ...state, error: `Requested ${code}, TradingView shows ${state.resolution}` };
  }
  return state;
}
