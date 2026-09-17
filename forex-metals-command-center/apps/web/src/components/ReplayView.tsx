"use client";

import type { ChartTimeframe, ReplayMode, ReplayReveal, ReplayState } from "@fmcc/shared-types";
import { useState } from "react";

import {
  answerReplay,
  createReplay,
  endReplay,
  loadReplay,
  replayRequest,
  stepReplay,
  type ReplayForm,
  type ReplayLoad,
} from "@/lib/replay";

import { CandleChart } from "./CandleChart";

export type ReplayApi = {
  create: typeof createReplay;
  step: typeof stepReplay;
  answer: typeof answerReplay;
  load: typeof loadReplay;
  end: typeof endReplay;
};

const DEFAULT_API: ReplayApi = { create: createReplay, step: stepReplay, answer: answerReplay, load: loadReplay, end: endReplay };

const MODES: { mode: ReplayMode; text: string }[] = [
  { mode: "MANUAL", text: "Manual: step freely, engine view shown" },
  { mode: "GUIDED", text: "Guided: engine view plus tutor notes" },
  { mode: "BLIND", text: "Blind: instrument, dates and levels hidden; forward only" },
  { mode: "QUIZ", text: "Quiz: predict, then the bars are revealed" },
];
const time = (iso: string) => `${iso.replace("T", " ").slice(0, 16)}Z`;

