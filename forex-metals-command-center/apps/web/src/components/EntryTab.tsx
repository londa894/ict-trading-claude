import type { EvaluationLoadState } from "@/lib/evaluation";

const fmt = (v: number | null) => (v === null ? "—" : String(v));

/** ENTRY tab: the deterministic evaluation, explicitly NOT AUTHORIZED while required gates are missing. */
export function EntryTab({ evaluation }: { evaluation: EvaluationLoadState | null }) {
  if (!evaluation) return <p className="muted">Loading evaluation…</p>;
  if (evaluation.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="entry-unavailable">
        <strong>EVALUATION UNAVAILABLE</strong>
        <br />
        {evaluation.reason}
      </p>
    );
  }
  const e = evaluation.evaluation;
  const plan = e.plan;
  return (
    <div data-testid="entry-tab">
      <p className="banner-inline" data-testid="entry-authority">
        NOT AUTHORIZED · missing gates: {e.missingGates.join(", ")} · outcome {e.outcome}
      </p>
      {!e.eligibleForDecision && (
        <p className="muted" data-testid="entry-ineligible">
          NOT used by the decision: {e.ineligibility.join(", ")}
        </p>
      )}
      <dl data-testid="entry-summary">
        <dt>Setup</dt>
        <dd>{e.setupState ? `${e.direction} ${e.setupType} · ${e.setupState}` : "no open setup"}</dd>
        <dt>Score / grade</dt>
        <dd>
          {e.score === null ? "—" : `${e.score} / ${e.evaluatedMax} evaluated · ${e.grade}`} (a ranking, not a
          probability)
        </dd>
        <dt>Confidence</dt>
        <dd>
          {e.confidence} · conflict {e.conflictScore} · data quality {e.dataQualityScore}
        </dd>
      </dl>

      {plan && (
        <>
          <h2>Confirmed plan (not authorized)</h2>
          <table className="mtf" data-testid="entry-plan">
            <tbody>
              <tr>
                <td>Model</td>
                <td>
                  {plan.model} · {plan.mode}
                  {plan.researchOnly ? " · RESEARCH ONLY" : ""}
                </td>
              </tr>
              <tr>
                <td>Entry / stop</td>
                <td>
                  {plan.entry} / {plan.stop} (risk {plan.risk})
                </td>
              </tr>
              <tr>
                <td>TP1 / TP2 / TP3</td>
                <td>
                  {plan.tp1} / {fmt(plan.tp2)} / {fmt(plan.tp3)}
                </td>
              </tr>
              <tr>
                <td>R:R</td>
                <td>
                  {plan.rr1} / {fmt(plan.rr2)} / {fmt(plan.rr3)} (min {plan.minRr})
                </td>
              </tr>
            </tbody>
          </table>
        </>
      )}

      {e.components.length > 0 && (
        <>
          <h2>Score breakdown</h2>
          <table className="mtf" data-testid="entry-components">
            <tbody>
              {e.components.map((c) => (
                <tr key={c.factor}>
                  <td>{c.factor}</td>
                  <td>{c.status === "NOT_EVALUATED" ? "n/e" : `${c.points}/${c.maxPoints}`}</td>
                  <td className="muted">{c.detail}</td>
                </tr>
              ))}
              {e.adjustments.map((a) => (
                <tr key={a.name}>
                  <td>{a.name}</td>
                  <td>{a.points}</td>
                  <td className="muted">{a.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {e.warnings.length > 0 && (
        <p data-testid="entry-warnings">
          <span className="label">Warnings</span>
          <br />
          {e.warnings.join(" · ")}
        </p>
      )}
      {e.evidenceFor.length + e.evidenceAgainst.length > 0 && <h2>Evidence</h2>}
      <ul className="event-list" data-testid="entry-evidence">
        {e.evidenceFor.map((x) => (
          <li key={`for:${x}`} className="bull">
            + {x}
          </li>
        ))}
        {e.evidenceAgainst.map((x) => (
          <li key={`against:${x}`} className="bear">
            − {x}
          </li>
        ))}
      </ul>
      <h2>Devil&apos;s advocate</h2>
      <ul className="event-list" data-testid="entry-devils-advocate">
        {e.devilsAdvocate.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
      {e.hardBlockers.length > 0 && (
        <p className="muted" data-testid="entry-hard-blockers">
          Hard blockers: {e.hardBlockers.join(", ")}
        </p>
      )}
    </div>
  );
}
