"use client";

import type { Alert, AlertFeed, ChartTimeframe, Direction, MtfStructureResponse } from "@fmcc/shared-types";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertsView } from "@/components/AlertsView";
import { ChartPanel } from "@/components/ChartPanel";
import { appendEvents, EventLog, type EventEntry } from "@/components/EventLog";
import { IntelligencePanel } from "@/components/IntelligencePanel";
import { BacktestView } from "@/components/BacktestView";
import { ReplayView } from "@/components/ReplayView";
import { JournalView } from "@/components/JournalView";
import { LeftNav } from "@/components/LeftNav";
import {
  MarketsView,
  ScannerView,
  viewForHash,
  WatchlistView,
  type CenterView,
  type ScanFilters,
} from "@/components/MarketsViews";
import { StatusBar } from "@/components/StatusBar";
import {
  createReadyWatch,
  deleteReadyWatch,
  loadAlerts,
  loadReadyWatches,
  loadAlignment,
  loadChartSeries,
  loadDashboard,
  loadEvaluation,
  loadLiquidity,
  loadMacro,
  loadMarkets,
  loadNews,
  loadNoWick,
  loadPdArrays,
  loadScan,
  loadSessions,
  loadSetups,
  loadStructure,
  type DashboardState,
} from "@/lib/api";
import { alertEvents, mergeAlerts, readDismissed, saveDismissed, type WatchesLoadState } from "@/lib/alerts";
import type { ChartState } from "@/lib/candles";
import type { EvaluationLoadState } from "@/lib/evaluation";
import { unavailableDecision } from "@/lib/failsafe";
import type { LiquidityLoadState } from "@/lib/liquidity";
import type { MacroLoadState } from "@/lib/macro";
import type { NewsLoadState } from "@/lib/news";
import type { NoWickLoadState } from "@/lib/noWick";
import type { PdArrayLoadState } from "@/lib/pdArrays";
import { readWatchlist, saveWatchlist, type MarketsLoadState, type ScanLoadState } from "@/lib/scanner";
import type { SessionLoadState } from "@/lib/sessions";
import type { SetupLoadState } from "@/lib/setups";
import type { StructureLoadState } from "@/lib/structure";

const DEFAULT_SYMBOL = "XAUUSD"; // primary, deeply validated market
const DEFAULT_TIMEFRAME: ChartTimeframe = "M5"; // execution timeframe
const REFRESH_MS = 15_000;
const SCAN_REFRESH_MS = 60_000;
const ALERT_REFRESH_MS = 15_000;

function initialDashboard(symbol: string): DashboardState {
  const now = new Date();
  return {
    status: null,
    market: {
      decision: unavailableDecision(symbol, ["PROVIDER_UNAVAILABLE"], "Loading…", now),
      data: null,
      trusted: false,
    },
    fetchedAt: now.toISOString(),
  };
}

function decisionEvents(d: DashboardState): EventEntry[] {
  const { decision } = d.market;
  const fingerprint = `${decision.symbol}|${decision.verdict}|${decision.dataQuality}|${decision.blockers.join(",")}`;
  return [
    {
      key: `decision:${fingerprint}`,
      at: d.fetchedAt,
      severity: decision.verdict === "UNAVAILABLE" ? "WARNING" : "INFO",
      text: `${decision.symbol} decision ${decision.verdict} · data ${decision.dataQuality} · ${decision.blockers.join(", ")}`,
    },
  ];
}

function chartEvents(c: ChartState, at: string): EventEntry[] {
  const entries: EventEntry[] = c.issues.map((i) => ({
    key: `issue:${c.symbol}:${c.timeframe}:${i.code}:${i.at ?? "-"}`,
    at,
    severity: i.severity,
    text: `${c.symbol} ${c.timeframe} ${i.code} ×${i.count}: ${i.message}`,
  }));
  if (c.status === "UNAVAILABLE") {
    entries.push({ key: `chart-unavailable:${c.symbol}:${c.timeframe}:${c.reason}`, at, severity: "ERROR", text: c.reason });
  }
  return entries;
}

