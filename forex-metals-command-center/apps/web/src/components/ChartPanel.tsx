"use client";

import { CHART_TIMEFRAMES, type ChartTimeframe } from "@fmcc/shared-types";
import { useMemo, useState, type ReactNode } from "react";

import type { ChartState } from "@/lib/candles";
import { buildLiquidityOverlay, liquiditySyncError, type LiquidityLoadState } from "@/lib/liquidity";
import { buildNoWickOverlay, noWickSyncError, type NoWickLoadState } from "@/lib/noWick";
import {
  buildSessionOverlay,
  DISPLAY_ZONES,
  SESSION_TIMEFRAMES,
  type DisplayZone,
  type SessionLoadState,
} from "@/lib/sessions";
import { buildNewsOverlay, type NewsLoadState } from "@/lib/news";
import { buildSetupOverlay, SETUP_TIMEFRAME, setupSyncError, type SetupLoadState } from "@/lib/setups";
import { buildPdArrayOverlay, pdArraySyncError, type PdArrayLoadState } from "@/lib/pdArrays";
import { buildOverlay, mergeOverlays, overlaySyncError, type Overlay, type StructureLoadState } from "@/lib/structure";

import { CandleChart } from "./CandleChart";

const DERIVED_NOTE: Partial<Record<ChartTimeframe, string>> = {
  H4: "H4 buckets: New York 17:00 close, derived from H1",
  D1: "D1 buckets: New York 17:00 close, derived from H1",
};

type Props = {
  symbol: string;
  timeframe: ChartTimeframe;
  state: ChartState | null;
  structure?: StructureLoadState | null;
  liquidity?: LiquidityLoadState | null;
  pdArrays?: PdArrayLoadState | null;
  noWick?: NoWickLoadState | null;
  sessions?: SessionLoadState | null;
  setups?: SetupLoadState | null;
  onTimeframeChange: (tf: ChartTimeframe) => void;
  /** Optional extra toolbar row (e.g. the TradingView Desktop timeframe switcher). */
  secondaryToolbar?: ReactNode;
  /** The Master Decision carries MARKET_NOT_VALIDATED: strategy parameters are not validated on this market. */
  researchOnly?: boolean;
  news?: NewsLoadState | null;
};

export type OverlayResolution = { overlay: Overlay | null; note: string | null };

/** Pure decision: draw the structure overlay only when both payloads are trusted and aligned. */
export function resolveOverlay(
  chart: ChartState | null,
  structure: StructureLoadState | null | undefined,
  options: { external: boolean; internal: boolean },
): OverlayResolution {
  if (!options.external && !options.internal) return { overlay: null, note: null };
  if (chart?.status !== "READY") return { overlay: null, note: null };
  if (!structure) return { overlay: null, note: "Structure loading…" };
  if (structure.status !== "READY") return { overlay: null, note: `Structure hidden: ${structure.reason}` };
  if (structure.analysis.timeframe !== chart.timeframe) return { overlay: null, note: "Structure hidden: timeframe out of sync" };
  const syncError = overlaySyncError(structure.analysis, chart.candles);
  if (syncError) return { overlay: null, note: `Structure hidden: ${syncError} (refreshing)` };
  return { overlay: buildOverlay(structure.analysis, options), note: null };
}

/** Liquidity overlay: same trust rules as structure (READY, same timeframe, anchors on the drawn candles). */
export function resolveLiquidityOverlay(
  chart: ChartState | null,
  liquidity: LiquidityLoadState | null | undefined,
  enabled: boolean,
): OverlayResolution {
  if (!enabled || chart?.status !== "READY") return { overlay: null, note: null };
  if (!liquidity) return { overlay: null, note: "Liquidity loading…" };
  if (liquidity.status !== "READY") return { overlay: null, note: `Liquidity hidden: ${liquidity.reason}` };
  if (liquidity.analysis.timeframe !== chart.timeframe) return { overlay: null, note: "Liquidity hidden: timeframe out of sync" };
  const syncError = liquiditySyncError(liquidity.analysis, chart.candles);
  if (syncError) return { overlay: null, note: `Liquidity hidden: ${syncError} (refreshing)` };
  return { overlay: buildLiquidityOverlay(liquidity.analysis), note: null };
}

