"use client";

import {
  EXIT_REASONS,
  RULE_VIOLATIONS,
  type Direction,
  type ExitReason,
  type JournalEntry,
  type JournalEntryKind,
  type RuleViolation,
} from "@fmcc/shared-types";
import { useCallback, useEffect, useState } from "react";

import {
  createJournalEntry,
  deleteJournalEntry,
  entryRequest,
  loadJournal,
  loadJournalEntry,
  outcomeRequest,
  recordJournalOutcome,
  type EntryForm,
  type JournalEntryState,
  type JournalListState,
  type OutcomeForm,
} from "@/lib/journal";

import { AnalyticsSection, type AnalyticsApi } from "./AnalyticsSection";
import { PaperSection, type PaperApi } from "./PaperSection";

const time = (iso: string | null | undefined) =>
  iso ? `${iso.replace("T", " ").slice(0, 16)}Z` : "—";
const r = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v}R`;

/** datetime-local value for "now" in the viewer's local time. */
function localNow(): string {
  const d = new Date();
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export type JournalApi = {
  list: typeof loadJournal;
  get: typeof loadJournalEntry;
  create: typeof createJournalEntry;
  outcome: typeof recordJournalOutcome;
  remove: typeof deleteJournalEntry;
  confirm: (message: string) => boolean;
};

const DEFAULT_API: JournalApi = {
  list: loadJournal,
  get: loadJournalEntry,
  create: createJournalEntry,
  outcome: recordJournalOutcome,
  remove: deleteJournalEntry,
  confirm: (m) => window.confirm(m),
};

export function JournalView({
  symbols,
  activeSymbol,
  api = DEFAULT_API,
  paperApi,
  analyticsApi,
  initialTab = "RECORDS",
}: {
  symbols: string[];
  activeSymbol: string;
  api?: JournalApi;
  paperApi?: PaperApi;
  initialTab?: "RECORDS" | "PAPER" | "ANALYTICS";
  analyticsApi?: AnalyticsApi;
}) {
  const [tab, setTab] = useState(initialTab);
  const [list, setList] = useState<JournalListState | null>(null);
  const [selected, setSelected] = useState<JournalEntryState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => setList(await api.list()), [api]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const open = async (id: string) => setSelected(await api.get(id));

  const store = list?.status === "READY" ? list.list.store : null;
  return (
    <section className="center-view" data-testid="journal-view">
      <h2>Journal</h2>
      <div className="tabs" role="tablist" data-testid="journal-tabs">
        {(["RECORDS", "PAPER", "ANALYTICS"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={tab === t ? "tab active" : "tab"}
            onClick={() => setTab(t)}
          >
            {t === "RECORDS" ? "Records" : t === "PAPER" ? "Paper sims" : "Analytics"}
          </button>
        ))}
      </div>
      {tab === "ANALYTICS" ? (
        <AnalyticsSection activeSymbol={activeSymbol} api={analyticsApi} />
      ) : tab === "PAPER" ? (
        <PaperSection
          symbols={symbols}
          activeSymbol={activeSymbol}
          api={paperApi}
        />
      ) : (
        <>
          <p className="muted">
            Your private records of decisions and your own manual trades. The
            engine snapshot is captured when you record an entry and can never
            be edited. Nothing is placed, sent or authorized.
          </p>
          {list === null ? (
            <p className="muted">Loading journal…</p>
          ) : list.status === "UNAVAILABLE" ? (
            <p
              className="not-available"
              role="alert"
              data-testid="journal-unavailable"
            >
              <strong>JOURNAL UNAVAILABLE</strong>
              <br />
              {list.reason}
            </p>
          ) : !store?.available ? (
            <p
              className="not-available"
              role="alert"
              data-testid="journal-unavailable"
            >
              <strong>JOURNAL NOT SAVING</strong>
              <br />
              {store?.reason} · set <code>JOURNAL_STORE=database</code> with{" "}
              <code>DATABASE_URL</code> and run{" "}
              <code>alembic upgrade head</code>.
            </p>
          ) : (
            <>
              <NewEntry
                symbols={symbols}
                activeSymbol={activeSymbol}
                onSubmit={async (form) => {
                  const built = entryRequest(form);
                  if ("error" in built) return setError(built.error);
                  const res = await api.create(built.body);
                  if (!res.ok) return setError(res.error);
                  setError(null);
                  if (res.entry)
                    setSelected({ status: "READY", entry: res.entry });
                  await refresh();
                }}
              />
              {error && (
                <p
                  className="sev-ERROR"
                  role="alert"
                  data-testid="journal-error"
                >
                  {error}
                </p>
              )}
              <EntryTable list={list} onOpen={(id) => void open(id)} />
            </>
          )}
          {selected?.status === "UNAVAILABLE" && (
            <p
              className="not-available"
              role="alert"
              data-testid="journal-entry-unavailable"
            >
              {selected.reason}
            </p>
          )}
          {selected?.status === "READY" && (
            <EntryDetail
              entry={selected.entry}
              onOutcome={async (form) => {
                const built = outcomeRequest(form);
                if ("error" in built) return built.error;
                const res = await api.outcome(selected.entry.id, built.body);
                if (!res.ok) return res.error;
                if (res.entry)
                  setSelected({ status: "READY", entry: res.entry });
                await refresh();
                return null;
              }}
              onDelete={async () => {
                if (
                  !api.confirm(
                    "Permanently delete this journal entry and its outcomes?",
                  )
                )
                  return;
                const res = await api.remove(selected.entry.id);
                if (!res.ok) return setError(res.error);
                setSelected(null);
                await refresh();
              }}
            />
          )}
        </>
      )}
    </section>
  );
}

function NewEntry({
  symbols,
  activeSymbol,
  onSubmit,
}: {
  symbols: string[];
  activeSymbol: string;
  onSubmit: (form: EntryForm) => Promise<void>;
}) {
  const [form, setForm] = useState<EntryForm>({
    symbol: activeSymbol,
    kind: "TRADE",
    direction: "BULLISH",
    entry: "",
    stop: "",
    targets: "",
    volume: "",
    riskPct: "",
    openedAt: localNow(),
    notes: "",
  });
  const set = <K extends keyof EntryForm>(k: K, v: EntryForm[K]) =>
    setForm((f) => ({ ...f, [k]: v }));
  const trade = form.kind === "TRADE";
  return (
    <form
      className="risk-form"
      data-testid="journal-form"
      onSubmit={(e) => {
        e.preventDefault();
        void onSubmit(form);
      }}
    >
      <label>
        Market
        <select
          className="tz-select"
          value={form.symbol}
          onChange={(e) => set("symbol", e.target.value)}
        >
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Record
        <select
          className="tz-select"
          value={form.kind}
          onChange={(e) => set("kind", e.target.value as JournalEntryKind)}
        >
          <option value="TRADE">TRADE (my manual trade)</option>
          <option value="NO_TRADE">NO_TRADE (decided not to trade)</option>
          <option value="MISSED_ENTRY">MISSED_ENTRY</option>
        </select>
      </label>
      {trade && (
        <>
          <label>
            Direction
            <select
              className="tz-select"
              value={form.direction}
              onChange={(e) => set("direction", e.target.value as Direction)}
            >
              <option value="BULLISH">BULLISH</option>
              <option value="BEARISH">BEARISH</option>
            </select>
          </label>
          <label>
            Opened (local time)
            <input
              type="datetime-local"
              value={form.openedAt}
              onChange={(e) => set("openedAt", e.target.value)}
            />
          </label>
          <label>
            Entry price
            <input
              inputMode="decimal"
              value={form.entry}
              onChange={(e) => set("entry", e.target.value)}
            />
          </label>
          <label>
            Stop (empty = none)
            <input
              inputMode="decimal"
              value={form.stop}
              onChange={(e) => set("stop", e.target.value)}
            />
          </label>
          <label>
            Targets (comma)
            <input
              value={form.targets}
              onChange={(e) => set("targets", e.target.value)}
            />
          </label>
          <label>
            Volume / Risk %
            <span style={{ display: "flex", gap: 4 }}>
              <input
                aria-label="Volume"
                inputMode="decimal"
                value={form.volume}
                onChange={(e) => set("volume", e.target.value)}
              />
              <input
                aria-label="Risk %"
                inputMode="decimal"
                value={form.riskPct}
                onChange={(e) => set("riskPct", e.target.value)}
              />
            </span>
          </label>
        </>
      )}
      <label style={{ gridColumn: "1 / -1" }}>
        Notes
        <input
          value={form.notes}
          maxLength={2000}
          onChange={(e) => set("notes", e.target.value)}
        />
      </label>
      <button type="submit" className="tf">
        Record entry (captures the engine snapshot now)
      </button>
    </form>
  );
}

function EntryTable({
  list,
  onOpen,
}: {
  list: Extract<JournalListState, { status: "READY" }>;
  onOpen: (id: string) => void;
}) {
  const rows = list.list.entries;
  if (rows.length === 0)
    return (
      <p className="muted" data-testid="journal-empty">
        No journal entries yet.
      </p>
    );
  return (
    <div className="table-scroll">
      <table className="scan-table" data-testid="journal-table">
        <thead>
          <tr>
            <th>Recorded</th>
            <th>Market</th>
            <th>Record</th>
            <th>Result</th>
            <th>R</th>
            <th>Process</th>
            <th>Violations</th>
            <th>Snapshot</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="muted">
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onOpen(e.id)}
                >
                  {time(e.createdAt)}
                </button>
              </td>
              <td>{e.symbol}</td>
              <td>
                {e.kind}
                {e.direction ? ` ${e.direction}` : ""}
              </td>
              <td>{e.result ?? e.status}</td>
              <td>{r(e.rMultiple)}</td>
              <td>{e.classification ?? "—"}</td>
              <td
                className={
                  e.detectedViolations.length ? "sev-WARNING" : undefined
                }
              >
                {e.detectedViolations.length}
              </td>
              <td>
                <span
                  className={`badge ${e.integrity === "VERIFIED" ? "q-CURRENT" : "q-INVALID"}`}
                >
                  {e.integrity}
                </span>
                {e.snapshotTiming === "POST_ENTRY" && (
                  <span className="badge q-STALE">POST_ENTRY</span>
                )}
                {e.isSynthetic && (
                  <span className="badge q-STALE">SYNTHETIC</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntryDetail({
  entry,
  onOutcome,
  onDelete,
}: {
  entry: JournalEntry;
  onOutcome: (form: OutcomeForm) => Promise<string | null>;
  onDelete: () => Promise<void>;
}) {
  const s = entry.summary;
  const t = entry.trade;
  const o = entry.outcome;
  return (
    <div data-testid="journal-detail">
      <h3>
        {entry.symbol} · {entry.kind}
        {t ? ` ${t.direction}` : ""} · {time(entry.createdAt)}
      </h3>
      {entry.snapshot.integrity === "TAMPERED" && (
        <p
          className="not-available"
          role="alert"
          data-testid="journal-tampered"
        >
          <strong>INTEGRITY CHECK FAILED</strong>: this record was changed after
          it was written. It cannot be trusted and no outcome can be added.
        </p>
      )}
      <dl data-testid="journal-snapshot">
        <dt>Snapshot</dt>
        <dd>
          {entry.snapshot.integrity} · {entry.snapshot.timing}
          {s.isSynthetic ? " · SYNTHETIC data" : ""} · {entry.strategyVersion}
        </dd>
        <dt>Engine at logging</dt>
        <dd>
          {s.verdict} ({s.engineAuthorization}) · data {s.dataQuality} ·{" "}
          {s.dayOfWeek} {s.activeSessions.join(", ") || "no session"} ·{" "}
          {s.timeQuality ?? "—"}
        </dd>
        <dt>Setup</dt>
        <dd>
          {s.setupType ?? "no setup"} {s.setupState ?? ""} · score{" "}
          {s.setupScore ?? "—"} {s.setupGrade ?? ""} · HTF {s.htfBias ?? "—"}
        </dd>
        <dt>Plan</dt>
        <dd>
          {s.planEntry !== null
            ? `entry ${s.planEntry} stop ${s.planStop} targets ${s.planTargets.join(", ")} (R:R ${s.planRr})`
            : "no confirmed plan"}
        </dd>
        <dt>Context</dt>
        <dd>
          DOL {s.primaryDol ?? "—"} · news {s.newsState ?? "—"} · macro{" "}
          {s.macroBias ?? "—"}
          {s.macroState ? ` ${s.macroState}` : ""} · risk {s.riskStatus ?? "—"}
        </dd>
        {t && (
          <>
            <dt>My trade</dt>
            <dd data-testid="journal-fill">
              entry {t.entry} · stop {t.stop ?? "none"} · targets{" "}
              {t.targets.join(", ") || "—"} · opened {time(t.openedAt)}
              {t.volume !== null ? ` · volume ${t.volume}` : ""}
              {t.riskPct !== null ? ` · risk ${t.riskPct}%` : ""}
            </dd>
          </>
        )}
        {entry.notes && (
          <>
            <dt>Notes</dt>
            <dd>{entry.notes}</dd>
          </>
        )}
      </dl>
      {entry.detectedViolations.length > 0 && (
        <p className="sev-WARNING" data-testid="journal-violations">
          Detected from the snapshot: {entry.detectedViolations.join(", ")}
        </p>
      )}
      {o && (
        <dl data-testid="journal-outcome">
          <dt>Outcome (revision {o.revision})</dt>
          <dd>
            {o.result} · {r(o.rMultiple)} of planned {r(o.plannedR)} ·{" "}
            {o.classification} · {o.exitReason} at {o.exitPrice} (
            {time(o.exitedAt)})
          </dd>
          <dt>MFE / MAE</dt>
          <dd>
            {r(o.mfeR)} / {r(o.maeR)} · efficiency entry{" "}
            {o.entryEfficiency ?? "—"} exit {o.exitEfficiency ?? "—"} ·{" "}
            {o.extremeSource}
            {o.extremesSynthetic ? " (synthetic candles)" : ""} ·{" "}
            {Math.round(o.durationMinutes)} min
          </dd>
          {o.violations.length > 0 && (
            <>
              <dt>Violations</dt>
              <dd>{o.violations.join(", ")}</dd>
            </>
          )}
        </dl>
      )}
      {entry.outcomeRevisions.length > 1 && (
        <p className="muted" data-testid="journal-revisions">
          Earlier revisions kept:{" "}
          {entry.outcomeRevisions
            .slice(0, -1)
            .map((x) => `#${x.revision} ${x.result} ${r(x.rMultiple)}`)
            .join(" · ")}
        </p>
      )}
      {entry.kind === "TRADE" && entry.snapshot.integrity === "VERIFIED" && (
        <OutcomeFormView onSubmit={onOutcome} revising={o !== null} />
      )}
      <button
        type="button"
        className="link-button"
        onClick={() => void onDelete()}
        data-testid="journal-delete"
      >
        Delete entry
      </button>
    </div>
  );
}

