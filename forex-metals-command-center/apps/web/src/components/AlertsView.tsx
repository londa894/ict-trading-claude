"use client";

import {
  ALERT_CATEGORIES,
  ALERT_PRIORITIES,
  type Alert,
  type AlertFeed,
  type AlertPriority,
  type Direction,
  type ReadyWatch,
} from "@fmcc/shared-types";
import { useState } from "react";

import type { WatchesLoadState } from "@/lib/alerts";

type Filters = { category: string; minPriority: AlertPriority; symbol: string };

export function filterAlerts(alerts: readonly Alert[], f: Filters, dismissed: ReadonlySet<string>): Alert[] {
  const floor = ALERT_PRIORITIES.indexOf(f.minPriority);
  return alerts.filter(
    (a) =>
      !dismissed.has(a.id) &&
      (f.category === "" || a.category === f.category) &&
      (f.symbol === "" || a.symbol === f.symbol) &&
      ALERT_PRIORITIES.indexOf(a.priority) >= floor,
  );
}

const PRIORITY_CLASS: Record<AlertPriority, string> = {
  LOW: "muted",
  MEDIUM: "",
  HIGH: "sev-WARNING",
  CRITICAL: "sev-ERROR",
};

export function AlertsView({
  alerts,
  feedError,
  monitor,
  suppressed,
  dismissed,
  onDismiss,
  onRestore,
  watches,
  symbols,
  onCreateWatch,
  onDeleteWatch,
  watchError,
}: {
  alerts: Alert[];
  feedError: string | null;
  monitor: AlertFeed["monitor"] | null;
  suppressed: Record<string, number>;
  dismissed: ReadonlySet<string>;
  onDismiss: (id: string) => void;
  onRestore: () => void;
  watches: WatchesLoadState | null;
  symbols: string[];
  onCreateWatch: (symbol: string, direction: Direction | "ANY") => void;
  onDeleteWatch: (id: string) => void;
  watchError: string | null;
}) {
  const [filters, setFilters] = useState<Filters>({ category: "", minPriority: "LOW", symbol: "" });
  const [watchSymbol, setWatchSymbol] = useState("XAUUSD");
  const [watchDirection, setWatchDirection] = useState<Direction | "ANY">("ANY");
  const shown = filterAlerts(alerts, filters, dismissed);
  const suppressedTotal = Object.values(suppressed).reduce((a, b) => a + b, 0);

  return (
    <section className="center-view" data-testid="alerts-view">
      <h2>Alerts</h2>
      <p className="muted">
        Alerts describe changes in the deterministic engines. They are never trade instructions. Dismissing hides an
        alert in this browser only.
      </p>
      {monitor && (
        <p className="muted" data-testid="monitor-status">
          Monitor {monitor.enabled ? (monitor.running ? "running" : "idle") : "disabled"} · {monitor.symbols.join(", ")} ·
          {" "}
          {monitor.cycles} cycles · last {monitor.lastCycleAt ? monitor.lastCycleAt.replace("T", " ").slice(0, 19) : "—"}
          {monitor.lastCycleMs !== null ? ` (${monitor.lastCycleMs} ms)` : ""} · {suppressedTotal} duplicates suppressed
          {monitor.lastError ? ` · last error ${monitor.lastError}` : ""}
        </p>
      )}
      {feedError && (
        <p className="not-available" role="alert" data-testid="alerts-unavailable">
          <strong>ALERTS UNAVAILABLE</strong>
          <br />
          {feedError}
        </p>
      )}

      <h2>ALERT ME WHEN READY</h2>
      <p className="muted">
        Fires once, only when the Master Decision itself is LONG or SHORT in the watched direction under FULL verdict
        authority. While authority is FAIL_SAFE_ONLY a watch can reach GATES_PENDING but cannot fire.
      </p>
      <div className="chart-toolbar">
        <select className="tz-select" aria-label="Watch symbol" value={watchSymbol} onChange={(e) => setWatchSymbol(e.target.value)}>
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="tz-select"
          aria-label="Watch direction"
          value={watchDirection}
          onChange={(e) => setWatchDirection(e.target.value as Direction | "ANY")}
        >
          <option value="ANY">either direction</option>
          <option value="BULLISH">BULLISH</option>
          <option value="BEARISH">BEARISH</option>
        </select>
        <button type="button" className="tf" onClick={() => onCreateWatch(watchSymbol, watchDirection)}>
          Watch
        </button>
        {watchError && <span className="sev-ERROR">{watchError}</span>}
      </div>
      {watches?.status === "UNAVAILABLE" ? (
        <p className="not-available" role="alert">
          {watches.reason}
        </p>
      ) : watches?.status === "READY" && watches.watches.length > 0 ? (
        <ul className="event-list" data-testid="ready-watches">
          {watches.watches.map((w: ReadyWatch) => (
            <li key={w.id} data-testid={`watch-${w.id}`}>
              <strong>
                {w.symbol} {w.direction ?? "either direction"}
              </strong>{" "}
              · {w.state} · next: {w.nextRequiredEvent ?? "—"}{" "}
              <button type="button" className="link-button" onClick={() => onDeleteWatch(w.id)}>
                remove
              </button>
              {w.conditions.length > 0 && (
                <div className="conditions">
                  {w.conditions.map((c) => (
                    <span key={c.name} className={c.status === "MET" ? "bull" : c.status === "MISSING" ? "bear" : "muted"} title={c.detail}>
                      {c.status === "MET" ? "✓" : c.status === "MISSING" ? "✗" : "·"} {c.name}{" "}
                    </span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No ready watches.</p>
      )}

      <h2>Feed</h2>
      <div className="chart-toolbar">
        <select
          className="tz-select"
          aria-label="Alert category"
          value={filters.category}
          onChange={(e) => setFilters({ ...filters, category: e.target.value })}
        >
          <option value="">all categories</option>
          {ALERT_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          className="tz-select"
          aria-label="Minimum priority"
          value={filters.minPriority}
          onChange={(e) => setFilters({ ...filters, minPriority: e.target.value as AlertPriority })}
        >
          {ALERT_PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}+
            </option>
          ))}
        </select>
        <select
          className="tz-select"
          aria-label="Alert symbol"
          value={filters.symbol}
          onChange={(e) => setFilters({ ...filters, symbol: e.target.value })}
        >
          <option value="">all markets</option>
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {dismissed.size > 0 && (
          <button type="button" className="tf" onClick={onRestore}>
            Show {dismissed.size} dismissed
          </button>
        )}
      </div>
      {shown.length === 0 ? (
        <p className="muted" data-testid="alerts-empty">
          No alerts{alerts.length > 0 ? " match the filters" : " yet: the monitor raises alerts on the next meaningful change"}.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="scan-table" data-testid="alerts-table">
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Priority</th>
                <th>Category</th>
                <th>Alert</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((a) => (
                <tr key={a.id} data-testid={`alert-${a.seq}`}>
                  <td className="muted">{a.createdAt.replace("T", " ").slice(0, 19)}</td>
                  <td className={PRIORITY_CLASS[a.priority]}>{a.priority}</td>
                  <td>{a.category}</td>
                  <td>
                    <strong>{a.title}</strong>
                    <br />
                    <span className="muted">{a.message}</span>
                  </td>
                  <td>
                    <button type="button" className="link-button" onClick={() => onDismiss(a.id)} aria-label={`Dismiss ${a.title}`}>
                      dismiss
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