function localHoursAgo(hours: number): string {
  const d = new Date(Date.now() - hours * 3600_000);
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

/** Replay view (Phase 19): Manual / Guided / Blind / Quiz sessions that never show bars after the cursor. */
export function ReplayView({ symbols, activeSymbol, api = DEFAULT_API }: { symbols: string[]; activeSymbol: string; api?: ReplayApi }) {
  const [form, setForm] = useState<ReplayForm>({ symbol: activeSymbol, mode: "MANUAL", timeframe: "M15", start: localHoursAgo(24) });
  const [session, setSession] = useState<ReplayLoad | null>(null);
  const [reveal, setReveal] = useState<ReplayReveal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof ReplayForm>(k: K, v: ReplayForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  const run = async (action: () => Promise<ReplayLoad>) => {
    setBusy(true);
    const res = await action();
    setBusy(false);
    if (res.status === "UNAVAILABLE") return setError(res.reason);
    setError(null);
    setSession(res);
  };

  const state = session?.status === "READY" ? session.state : null;
  return (
    <section className="center-view" data-testid="replay-view">
      <h2>Replay</h2>
      <p className="muted">
        Practise on history. Every view is computed as of the replay cursor: bars after it are never sent. Education only; nothing here is a signal.
      </p>
      <form
        className="risk-form"
        data-testid="replay-form"
        onSubmit={(e) => {
          e.preventDefault();
          const built = replayRequest(form);
          if ("error" in built) return setError(built.error);
          setReveal(null);
          void run(() => api.create(built.body));
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
          Mode
          <select className="tz-select" value={form.mode} onChange={(e) => set("mode", e.target.value as ReplayMode)}>
            {MODES.map((m) => (
              <option key={m.mode} value={m.mode}>
                {m.text}
              </option>
            ))}
          </select>
        </label>
        <label>
          Timeframe
          <select className="tz-select" value={form.timeframe} onChange={(e) => set("timeframe", e.target.value as ChartTimeframe)}>
            {(["M5", "M15", "H1"] as const).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Start (local time)
          <input type="datetime-local" value={form.start} onChange={(e) => set("start", e.target.value)} />
        </label>
        <button type="submit" className="tf" disabled={busy}>
          Start replay
        </button>
      </form>
      {error && (
        <p className="sev-ERROR" role="alert" data-testid="replay-error">
          {error}
        </p>
      )}
      {state && <Session state={state} busy={busy} api={api} run={run} onReveal={setReveal} onError={setError} />}
      {reveal && (
        <p className="sev-WARNING" data-testid="replay-reveal">
          Revealed: {reveal.symbol} · started {time(reveal.start)} · ended at {time(reveal.cursor)} · prices were scaled by {reveal.priceScale} and dates shifted by{" "}
          {reveal.weekShift} weeks
        </p>
      )}
    </section>
  );
}

function Session({
  state,
  busy,
  api,
  run,
  onReveal,
  onError,
}: {
  state: ReplayState;
  busy: boolean;
  api: ReplayApi;
  run: (action: () => Promise<ReplayLoad>) => Promise<void>;
  onReveal: (r: ReplayReveal) => void;
  onError: (e: string) => void;
}) {
  const quiz = state.quiz;
  const a = state.analysis;
  return (
    <div data-testid="replay-session">
      <h3>
        {state.label} · {state.mode} · {state.timeframe} · cursor {time(state.cursor)}
        {state.masked ? " (dates shifted)" : ""} · {state.authority}
      </h3>
      <div className="replay-chart" style={{ height: 360 }}>
        <CandleChart candles={state.candles} timeframe={state.timeframe} />
      </div>
      <div className="chart-toolbar" data-testid="replay-controls">
        {state.mode !== "QUIZ" && !state.ended && (
          <>
            {state.canStepBack && (
              <button type="button" className="tf" disabled={busy} onClick={() => void run(() => api.step(state.id, -1))}>
                ◀ 1 bar
              </button>
            )}
            {[1, 4, 12].map((n) => (
              <button key={n} type="button" className="tf" disabled={busy || state.atEnd} onClick={() => void run(() => api.step(state.id, n))}>
                {n} bar{n > 1 ? "s" : ""} ▶
              </button>
            ))}
          </>
        )}
        {!state.ended && (
          <button
            type="button"
            className="link-button"
            data-testid="replay-end"
            onClick={() =>
              void api.end(state.id).then(async (res) => {
                if (!res.ok) return onError(res.error);
                onReveal(res.reveal);
                await run(() => api.load(state.id));
              })
            }
          >
            End session{state.mode === "BLIND" ? " and reveal" : ""}
          </button>
        )}
        {state.atEnd && <span className="muted">End of available history.</span>}
      </div>
      {quiz && (
        <div className="panel" data-testid="replay-quiz">
          <p>
            <strong>{quiz.id}</strong> · {quiz.prompt}
          </p>
          <div className="chart-toolbar">
            {quiz.options.map((option) => (
              <button key={option} type="button" className="tf" disabled={busy} onClick={() => void run(() => api.answer(state.id, quiz.id, option))}>
                {option}
              </button>
            ))}
          </div>
        </div>
      )}
      {state.lastResult && (
        <p className={state.lastResult.grade === "CORRECT" ? "q-CURRENT" : state.lastResult.grade === "INCORRECT" ? "sev-ERROR" : "muted"} data-testid="replay-result">
          {state.lastResult.questionId}: {state.lastResult.grade} · you answered {state.lastResult.answer}
          {state.lastResult.correctAnswer ? ` · answer ${state.lastResult.correctAnswer}` : ""} · {state.lastResult.detail}
        </p>
      )}
      {state.score && (
        <p data-testid="replay-score">
          Score: {state.score.correct}/{state.score.correct + state.score.incorrect} correct
          {state.score.accuracy !== null ? ` (${(state.score.accuracy * 100).toFixed(0)}%)` : ""} · {state.score.void} void ·{" "}
          <span className={state.score.label === "INSUFFICIENT" ? "badge q-INVALID" : "badge q-STALE"}>{state.score.label}</span>
        </p>
      )}
      {a && (
        <dl data-testid="replay-analysis">
          <dt>Engine at cursor</dt>
          <dd>
            {a.eligibleForDecision ? "eligible" : `not eligible (${a.ineligibility.join(", ")})`} · setup {a.setupState ?? "none"}
            {a.currentSetup ? ` · ${a.currentSetup.direction} ${a.currentSetup.setupType} (${a.currentSetup.state})` : ""}
            {a.currentSetup?.planEntry != null ? ` · plan ${a.currentSetup.planEntry} / stop ${a.currentSetup.planStop} / TP1 ${a.currentSetup.planTp1}` : ""}
          </dd>
          <dt>Time</dt>
          <dd>
            New York {a.newYorkTime} · {a.activeSessions.join(", ") || "no session"} · {a.timeQuality}
          </dd>
        </dl>
      )}
      {state.guidance && (
        <ul className="event-list" data-testid="replay-guidance">
          {state.guidance.narrative.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
      {state.mode === "BLIND" && state.masked && (
        <p className="muted" data-testid="replay-blind-note">
          Instrument, dates and price levels are hidden; the engine view stays hidden until you end the session.
        </p>
      )}
    </div>
  );
}