/** FVG/IFVG overlay: same trust rules (READY, same timeframe, anchors on the drawn candles). */
export function resolvePdArrayOverlay(
  chart: ChartState | null,
  pdArrays: PdArrayLoadState | null | undefined,
  enabled: boolean,
): OverlayResolution {
  if (!enabled || chart?.status !== "READY") return { overlay: null, note: null };
  if (!pdArrays) return { overlay: null, note: "FVG loading…" };
  if (pdArrays.status !== "READY") return { overlay: null, note: `FVG hidden: ${pdArrays.reason}` };
  if (pdArrays.analysis.timeframe !== chart.timeframe) return { overlay: null, note: "FVG hidden: timeframe out of sync" };
  const syncError = pdArraySyncError(pdArrays.analysis, chart.candles);
  if (syncError) return { overlay: null, note: `FVG hidden: ${syncError} (refreshing)` };
  return { overlay: buildPdArrayOverlay(pdArrays.analysis, chart.candles), note: null };
}

/** No Wick overlay: same trust rules (READY, same timeframe, anchors on the drawn candles). */
export function resolveNoWickOverlay(
  chart: ChartState | null,
  noWick: NoWickLoadState | null | undefined,
  enabled: boolean,
): OverlayResolution {
  if (!enabled || chart?.status !== "READY") return { overlay: null, note: null };
  if (!noWick) return { overlay: null, note: "No Wick loading…" };
  if (noWick.status !== "READY") return { overlay: null, note: `No Wick hidden: ${noWick.reason}` };
  if (noWick.analysis.timeframe !== chart.timeframe) return { overlay: null, note: "No Wick hidden: timeframe out of sync" };
  const syncError = noWickSyncError(noWick.analysis, chart.candles);
  if (syncError) return { overlay: null, note: `No Wick hidden: ${syncError} (refreshing)` };
  return { overlay: buildNoWickOverlay(noWick.analysis, chart.candles), note: null };
}

/** Session high/low segments: sessions READY, intraday chart only; windows are snapped to drawn candles. */
export function resolveSessionOverlay(
  chart: ChartState | null,
  sessions: SessionLoadState | null | undefined,
  enabled: boolean,
): OverlayResolution {
  if (!enabled || chart?.status !== "READY") return { overlay: null, note: null };
  if (!SESSION_TIMEFRAMES.has(chart.timeframe)) return { overlay: null, note: `Sessions hidden on ${chart.timeframe}` };
  if (!sessions) return { overlay: null, note: "Sessions loading…" };
  if (sessions.status !== "READY") return { overlay: null, note: `Sessions hidden: ${sessions.reason}` };
  return { overlay: buildSessionOverlay(sessions.analysis, chart.candles), note: null };
}

/** Setup milestones: setups READY, chart on the setup timeframe, every milestone on the drawn candles. */
export function resolveSetupOverlay(
  chart: ChartState | null,
  setups: SetupLoadState | null | undefined,
  enabled: boolean,
): OverlayResolution {
  if (!enabled || chart?.status !== "READY") return { overlay: null, note: null };
  if (chart.timeframe !== SETUP_TIMEFRAME) return { overlay: null, note: `Setups shown on ${SETUP_TIMEFRAME} only` };
  if (!setups) return { overlay: null, note: "Setups loading…" };
  if (setups.status !== "READY") return { overlay: null, note: `Setups hidden: ${setups.reason}` };
  const syncError = setupSyncError(setups.analysis, chart.candles);
  if (syncError) return { overlay: null, note: `Setups hidden: ${syncError} (refreshing)` };
  return { overlay: buildSetupOverlay(setups.analysis), note: null };
}