function OutcomeFormView({
  onSubmit,
  revising,
}: {
  onSubmit: (form: OutcomeForm) => Promise<string | null>;
  revising: boolean;
}) {
  const [form, setForm] = useState<OutcomeForm>({
    exitPrice: "",
    exitedAt: localNow(),
    exitReason: "TARGET",
    mfePrice: "",
    maePrice: "",
    reportedViolations: [],
    notes: "",
  });
  const [message, setMessage] = useState<string | null>(null);
  const set = <K extends keyof OutcomeForm>(k: K, v: OutcomeForm[K]) =>
    setForm((f) => ({ ...f, [k]: v }));
  const toggle = (v: RuleViolation) =>
    set(
      "reportedViolations",
      form.reportedViolations.includes(v)
        ? form.reportedViolations.filter((x) => x !== v)
        : [...form.reportedViolations, v],
    );
  return (
    <form
      className="risk-form"
      data-testid="journal-outcome-form"
      onSubmit={(e) => {
        e.preventDefault();
        void onSubmit(form).then(setMessage);
      }}
    >
      <label>
        Exit price (average)
        <input
          inputMode="decimal"
          value={form.exitPrice}
          onChange={(e) => set("exitPrice", e.target.value)}
        />
      </label>
      <label>
        Exited (local time)
        <input
          type="datetime-local"
          value={form.exitedAt}
          onChange={(e) => set("exitedAt", e.target.value)}
        />
      </label>
      <label>
        Exit reason
        <select
          className="tz-select"
          value={form.exitReason}
          onChange={(e) => set("exitReason", e.target.value as ExitReason)}
        >
          {EXIT_REASONS.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      </label>
      <label>
        MFE / MAE price (empty = from candles)
        <span style={{ display: "flex", gap: 4 }}>
          <input
            aria-label="MFE price"
            inputMode="decimal"
            value={form.mfePrice}
            onChange={(e) => set("mfePrice", e.target.value)}
          />
          <input
            aria-label="MAE price"
            inputMode="decimal"
            value={form.maePrice}
            onChange={(e) => set("maePrice", e.target.value)}
          />
        </span>
      </label>
      <fieldset
        style={{ gridColumn: "1 / -1", border: 0, padding: 0, margin: 0 }}
      >
        <legend className="muted">Rules I broke (self-reported)</legend>
        {RULE_VIOLATIONS.filter((v) =>
          [
            "MOVED_STOP",
            "OVERSIZED_POSITION",
            "EXITED_EARLY_WITHOUT_REASON",
            "REVENGE_TRADE",
            "OTHER",
          ].includes(v),
        ).map((v) => (
          <label
            key={v}
            style={{
              display: "inline-flex",
              flexDirection: "row",
              gap: 4,
              marginRight: 8,
            }}
          >
            <input
              type="checkbox"
              checked={form.reportedViolations.includes(v)}
              onChange={() => toggle(v)}
            />
            {v}
          </label>
        ))}
      </fieldset>
      <label style={{ gridColumn: "1 / -1" }}>
        Outcome notes
        <input
          value={form.notes}
          maxLength={2000}
          onChange={(e) => set("notes", e.target.value)}
        />
      </label>
      <button type="submit" className="tf">
        {revising
          ? "Record corrected outcome (new revision)"
          : "Record outcome"}
      </button>
      {message && (
        <p
          className="sev-ERROR"
          role="alert"
          data-testid="journal-outcome-error"
        >
          {message}
        </p>
      )}
    </form>
  );
}