export default function CommandCenterPage() {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [view, setView] = useState<CenterView>("COMMAND_CENTER");
  const [dashboard, setDashboard] = useState<DashboardState>(() => initialDashboard(DEFAULT_SYMBOL));
  const [timeframe, setTimeframe] = useState<ChartTimeframe>(DEFAULT_TIMEFRAME);
  const [chart, setChart] = useState<ChartState | null>(null);
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [structure, setStructure] = useState<StructureLoadState | null>(null);
  const [alignment, setAlignment] = useState<MtfStructureResponse | null>(null);
  const [liquidity, setLiquidity] = useState<LiquidityLoadState | null>(null);
  const [pdArrays, setPdArrays] = useState<PdArrayLoadState | null>(null);
  const [noWick, setNoWick] = useState<NoWickLoadState | null>(null);
  const [sessions, setSessions] = useState<SessionLoadState | null>(null);
  const [setups, setSetups] = useState<SetupLoadState | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluationLoadState | null>(null);
  const [news, setNews] = useState<NewsLoadState | null>(null);
  const [macro, setMacro] = useState<MacroLoadState | null>(null);
  const [markets, setMarkets] = useState<MarketsLoadState | null>(null);
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [scan, setScan] = useState<ScanLoadState | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  const [filters, setFilters] = useState<ScanFilters>({ minScore: "", onlySetups: false });
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const alertCursor = useRef(0); // highest alert sequence already received
  const [alertError, setAlertError] = useState<string | null>(null);
  const [monitor, setMonitor] = useState<AlertFeed["monitor"] | null>(null);
  const [suppressed, setSuppressed] = useState<Record<string, number>>({});
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [watches, setWatches] = useState<WatchesLoadState | null>(null);
  const [watchError, setWatchError] = useState<string | null>(null);
  const authority = dashboard.status?.verdictAuthority ?? null;

  // Center view follows the URL hash (left nav links).
  useEffect(() => {
    const sync = () => setView(viewForHash(window.location.hash));
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  // Catalog + watchlist (per-viewer preference, sanitized against the catalog).
  useEffect(() => {
    let active = true;
    void loadMarkets().then((m) => {
      if (!active) return;
      setMarkets(m);
      if (m.status === "READY") setWatchlist(readWatchlist(m.markets.map((x) => x.symbol)));
    });
    return () => {
      active = false;
    };
  }, []);

  // Symbol-scoped decision data. Every state is cleared on a symbol change so contexts never mix.
  useEffect(() => {
    let active = true;
    setDashboard(initialDashboard(symbol));
    setSessions(null);
    setSetups(null);
    setEvaluation(null);
    setNews(null);
    setMacro(null);
    const refresh = async () => {
      const [next, nextSessions, nextSetups, nextEvaluation, nextNews] = await Promise.all([
        loadDashboard(symbol),
        loadSessions(symbol),
        loadSetups(symbol),
        loadEvaluation(symbol),
        loadNews(symbol),
      ]);
      // Macro follows the evaluation's open setup direction so the tab and the status bar show one state.
      const direction = nextEvaluation.status === "READY" ? nextEvaluation.evaluation.direction : null;
      const nextMacro = await loadMacro(symbol, direction);
      if (!active) return;
      setNews(nextNews);
      setMacro(nextMacro);
      setDashboard(next);
      setSessions(nextSessions);
      setSetups(nextSetups);
      setEvaluation(nextEvaluation);
      setEvents((e) => appendEvents(e, decisionEvents(next)));
    };
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [symbol]);

  useEffect(() => {
    let active = true;
    setChart(null);
    setStructure(null);
    setAlignment(null);
    setLiquidity(null);
    setPdArrays(null);
    setNoWick(null);
    const refresh = async () => {
      // Fetch candles and structure together so the overlay anchors match the drawn candles.
      const [next, nextStructure, nextAlignment, nextLiquidity, nextPdArrays, nextNoWick] = await Promise.all([
        loadChartSeries(symbol, timeframe),
        loadStructure(symbol, timeframe),
        loadAlignment(symbol),
        loadLiquidity(symbol, timeframe),
        loadPdArrays(symbol, timeframe),
        loadNoWick(symbol, timeframe),
      ]);
      if (!active) return;
      setChart(next);
      setStructure(nextStructure);
      setAlignment(nextAlignment);
      setLiquidity(nextLiquidity);
      setPdArrays(nextPdArrays);
      setNoWick(nextNoWick);
      setEvents((e) => appendEvents(e, chartEvents(next, new Date().toISOString())));
    };
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    setDismissed(new Set(readDismissed()));
  }, []);

  // Alert feed: incremental by cursor; new alerts also go to the bottom event area.
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const result = await loadAlerts(alertCursor.current, authority);
      if (!active) return;
      if (result.status === "UNAVAILABLE") {
        setAlertError(result.reason);
        return;
      }
      setAlertError(null);
      setMonitor(result.feed.monitor);
      setSuppressed(result.feed.suppressed);
      if (result.feed.alerts.length > 0) {
        alertCursor.current = result.feed.nextCursor;
        setAlerts((current) => mergeAlerts(current, result.feed.alerts));
        setEvents((e) => appendEvents(e, alertEvents(result.feed.alerts)));
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), ALERT_REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [authority]);

  const refreshWatches = useCallback(async () => {
    setWatches(await loadReadyWatches(authority));
  }, [authority]);

  useEffect(() => {
    if (view !== "ALERTS") return;
    void refreshWatches();
    const timer = setInterval(() => void refreshWatches(), ALERT_REFRESH_MS);
    return () => clearInterval(timer);
  }, [view, refreshWatches]);

  const createWatch = async (s: string, direction: Direction | "ANY") => {
    setWatchError(await createReadyWatch(s, direction));
    await refreshWatches();
  };
  const removeWatch = async (id: string) => {
    setWatchError(await deleteReadyWatch(id));
    await refreshWatches();
  };
  const dismissAlert = (id: string) => {
    setDismissed((current) => {
      const next = new Set(current).add(id);
      saveDismissed([...next]);
      return next;
    });
  };
  const restoreDismissed = () => {
    saveDismissed([]);
    setDismissed(new Set());
  };

  const runScan = useCallback(async () => {
    if (view !== "WATCHLIST" && view !== "SCANNER") return;
    if (view === "WATCHLIST" && watchlist.length === 0) return;
    setScanBusy(true);
    const minScore = filters.minScore === "" ? null : Number(filters.minScore);
    const result =
      view === "WATCHLIST"
        ? await loadScan(watchlist)
        : await loadScan(null, { minScore, onlySetups: filters.onlySetups });
    setScan(result);
    setScanBusy(false);
  }, [view, watchlist, filters]);

  // Scans run only while a scan view is open (a cold scan evaluates every market).
  useEffect(() => {
    if (view !== "WATCHLIST" && view !== "SCANNER") return;
    setScan(null);
    void runScan();
    const timer = setInterval(() => void runScan(), SCAN_REFRESH_MS);
    return () => clearInterval(timer);
  }, [view, runScan]);

  const openSymbol = (next: string) => {
    if (next !== symbol) {
      setSymbol(next);
      setTimeframe(DEFAULT_TIMEFRAME);
    }
    window.location.hash = "#command-center";
  };

  const toggleWatch = (s: string) => {
    setWatchlist((current) => {
      const next = current.includes(s) ? current.filter((x) => x !== s) : [...current, s];
      saveWatchlist(next);
      return next;
    });
  };

  const { decision, data, trusted } = dashboard.market;
  const researchOnly = decision.blockers.includes("MARKET_NOT_VALIDATED");

  return (
    <div className="shell">
      <StatusBar decision={decision} data={data} chart={chart} sessions={sessions} />
      <LeftNav view={view} />
      {view === "REPLAY" ? (
        <ReplayView symbols={markets?.status === "READY" ? markets.markets.map((m) => m.symbol) : [DEFAULT_SYMBOL]} activeSymbol={symbol} />
      ) : view === "BACKTEST" ? (
        <BacktestView symbols={markets?.status === "READY" ? markets.markets.map((m) => m.symbol) : [DEFAULT_SYMBOL]} activeSymbol={symbol} />
      ) : view === "JOURNAL" ? (
        <JournalView
          key={typeof window !== "undefined" ? window.location.hash : "journal"}
          symbols={markets?.status === "READY" ? markets.markets.map((m) => m.symbol) : [DEFAULT_SYMBOL]}
          activeSymbol={symbol}
          initialTab={
            typeof window === "undefined"
              ? "RECORDS"
              : window.location.hash === "#paper"
                ? "PAPER"
                : window.location.hash === "#analytics"
                  ? "ANALYTICS"
                  : "RECORDS"
          }
        />
      ) : view === "ALERTS" ? (
        <AlertsView
          alerts={alerts}
          feedError={alertError}
          monitor={monitor}
          suppressed={suppressed}
          dismissed={dismissed}
          onDismiss={dismissAlert}
          onRestore={restoreDismissed}
          watches={watches}
          symbols={markets?.status === "READY" ? markets.markets.map((m) => m.symbol) : [DEFAULT_SYMBOL]}
          onCreateWatch={(s, d) => void createWatch(s, d)}
          onDeleteWatch={(id) => void removeWatch(id)}
          watchError={watchError}
        />
      ) : view === "MARKETS" ? (
        <MarketsView markets={markets} watchlist={watchlist} activeSymbol={symbol} onToggle={toggleWatch} onOpen={openSymbol} />
      ) : view === "WATCHLIST" ? (
        <WatchlistView watchlist={watchlist} scan={scan} busy={scanBusy} onRefresh={() => void runScan()} onOpen={openSymbol} />
      ) : view === "SCANNER" ? (
        <ScannerView
          scan={scan}
          busy={scanBusy}
          filters={filters}
          onFilters={setFilters}
          onRefresh={() => void runScan()}
          onOpen={openSymbol}
        />
      ) : (
        <ChartPanel
          symbol={symbol}
          timeframe={timeframe}
          state={chart}
          structure={structure}
          liquidity={liquidity}
          pdArrays={pdArrays}
          noWick={noWick}
          sessions={sessions}
          setups={setups}
          onTimeframeChange={setTimeframe}
          researchOnly={researchOnly}
          news={news}
        />
      )}
      <IntelligencePanel
        key={symbol}
        decision={decision}
        data={data}
        status={dashboard.status}
        trusted={trusted}
        structure={structure}
        alignment={alignment}
        liquidity={liquidity}
        pdArrays={pdArrays}
        noWick={noWick}
        sessions={sessions}
        setups={setups}
        evaluation={evaluation}
        news={news}
        macro={macro}
      />
      <EventLog entries={events} />
    </div>
  );
}
