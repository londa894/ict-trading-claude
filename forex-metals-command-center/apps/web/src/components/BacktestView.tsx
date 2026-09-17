"use client";

import type { BacktestRun, EntryMode, GroupStats, VariantResult } from "@fmcc/shared-types";
import { useCallback, useEffect, useState } from "react";

import { pct, rr } from "@/lib/analytics";
import {
  backtestRequest,
  cancelBacktest,
  deleteBacktest,
  loadBacktest,
  loadBacktests,
  startBacktest,
  type BacktestForm,
  type BacktestListState,
  type BacktestRunState,
} from "@/lib/backtest";

export type BacktestApi = {
  list: typeof loadBacktests;
  get: typeof loadBacktest;
  start: typeof startBacktest;
  cancel: typeof cancelBacktest;
  remove: typeof deleteBacktest;
  confirm: (message: string) => boolean;
  pollMs: number;
};

const DEFAULT_API: BacktestApi = {
  list: loadBacktests,
  get: loadBacktest,
  start: startBacktest,
  cancel: cancelBacktest,
  remove: deleteBacktest,
  confirm: (m) => window.confirm(m),
  pollMs: 2000,
};

const MODES: EntryMode[] = ["CONSERVATIVE", "STANDARD", "AGGRESSIVE"];
const time = (iso: string | null | undefined) => (iso ? `${iso.replace("T", " ").slice(0, 16)}Z` : "—");

function Stats({ g }: { g: GroupStats }) {
  return (
    <>
      {g.count} closed <span className={g.label === "INSUFFICIENT" ? "badge q-INVALID" : "badge q-STALE"}>{g.label}</span> · W/L/BE {g.wins}/
      {g.losses}/{g.breakeven} · win rate {pct(g.winRate)} · expectancy {rr(g.expectancyR)} · total {rr(g.totalR)} · PF {g.profitFactor ?? "—"}
    </>
  );
}

