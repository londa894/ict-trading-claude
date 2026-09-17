"use client";

import type { AnalyticsSource, EquityPoint, GroupStats } from "@fmcc/shared-types";
import { useCallback, useEffect, useState } from "react";

import { loadAnalytics, pct, rr, type AnalyticsState } from "@/lib/analytics";

export type AnalyticsApi = { load: typeof loadAnalytics };
const DEFAULT_API: AnalyticsApi = { load: loadAnalytics };

const LABEL_CLASS: Record<string, string> = {
  INSUFFICIENT: "badge q-INVALID",
  LIMITED: "badge q-STALE",
  MODERATE: "badge q-DELAYED",
  STRONGER_EVIDENCE: "badge q-CURRENT",
};

function Label({ label }: { label: string }) {
  return <span className={LABEL_CLASS[label] ?? "badge"}>{label}</span>;
}

function GroupRow({ g }: { g: GroupStats }) {
  return (
    <tr>
      <td>{g.key}</td>
      <td>
        {g.count} <Label label={g.label} />
      </td>
      <td>
        {g.wins}/{g.losses}/{g.breakeven}
      </td>
      <td>{pct(g.winRate)}</td>
      <td>{rr(g.expectancyR)}</td>
      <td>{rr(g.totalR)}</td>
      <td>{g.profitFactor ?? "—"}</td>
    </tr>
  );
}

