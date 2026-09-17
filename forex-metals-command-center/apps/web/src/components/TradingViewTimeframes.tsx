"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  labelForResolution,
  loadTvState,
  normalizeResolution,
  setTvTimeframe,
  TV_TIMEFRAMES,
  type TvChartState,
  type TvTimeframeCode,
} from "@/lib/tradingview";

const REFRESH_MS = 15_000;

type Deps = { load?: typeof loadTvState; set?: typeof setTvTimeframe; refreshMs?: number };

/**
 * Timeframe switcher for the user's TradingView Desktop chart (via the local MCP bridge). Independent of
 * the in-app chart/analysis timeframe. The highlighted button is always what TradingView reports.
 */
export function TradingViewTimeframes({ load = loadTvState, set = setTvTimeframe, refreshMs = REFRESH_MS }: Deps) {
  const [state, setState] = useState<TvChartState | null>(null);
  const [pending, setPending] = useState<TvTimeframeCode | null>(null);
  const switching = useRef(false); // a poll result must not overwrite an in-flight switch

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const next = await load();
      if (active && !switching.current) setState(next);
    };
    void refresh();
    const timer = setInterval(() => void refresh(), refreshMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [load, refreshMs]);

  const choose = useCallback(
    async (code: TvTimeframeCode) => {
      switching.current = true;
      setPending(code);
      const next = await set(code);
      setState(next);
      switching.current = false;
      setPending(null);
    },
    [set],
  );

  const active = state?.connected && state.resolution ? normalizeResolution(state.resolution) : null;

  return (
    <div className="chart-toolbar" aria-label="TradingView Desktop">
      <strong>TradingView</strong>
      <div role="group" aria-label="TradingView timeframe" className="tf-group">
        {TV_TIMEFRAMES.map(({ label, code }) => (
          <button
            key={code}
            type="button"
            aria-pressed={code === active}
            className={code === active ? "tf active" : "tf"}
            disabled={pending !== null || !state?.connected}
            onClick={() => void choose(code)}
          >
            {label}
          </button>
        ))}
      </div>
      <span className={`badge ${state?.connected ? "q-CURRENT" : "q-DISCONNECTED"}`} data-testid="tv-status">
        {state === null ? "LOADING" : state.connected ? "CONNECTED" : "NOT CONNECTED"}
      </span>
      {pending !== null && <span className="muted">Switching to {labelForResolution(pending)}…</span>}
      {pending === null && state?.connected && (
        <span className="muted" data-testid="tv-active">
          {state.symbol ?? "—"} · {labelForResolution(state.resolution)}
        </span>
      )}
      {pending === null && state?.error && (
        <span className="muted" data-testid="tv-error">
          {state.error}
        </span>
      )}
    </div>
  );
}
