"use client";

import type { Direction, PaperEntryType, PaperSim, PaperSource } from "@fmcc/shared-types";
import { useCallback, useEffect, useState } from "react";

import {
  cancelPaperSim,
  closePaperSim,
  createPaperSim,
  deletePaperSim,
  loadPaperSim,
  loadPaperSims,
  paperRequest,
  type PaperForm,
  type PaperListState,
  type PaperSimState,
} from "@/lib/paper";

const time = (iso: string | null | undefined) => (iso ? `${iso.replace("T", " ").slice(0, 16)}Z` : "—");
const r = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v}R`);

export type PaperApi = {
  list: typeof loadPaperSims;
  get: typeof loadPaperSim;
  create: typeof createPaperSim;
  close: typeof closePaperSim;
  cancel: typeof cancelPaperSim;
  remove: typeof deletePaperSim;
  confirm: (message: string) => boolean;
};

const DEFAULT_API: PaperApi = {
  list: loadPaperSims,
  get: loadPaperSim,
  create: createPaperSim,
  close: closePaperSim,
  cancel: cancelPaperSim,
  remove: deletePaperSim,
  confirm: (m) => window.confirm(m),
};

/** Paper simulations (Phase 16): broker-free, on the independent market data, never an authorization. */
export function PaperSection({ symbols, activeSymbol, api = DEFAULT_API }: { symbols: string[]; activeSymbol: string; api?: PaperApi }) {
  const [list, setList] = useState<PaperListState | null>(null);
  const [selected, setSelected] = useState<PaperSimState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<PaperForm>({
    symbol: activeSymbol,
    source: "MANUAL",
    direction: "BULLISH",
    entryType: "MARKET",
    limitPrice: "",
    stop: "",
    target: "",
    notes: "",
  });
  const set = <K extends keyof PaperForm>(k: K, v: PaperForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  const refresh = useCallback(async () => setList(await api.list()), [api]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (run: () => ReturnType<PaperApi["close"]>) => {
    const res = await run();
    if (!res.ok) return setError(res.error);
    setError(null);
    setSelected(res.sim ? { status: "READY", sim: res.sim } : null);
    await refresh();
  };

  const store = list?.status === "READY" ? list.list.store : null;
  return (
    <div data-testid="paper-section">
      <p className="muted">
        Paper sims replay the independent market data bar by bar with assumed spread, slippage and commission. Simulation
        only: nothing is sent to any broker and nothing is authorized.
      </p>
      {list === null ? (
        <p className="muted">Loading paper sims…</p>
      ) : list.status === "UNAVAILABLE" || !store?.available ? (
        <p className="not-available" role="alert" data-testid="paper-unavailable">
          <strong>PAPER TRADING UNAVAILABLE</strong>
          <br />
          {list.status === "UNAVAILABLE" ? list.reason : store?.reason} · set <code>PAPER_STORE=database</code> with{" "}
          <code>DATABASE_URL</code> and run <code>alembic upgrade head</code>.
        </p>
      ) : (
        <>
          <form
            className="risk-form"
            data-testid="paper-form"
            onSubmit={(e) => {
              e.preventDefault();
              const built = paperRequest(form);
              if ("error" in built) return setError(built.error);
              void act(() => api.create(built.body));
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
              Source
              <select className="tz-select" value={form.source} onChange={(e) => set("source", e.target.value as PaperSource)}>
                <option value="MANUAL">MANUAL (my levels)</option>
                <option value="ENGINE_PLAN">ENGINE_PLAN (forward-test the confirmed plan)</option>
              </select>
            </label>
            {form.source === "MANUAL" && (
              <>
                <label>
                  Direction
                  <select className="tz-select" value={form.direction} onChange={(e) => set("direction", e.target.value as Direction)}>
                    <option value="BULLISH">BULLISH</option>
                    <option value="BEARISH">BEARISH</option>
                  </select>
                </label>
                <label>
                  Entry
                  <select className="tz-select" value={form.entryType} onChange={(e) => set("entryType", e.target.value as PaperEntryType)}>
                    <option value="MARKET">MARKET (next bar open)</option>
                    <option value="LIMIT">LIMIT</option>
                  </select>
                </label>
                {form.entryType === "LIMIT" && (
                  <label>
                    Limit price
                    <input inputMode="decimal" value={form.limitPrice} onChange={(e) => set("limitPrice", e.target.value)} />
                  </label>
                )}
                <label>
                  Stop
                  <input inputMode="decimal" value={form.stop} onChange={(e) => set("stop", e.target.value)} />
                </label>
                <label>
                  Target
                  <input inputMode="decimal" value={form.target} onChange={(e) => set("target", e.target.value)} />
                </label>
              </>
            )}
            <label style={{ gridColumn: "1 / -1" }}>
              Notes
              <input value={form.notes} maxLength={2000} onChange={(e) => set("notes", e.target.value)} />
            </label>
            <button type="submit" className="tf">
              Start paper simulation
            </button>
          </form>
          {error && (
            <p className="sev-ERROR" role="alert" data-testid="paper-error">
              {error}
            </p>
          )}
          {list.list.sims.length === 0 ? (
            <p className="muted" data-testid="paper-empty">
              No paper sims yet.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="scan-table" data-testid="paper-table">
                <thead>
                  <tr>
                    <th>Created</th>
                    <th>Market</th>
                    <th>Sim</th>
                    <th>Status</th>
                    <th>Fill → exit</th>
                    <th>Net R</th>
                    <th>Process</th>
                    <th>Record</th>
                  </tr>
                </thead>
                <tbody>
                  {list.list.sims.map((s) => (
                    <tr key={s.id}>
                      <td className="muted">
                        <button type="button" className="link-button" onClick={() => void api.get(s.id).then(setSelected)}>
                          {time(s.createdAt)}
                        </button>
                      </td>
                      <td>{s.symbol}</td>
                      <td>
                        {s.source === "ENGINE_PLAN" ? "PLAN " : ""}
                        {s.entryType} {s.direction}
                      </td>
                      <td>{s.result ?? s.status}</td>
                      <td>
                        {s.fillPrice ?? "—"} → {s.exitPrice ?? "—"}
                      </td>
                      <td>{r(s.netRMultiple)}</td>
                      <td>{s.classification ?? "—"}</td>
                      <td>
                        <span className={`badge ${s.integrity === "VERIFIED" ? "q-CURRENT" : "q-INVALID"}`}>{s.integrity}</span>
                        {s.isSynthetic && <span className="badge q-STALE">SYNTHETIC</span>}
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
        <p className="not-available" role="alert" data-testid="paper-sim-unavailable">
          {selected.reason}
        </p>
      )}
      {selected?.status === "READY" && (
        <SimDetail
          sim={selected.sim}
          onClose={() => void act(() => api.close(selected.sim.id))}
          onCancel={() => void act(() => api.cancel(selected.sim.id))}
          onDelete={() => {
            if (api.confirm("Permanently delete this paper sim?")) void act(() => api.remove(selected.sim.id));
          }}
        />
      )}
    </div>
  );
}

function SimDetail({ sim, onClose, onCancel, onDelete }: { sim: PaperSim; onClose: () => void; onCancel: () => void; onDelete: () => void }) {
  const res = sim.result;
  return (
    <div data-testid="paper-detail">
      <h3>
        {sim.symbol} · {sim.source} {sim.entryType} {sim.direction} · {sim.status} · {sim.authority}
      </h3>
      {sim.integrity === "TAMPERED" && (
        <p className="not-available" role="alert" data-testid="paper-tampered">
          <strong>INTEGRITY CHECK FAILED</strong>: this simulation record was changed after it was written and is no longer
          simulated.
        </p>
      )}
      <dl>
        <dt>Levels</dt>
        <dd data-testid="paper-levels">
          reference {sim.referencePrice}
          {sim.limitPrice !== null ? ` · limit ${sim.limitPrice}` : ""} · stop {sim.stop} · target {sim.target}
        </dd>
        <dt>Assumed costs</dt>
        <dd>
          spread {sim.costs.spread} · slippage {sim.costs.slippage} · commission {sim.costs.commission}/side (assumptions, not broker specs)
        </dd>
        <dt>Simulation</dt>
        <dd data-testid="paper-progress">
          fill {sim.fillPrice ?? "—"} ({time(sim.filledAt)}) · exit {sim.exitPrice ?? "—"} ({time(sim.exitedAt)}) · simulated through{" "}
          {time(sim.processedThrough)}
          {sim.dataQuality ? ` · data ${sim.dataQuality}` : ""}
          {sim.dataNote ? ` · ${sim.dataNote}` : ""}
          {sim.isSynthetic ? " · SYNTHETIC data" : ""}
        </dd>
        <dt>Engine at start</dt>
        <dd>
          {sim.summary.verdict} ({sim.summary.engineAuthorization}) · setup {sim.summary.setupType ?? "none"} · news {sim.summary.newsState ?? "—"}
        </dd>
      </dl>
      {sim.detectedViolations.length > 0 && <p className="sev-WARNING">Detected at start: {sim.detectedViolations.join(", ")}</p>}
      {res && (
        <p data-testid="paper-result">
          {res.result} · {r(res.rMultiple)} ({r(res.netRMultiple)} net) of planned {r(res.plannedR)} · {res.classification} · MFE {r(res.mfeR)} / MAE{" "}
          {r(res.maeR)} · {Math.round(res.durationMinutes)} min
        </p>
      )}
      <ol className="event-list" data-testid="paper-events">
        {sim.events.map((e) => (
          <li key={e.seq} className={e.integrity === "TAMPERED" ? "sev-ERROR" : undefined}>
            {time(e.at)} {e.type}
            {e.price !== null ? ` @ ${e.price}` : ""} · {e.detail}
            {e.ambiguous ? " · AMBIGUOUS BAR (conservative)" : ""}
          </li>
        ))}
      </ol>
      <div className="chart-toolbar">
        {sim.status === "OPEN" && (
          <button type="button" className="tf" onClick={onClose}>
            Close simulation at last bar
          </button>
        )}
        {sim.status === "PENDING" && (
          <button type="button" className="tf" onClick={onCancel}>
            Cancel pending simulation
          </button>
        )}
        <button type="button" className="link-button" onClick={onDelete} data-testid="paper-delete">
          Delete sim
        </button>
      </div>
    </div>
  );
}