function EquityCurve({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) return <p className="muted">Equity curve needs at least two closed records.</p>;
  const w = 600;
  const h = 120;
  const values = [0, ...points.map((p) => p.cumulativeR)];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const xy = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`);
  const zero = h - ((0 - min) / span) * h;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="Cumulative R" data-testid="analytics-equity">
      <line x1={0} x2={w} y1={zero} y2={zero} stroke="#30363d" strokeDasharray="4 4" />
      <polyline fill="none" stroke="#58a6ff" strokeWidth={2} points={xy.join(" ")} />
    </svg>
  );
}

/** Analytics V1: descriptive statistics of verified closed records, labelled by sample size. */
export function AnalyticsSection({ activeSymbol, api = DEFAULT_API }: { activeSymbol: string; api?: AnalyticsApi }) {
  const [source, setSource] = useState<AnalyticsSource>("JOURNAL");
  const [symbolOnly, setSymbolOnly] = useState(false);
  const [includeSynthetic, setIncludeSynthetic] = useState(false);
  const [state, setState] = useState<AnalyticsState | null>(null);

  const load = useCallback(async () => {
    setState(null);
    setState(await api.load({ source, symbol: symbolOnly ? activeSymbol : undefined, includeSynthetic }));
  }, [api, source, symbolOnly, includeSynthetic, activeSymbol]);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div data-testid="analytics-section">
      <div className="chart-toolbar">
        <select className="tz-select" aria-label="Analytics source" value={source} onChange={(e) => setSource(e.target.value as AnalyticsSource)}>
          <option value="JOURNAL">Journal trades</option>
          <option value="PAPER">Paper sims</option>
        </select>
        <label className="muted">
          <input type="checkbox" checked={symbolOnly} onChange={(e) => setSymbolOnly(e.target.checked)} /> {activeSymbol} only
        </label>
        <label className="muted">
          <input type="checkbox" checked={includeSynthetic} onChange={(e) => setIncludeSynthetic(e.target.checked)} /> include synthetic data
        </label>
      </div>
      {state === null ? (
        <p className="muted">Loading analytics…</p>
      ) : state.status === "UNAVAILABLE" ? (
        <p className="not-available" role="alert" data-testid="analytics-unavailable">
          {state.reason}
        </p>
      ) : !state.report.available ? (
        <p className="not-available" role="alert" data-testid="analytics-unavailable">
          <strong>ANALYTICS UNAVAILABLE</strong>
          <br />
          {state.report.reason}
        </p>
      ) : (
        <Report state={state} />
      )}
    </div>
  );
}

function Report({ state }: { state: Extract<AnalyticsState, { status: "READY" }> }) {
  const r = state.report;
  const o = r.overall;
  return (
    <>
      <p className="sev-WARNING" data-testid="analytics-disclaimer">
        {r.disclaimer}
      </p>
      <dl data-testid="analytics-overall">
        <dt>Closed records</dt>
        <dd>
          {o.count} <Label label={o.label} />
          {r.includesSynthetic ? " · includes SYNTHETIC data" : ""} · excluded: {r.excluded.tampered} tampered, {r.excluded.synthetic} synthetic,{" "}
          {r.excluded.notClosed} not closed
        </dd>
        <dt>Win / loss / break-even</dt>
        <dd>
          {o.wins} / {o.losses} / {o.breakeven} · win rate {pct(o.winRate)}
        </dd>
        <dt>R</dt>
        <dd>
          total {rr(o.totalR)} · average {rr(o.avgR)} · expectancy {rr(o.expectancyR)} · avg win {rr(o.avgWinR)} / avg non-win {rr(o.avgLossR)} ·
          profit factor {o.profitFactor ?? "— (no losing R)"}
        </dd>
        <dt>Drawdown</dt>
        <dd data-testid="analytics-drawdown">
          max {rr(-r.drawdown.maxDrawdownR)}
          {r.drawdown.recoveredAt ? ` · recovered after ${r.drawdown.recoveryTrades} records` : r.drawdown.maxDrawdownR > 0 ? " · not recovered" : ""}
        </dd>
        <dt>Execution</dt>
        <dd>
          duration avg {r.avgDurationMinutes ?? "—"} min / median {r.medianDurationMinutes ?? "—"} min · MFE {rr(r.avgMfeR)} · MAE {rr(r.avgMaeR)} · efficiency entry{" "}
          {r.avgEntryEfficiency ?? "—"} exit {r.avgExitEfficiency ?? "—"}
        </dd>
        <dt>DOL accuracy</dt>
        <dd data-testid="analytics-dol">
          {r.dol.reached}/{r.dol.evaluated} reached ({pct(r.dol.rate)}) <Label label={r.dol.label} /> · aligned {rr(r.dol.aligned.expectancyR)} ({r.dol.aligned.count} <Label label={r.dol.aligned.label} />) vs against{" "}
          {rr(r.dol.opposed.expectancyR)} ({r.dol.opposed.count} <Label label={r.dol.opposed.label} />)
        </dd>
        <dt>Alert usefulness</dt>
        <dd>UNAVAILABLE · {r.alertUsefulness.reason}</dd>
        {Object.keys(r.decisionRecords).length > 0 && (
          <>
            <dt>Decision records</dt>
            <dd>
              {Object.entries(r.decisionRecords)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            </dd>
          </>
        )}
      </dl>
      <EquityCurve points={r.equityCurve} />
      <h3>Process</h3>
      <p data-testid="analytics-process">
        {Object.entries(r.process.classifications)
          .map(([k, v]) => `${k} ${v}`)
          .join(" · ") || "no records"}
        {" · "}with violations {rr(r.process.withViolations.expectancyR)} ({r.process.withViolations.count} <Label label={r.process.withViolations.label} />) vs
        without {rr(r.process.withoutViolations.expectancyR)} ({r.process.withoutViolations.count}{" "}
        <Label label={r.process.withoutViolations.label} />)
      </p>
      {Object.keys(r.process.violationCounts).length > 0 && (
        <p className="muted">
          Rule violations:{" "}
          {Object.entries(r.process.violationCounts)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · ")}
        </p>
      )}
      {r.breakdowns.map((b) => (
        <div key={b.dimension} className="table-scroll">
          <table className="scan-table" data-testid={`analytics-${b.dimension}`}>
            <caption style={{ textAlign: "left" }}>
              {b.dimension} · best: {b.best ?? "none"} ({b.bestReason})
            </caption>
            <thead>
              <tr>
                <th>Group</th>
                <th>Records</th>
                <th>W/L/BE</th>
                <th>Win rate</th>
                <th>Expectancy</th>
                <th>Total R</th>
                <th>PF</th>
              </tr>
            </thead>
            <tbody>
              {b.groups.map((g) => (
                <GroupRow key={g.key} g={g} />
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}
