import { getTvChartState, setTvChartTimeframe } from "@/lib/server/tradingviewBridge";
import { isTvTimeframeCode } from "@/lib/tradingview";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** The bridge drives the user's own desktop app: only serve requests addressed to this machine. */
function isLocal(request: Request): boolean {
  const host = new URL(request.url).hostname;
  return LOCAL_HOSTS.has(host);
}

const forbidden = () => Response.json({ connected: false, error: "local requests only" }, { status: 403 });

export async function GET(request: Request) {
  if (!isLocal(request)) return forbidden();
  return Response.json(await getTvChartState());
}

export async function POST(request: Request) {
  if (!isLocal(request)) return forbidden();
  const body: unknown = await request.json().catch(() => null);
  const timeframe = typeof body === "object" && body !== null ? (body as { timeframe?: unknown }).timeframe : undefined;
  if (!isTvTimeframeCode(timeframe)) {
    return Response.json({ connected: false, error: "unsupported timeframe" }, { status: 422 });
  }
  return Response.json(await setTvChartTimeframe(timeframe));
}