function VariantPanel({ v }: { v: VariantResult }) {
  const f = v.funnel;
  return (
    <div data-testid={`backtest-variant-${v.variant.name}`}>
      <h3>
        Variant {v.variant.name} · {v.variant.entryMode} · costs ×{v.variant.costMultiplier}
      </h3>
      <dl>
        <dt>Funnel</dt>
        <dd data-testid={`backtest-funnel-${v.variant.name}`}>
          {f.steps} steps ({f.ineligibleSteps} ineligible
          {Object.keys(f.ineligibleReasons).length ? `: ${Object.entries(f.ineligibleReasons).map(([k, n]) => `${k} ${n}`).join(", ")}` : ""}) · {f.setupsDiscovered} setups · {f.plansConfirmed} plans confirmed → {f.fills} filled ·{" "}
          {f.closed} closed · {f.expired} expired · {f.plansSkippedOverlap} skipped (overlap) · {f.openAtEnd} open at end
        </dd>
        <dt>States reached</dt>
        <dd className="muted">
          {Object.entries(f.statesReached)
            .map(([k, n]) => `${k} ${n}`)
            .join(" · ") || "none"}
        </dd>
        <dt>Statistics</dt>
        <dd>
          <Stats g={v.stats} />
        </dd>
        <dt>Drawdown</dt>
        <dd>
          max {rr(-v.drawdown.maxDrawdownR)} · ambiguous bars {v.ambiguousTrades}
        </dd>
        {v.inSample && v.outOfSample && (
          <>
            <dt>In / out of sample</dt>
            <dd>
              <Stats g={v.inSample} /> | <Stats g={v.outOfSample} />
            </dd>
          </>
        )}
        {v.monteCarlo && (
          <>
            <dt>Monte Carlo</dt>
            <dd data-testid={`backtest-mc-${v.variant.name}`}>
              {v.monteCarlo.resamples} resamples of {v.monteCarlo.trades} trades ({v.monteCarlo.label}) · total R p5/p50/p95{" "}
              {v.monteCarlo.totalRP05}/{v.monteCarlo.totalRP50}/{v.monteCarlo.totalRP95} · max drawdown p50/p95 {v.monteCarlo.maxDrawdownRP50}/
              {v.monteCarlo.maxDrawdownRP95}
            </dd>
          </>
        )}
      </dl>
      {v.segments.length > 1 && (
        <div className="table-scroll">
          <table className="scan-table">
            <caption style={{ textAlign: "left" }}>Segment stability (no parameters are fitted)</caption>
            <tbody>
              {v.segments.map((s) => (
                <tr key={s.name}>
                  <td>
                    {s.name} {time(s.start)} → {time(s.end)}
                  </td>
                  <td>
                    <Stats g={s.stats} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v.trades.length > 0 && (
        <div className="table-scroll">
          <table className="scan-table" data-testid={`backtest-trades-${v.variant.name}`}>
            <thead>
              <tr>
                <th>Confirmed</th>
                <th>Plan</th>
                <th>Status</th>
                <th>Fill → exit</th>
                <th>Result</th>
                <th>Net R</th>
              </tr>
            </thead>
            <tbody>
              {v.trades.map((t) => (
                <tr key={`${t.setupId}-${t.confirmedAt}`}>
                  <td className="muted">{time(t.confirmedAt)}</td>
                  <td>
                    {t.model} {t.direction} @ {t.limitPrice} (stop {t.stop}, TP1 {t.target})
                  </td>
                  <td>
                    {t.status}
                    {t.ambiguous ? " · AMBIGUOUS" : ""}
                  </td>
                  <td>
                    {t.fillPrice ?? "—"} → {t.exitPrice ?? "—"}
                  </td>
                  <td>{t.result ?? "—"}</td>
                  <td>{rr(t.netRMultiple)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunDetail({ run, onCancel, onDelete }: { run: BacktestRun; onCancel: () => void; onDelete: () => void }) {
  const active = run.status === "QUEUED" || run.status === "RUNNING";
  return (
    <div data-testid="backtest-detail">
      <h3>
        {run.request.symbol} · {time(run.request.start)} → {time(run.request.end)} · {run.status} · {run.authority}
      </h3>
      {active && (
        <p data-testid="backtest-progress">
          {run.progress.pct}% {run.progress.variant ? `(variant ${run.progress.variant}: ${run.progress.stepsDone}/${run.progress.stepsTotal} steps)` : ""}
        </p>
      )}
      {run.error && (
        <p className="not-available" role="alert" data-testid="backtest-error">
          {run.error}
        </p>
      )}
      {run.integrity === "UNREADABLE" && <p className="muted">The stored result uses an older format and cannot be shown.</p>}
      {run.integrity === "TAMPERED" && (
        <p className="not-available" role="alert">
          <strong>INTEGRITY CHECK FAILED</strong>: the stored result was changed and is not shown.
        </p>
      )}
      {run.result && (
        <>
          <p className="sev-WARNING" data-testid="backtest-disclosures">
            {run.result.data.isSynthetic ? "SYNTHETIC data. " : ""}
            {run.result.disclosures.join(" ")}
          </p>
          <p className="muted">
            {run.result.data.provider} · {run.result.data.stepBars} setup bars · {run.result.data.executionBars} execution bars · config{" "}
            {run.result.configHash.slice(0, 12)} · {run.strategyVersion}
          </p>
          {run.result.variants.map((v) => (
            <VariantPanel key={v.variant.name} v={v} />
          ))}
        </>
      )}
      <div className="chart-toolbar">
        {active && (
          <button type="button" className="tf" onClick={onCancel}>
            Cancel run
          </button>
        )}
        {!active && (
          <button type="button" className="link-button" onClick={onDelete} data-testid="backtest-delete">
            Delete run
          </button>
        )}
      </div>
    </div>
  );
}

/** Backtest view (Phase 18): research replays of the live setup engine over closed history. */
export function BacktestView({ symbols, activeSymbol, api = DEFAULT_API }: { symbols: string[]; activeSymbol: string; api?: BacktestApi }) {
  const [list, setList] = useState<BacktestListState | null>(null);
  const [selected, setSelected] = useState<BacktestRunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<BacktestForm>({
    symbol: activeSymbol,
    start: "",
    end: "",
    modeA: "STANDARD",
    compare: false,
    modeB: "CONSERVATIVE",
    costMultiplierB: "1",
    segments: "1",
    outOfSampleFrom: "",
  });
  const set = <K extends keyof BacktestForm>(k: K, v: BacktestForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  const refresh = useCallback(async () => setList(await api.list()), [api]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectedRun = selected?.status === "READY" ? selected.run : null;
  const active = selectedRun && (selectedRun.status === "QUEUED" || selectedRun.status === "RUNNING");
  useEffect(() => {
    if (!active || !selectedRun) return;
    const timer = setTimeout(() => {
      void api.get(selectedRun.id).then((s) => {
        setSelected(s);
        if (s.status === "READY" && s.run.status !== "QUEUED" && s.run.status !== "RUNNING") void refresh();
      });
    }, api.pollMs);
    return () => clearTimeout(timer);
  }, [active, selectedRun, api, refresh]);

  const store = list?.status === "READY" ? list.list.store : null;
  return (
    <section className="center-view" data-testid="backtest-view">
      <h2>Backtest</h2>
      <p className="muted">
        Research replays: at every closed setup candle the live setup engine is evaluated as of that close, confirmed plans are simulated on closed
        bars with assumed costs. Risk, news and authority gates are not applied. Results are not a forecast.
      </p>
      {list === null ? (
        <p className="muted">Loading backtests…</p>
      ) : list.status === "UNAVAILABLE" || !store?.available ? (
        <p className="not-available" role="alert" data-testid="backtest-unavailable">
          <strong>BACKTESTING UNAVAILABLE</strong>
          <br />
          {list.status === "UNAVAILABLE" ? list.reason : store?.reason} · set <code>BACKTEST_STORE=database</code> with <code>DATABASE_URL</code> and run{" "}
          <code>alembic upgrade head</code>.
        </p>
      ) : (
        <>
          <form
            className="risk-form"
            data-testid="backtest-form"
            onSubmit={(e) => {
              e.preventDefault();
              const built = backtestRequest(form);
              if ("error" in built) return setError(built.error);
              void api.start(built.body).then(async (res) => {
                if (!res.ok) return setError(res.error);
                setError(null);
                if (res.run) setSelected({ status: "READY", run: res.run });
                await refresh();
              });
            }}
          >
            <label>
              Market
              <select className="tz-select" value={form.symbol} onChange={(e) => set("symbol", e.target.value)}>
                {symbols.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Variant A entry mode
              <select className="tz-select" value={form.modeA} onChange={(e) => set("modeA", e.target.value as EntryMode)}>
                {MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label>
              From (local time)
              <input type="datetime-local" value={form.start} onChange={(e) => set("start", e.target.value)} />
            </label>
            <label>
              To (local time)
              <input type="datetime-local" value={form.end} onChange={(e) => set("end", e.target.value)} />
            </label>
            <label>
              Segments (1–6)
              <input inputMode="numeric" value={form.segments} onChange={(e) => set("segments", e.target.value)} />
            </label>
            <label>
              Out-of-sample from (optional)
              <input type="datetime-local" value={form.outOfSampleFrom} onChange={(e) => set("outOfSampleFrom", e.target.value)} />
            </label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={form.compare} onChange={(e) => set("compare", e.target.checked)} /> Compare with variant B (A/B)
            </label>
            {form.compare && (
              <>
                <label>
                  Variant B entry mode
                  <select className="tz-select" value={form.modeB} onChange={(e) => set("modeB", e.target.value as EntryMode)}>
                    {MODES.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Variant B cost multiplier
                  <input inputMode="decimal" value={form.costMultiplierB} onChange={(e) => set("costMultiplierB", e.target.value)} />
                </label>
              </>
            )}
            <button type="submit" className="tf" disabled={Boolean(store?.runningId)}>
              {store?.runningId ? "A backtest is running…" : "Start backtest"}
            </button>
          </form>
          {error && (
            <p className="sev-ERROR" role="alert" data-testid="backtest-form-error">
              {error}
            </p>
          )}
          {list.list.runs.length === 0 ? (
            <p className="muted">No backtests yet.</p>
          ) : (
            <div className="table-scroll">
              <table className="scan-table" data-testid="backtest-runs">
                <thead>
                  <tr>
                    <th>Created</th>
                    <th>Market</th>
                    <th>Range</th>
                    <th>Status</th>
                    <th>Variants</th>
                    <th>Closed / total R</th>
                  </tr>
                </thead>
                <tbody>
                  {list.list.runs.map((r) => (
                    <tr key={r.id}>
                      <td className="muted">
                        <button type="button" className="link-button" onClick={() => void api.get(r.id).then(setSelected)}>
                          {time(r.createdAt)}
                        </button>
                      </td>
                      <td>{r.symbol}</td>
                      <td>
                        {time(r.start)} → {time(r.end)}
                      </td>
                      <td>
                        {r.status}
                        {r.status === "RUNNING" ? ` ${r.pct}%` : ""}
                      </td>
                      <td>{r.variants.join(", ")}</td>
                      <td>
                        {r.variants.map((name) => `${name}: ${r.closedTrades[name] ?? "—"} / ${rr(r.netTotalR[name] ?? null)}`).join(" · ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {selected?.status === "UNAVAILABLE" && (
        <p className="not-available" role="alert" data-testid="backtest-run-unavailable">
          {selected.reason}
        </p>
      )}
      {selectedRun && (
        <RunDetail
          run={selectedRun}
          onCancel={() => void api.cancel(selectedRun.id).then((res) => (res.ok ? res.run && setSelected({ status: "READY", run: res.run }) : setError(res.error)))}
          onDelete={() => {
            if (!api.confirm("Permanently delete this backtest run?")) return;
            void api.remove(selectedRun.id).then(async (res) => {
              if (!res.ok) return setError(res.error);
              setSelected(null);
              await refresh();
            });
          }}
        />
      )}
    </section>
  );
}