export function ChartPanel({
  symbol,
  timeframe,
  state,
  structure = null,
  liquidity = null,
  pdArrays = null,
  noWick = null,
  sessions = null,
  setups = null,
  onTimeframeChange,
  secondaryToolbar,
  researchOnly = false,
  news = null,
}: Props) {
  const [showExternal, setShowExternal] = useState(true);
  const [showInternal, setShowInternal] = useState(false);
  const [showLiquidity, setShowLiquidity] = useState(true);
  const [showFvg, setShowFvg] = useState(true);
  const [showNoWick, setShowNoWick] = useState(false);
  const [showSessions, setShowSessions] = useState(false);
  const [showSetups, setShowSetups] = useState(false);
  const [showNews, setShowNews] = useState(true);
  const [timeZone, setTimeZone] = useState<DisplayZone>("UTC");
  const quality = state?.quality ?? "DISCONNECTED";
  const { overlay, note } = useMemo(() => {
    const s = resolveOverlay(state, structure, { external: showExternal, internal: showInternal });
    const l = resolveLiquidityOverlay(state, liquidity, showLiquidity);
    const p = resolvePdArrayOverlay(state, pdArrays, showFvg);
    const n = resolveNoWickOverlay(state, noWick, showNoWick);
    const ses = resolveSessionOverlay(state, sessions, showSessions);
    const st = resolveSetupOverlay(state, setups, showSetups);
    const nw =
      showNews && state?.status === "READY" && news?.status === "READY" && news.news.symbol === state.symbol
        ? buildNewsOverlay(news.news, state)
        : null;
    return {
      overlay: mergeOverlays(s.overlay, l.overlay, p.overlay, n.overlay, ses.overlay, st.overlay, nw),
      note: [s.note, l.note, p.note, n.note, ses.note, st.note].filter(Boolean).join(" · ") || null,
    };
  }, [state, structure, liquidity, pdArrays, noWick, sessions, setups, showExternal, showInternal, showLiquidity, showFvg, showNoWick, showSessions, showSetups, news, showNews]);

  return (
    <section className="chart-panel" aria-label="Chart">
      <div className="chart-toolbar">
        <strong>{symbol}</strong>
        <div role="group" aria-label="Timeframe" className="tf-group">
          {CHART_TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              aria-pressed={tf === timeframe}
              className={tf === timeframe ? "tf active" : "tf"}
              onClick={() => onTimeframeChange(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
        <span className={`badge q-${quality}`} data-testid="chart-quality">
          {state ? quality : "LOADING"}
        </span>
        <label className="toggle">
          <input type="checkbox" checked={showExternal} onChange={(e) => setShowExternal(e.target.checked)} /> Structure
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showInternal} onChange={(e) => setShowInternal(e.target.checked)} /> Internal
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showLiquidity} onChange={(e) => setShowLiquidity(e.target.checked)} /> Liquidity
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showFvg} onChange={(e) => setShowFvg(e.target.checked)} /> FVG
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showNoWick} onChange={(e) => setShowNoWick(e.target.checked)} /> No Wick
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showSessions} onChange={(e) => setShowSessions(e.target.checked)} /> Sessions
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showSetups} onChange={(e) => setShowSetups(e.target.checked)} /> Setup
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showNews} onChange={(e) => setShowNews(e.target.checked)} /> News
        </label>
        <label className="toggle">
          Times
          <select
            aria-label="Chart time zone"
            className="tz-select"
            value={timeZone}
            onChange={(e) => setTimeZone(e.target.value as DisplayZone)}
          >
            {DISPLAY_ZONES.map((z) => (
              <option key={z.id} value={z.id}>
                {z.label}
              </option>
            ))}
          </select>
        </label>
        {DERIVED_NOTE[timeframe] && <span className="muted">{DERIVED_NOTE[timeframe]}</span>}
        {note && (
          <span className="muted" data-testid="overlay-note">
            {note}
          </span>
        )}
      </div>
      {secondaryToolbar}

      {researchOnly && (
        <p className="banner" role="status" data-testid="research-only">
          RESEARCH ONLY — {symbol} is not deeply validated. Strategy parameters are validated on XAUUSD first; its decision
          stays blocked by MARKET_NOT_VALIDATED.
        </p>
      )}
      {state?.isSynthetic && (
        <p className="banner" role="alert">
          SYNTHETIC fixture data — generated for development, not market facts.
        </p>
      )}
      {state?.status === "READY" && (state.quality === "STALE" || state.quality === "DELAYED") && (
        <p className="banner" role="status">
          Data is {state.quality}: the latest candles may not reflect the current market.
        </p>
      )}

      <div className="chart-body">
        {state === null && <div className="chart-overlay">Loading {timeframe} candles…</div>}
        {state?.status === "UNAVAILABLE" && (
          <div className="chart-overlay" role="alert" data-testid="chart-unavailable">
            <strong>CHART DATA UNAVAILABLE</strong>
            <span>{state.reason}</span>
          </div>
        )}
        {state?.status === "READY" && <CandleChart candles={state.candles} timeframe={timeframe} overlay={overlay} timeZone={timeZone} />}
      </div>
    </section>
  );
}
