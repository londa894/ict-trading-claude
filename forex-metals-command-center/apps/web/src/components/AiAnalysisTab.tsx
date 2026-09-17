"use client";

import type { AssistantCapabilities, EducationLevel } from "@fmcc/shared-types";
import { useEffect, useState, type FormEvent } from "react";

import { API_BASE_URL, createReadyWatch } from "@/lib/api";
import { askAssistant, reconcileCapabilities, type AnswerLoadState } from "@/lib/assistant";

type Turn = { id: number; question: string; result: AnswerLoadState | null };
const LEVELS: EducationLevel[] = ["BEGINNER", "INTERMEDIATE", "ADVANCED", "PROFESSIONAL"];
const SUGGESTED = ["Analyze Gold", "Why are we waiting?", "Where is liquidity?", "What invalidates this?", "Give trade plan", "What is an FVG?"];

/** AI_ANALYSIS tab: explanations of this market's engine state. The conversation is scoped to one symbol. */
export function AiAnalysisTab({ symbol, fetcher }: { symbol: string; fetcher?: typeof fetch }) {
  const [caps, setCaps] = useState<AssistantCapabilities | null>(null);
  const [level, setLevel] = useState<EducationLevel>("INTERMEDIATE");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [watchMessage, setWatchMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void (fetcher ?? fetch)(`${API_BASE_URL}/api/v1/assistant/capabilities`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body: unknown) => active && setCaps(reconcileCapabilities(body)))
      .catch(() => active && setCaps(null));
    return () => {
      active = false;
    };
  }, [fetcher]);

  const ask = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const id = Date.now();
    setTurns((t) => [{ id, question: q, result: null }, ...t]);
    setQuestion("");
    setBusy(true);
    const result = await askAssistant(API_BASE_URL, symbol, q, level, fetcher);
    setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, result } : turn)));
    setBusy(false);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void ask(question);
  };

  const confirmWatch = async (direction: "BULLISH" | "BEARISH" | null) => {
    const error = await createReadyWatch(symbol, direction ?? "ANY", fetcher);
    setWatchMessage(error ?? `ALERT_ME_WHEN_READY watch created for ${symbol}. See the ALERTS view.`);
  };

  return (
    <div data-testid="ai-tab">
      <p className="banner-inline" data-testid="ai-disclaimer">
        Explains the deterministic engine state for {symbol}. It never creates prices, verdicts or trade instructions.
      </p>
      <p className="muted" data-testid="ai-provider">
        {caps
          ? caps.externalAiConfigured
            ? `External model ${caps.model} (answers must pass the grounding guard${caps.sharesAccountData ? "" : "; account amounts withheld"})`
            : "Deterministic explainer (no external AI configured)"
          : "Assistant capabilities unavailable"}
      </p>
      <form onSubmit={onSubmit} className="ai-form">
        <select className="tz-select" aria-label="Education level" value={level} onChange={(e) => setLevel(e.target.value as EducationLevel)}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        <input
          aria-label="Question"
          value={question}
          maxLength={500}
          placeholder={`Ask about ${symbol}…`}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" className="tf" disabled={busy || !question.trim()}>
          {busy ? "…" : "Ask"}
        </button>
      </form>
      <div className="ai-suggested">
        {SUGGESTED.map((s) => (
          <button key={s} type="button" className="tf" onClick={() => void ask(s)} disabled={busy}>
            {s}
          </button>
        ))}
      </div>
      {watchMessage && <p className="muted" data-testid="ai-watch-message">{watchMessage}</p>}
      <ol className="ai-turns" data-testid="ai-turns">
        {turns.map((turn) => (
          <li key={turn.id}>
            <p>
              <strong>Q:</strong> {turn.question}
            </p>
            {!turn.result ? (
              <p className="muted">Reading the engine state…</p>
            ) : turn.result.status === "UNAVAILABLE" ? (
              <p className="not-available" role="alert" data-testid="ai-unavailable">
                {turn.result.reason}
              </p>
            ) : (
              <div data-testid="ai-answer">
                <p>{turn.result.answer.answer}</p>
                <p className="muted" data-testid="ai-meta">
                  {turn.result.answer.intent} · {turn.result.answer.provider}
                  {turn.result.answer.model ? ` (${turn.result.answer.model})` : ""} · guard {turn.result.answer.guard.status}
                  {turn.result.answer.decision
                    ? ` · decision ${turn.result.answer.decision.verdict} @ ${turn.result.answer.decision.updatedAt.replace("T", " ").slice(0, 19)}Z`
                    : ""}
                </p>
                {turn.result.answer.guard.violations.length > 0 && (
                  <p className="sev-WARNING" data-testid="ai-guard">
                    External answer withheld: {turn.result.answer.guard.violations.join("; ")}
                  </p>
                )}
                {turn.result.answer.facts.length > 0 && (
                  <table className="mtf" data-testid="ai-facts">
                    <tbody>
                      {turn.result.answer.facts.map((f) => (
                        <tr key={`${f.label}:${f.source}`}>
                          <td>{f.label}</td>
                          <td>{f.value}</td>
                          <td className="muted">{f.source}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {turn.result.answer.unknowns.length > 0 && (
                  <p className="muted" data-testid="ai-unknowns">
                    Unknown: {turn.result.answer.unknowns.join(" · ")}
                  </p>
                )}
                {turn.result.answer.proposal && (
                  <p data-testid="ai-proposal">
                    Proposed watch: {turn.result.answer.proposal.symbol} {turn.result.answer.proposal.direction ?? "either direction"}{" "}
                    <button
                      type="button"
                      className="tf"
                      onClick={() => void confirmWatch(turn.result?.status === "READY" ? turn.result.answer.proposal?.direction ?? null : null)}
                    >
                      Confirm watch
                    </button>
                  </p>
                )}
                <p className="muted">Tools: {turn.result.answer.tools.map((t) => `${t.name} ${t.status}`).join(", ")}</p>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
