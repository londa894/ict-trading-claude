import type { SetupStepState } from "@fmcc/shared-types";

import type { SetupLoadState } from "@/lib/setups";

const MARK: Record<SetupStepState["status"], string> = { DONE: "✓", PENDING: "·", NOT_EVALUATED: "n/e" };

/** Setup progress / missing-confirmation panel (spec STEP 11 signature UI). Context only; never a trade signal. */
export function SetupPanel({ setups }: { setups: SetupLoadState | null }) {
  if (!setups) return <p className="muted">Loading setup state…</p>;
  if (setups.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="setup-unavailable">
        <strong>SETUP STATE UNAVAILABLE</strong>
        <br />
        {setups.reason}
      </p>
    );
  }
  const a = setups.analysis;
  const c = a.current;
  const last = [...a.setups].reverse().find((s) => s.terminal);
  return (
    <section id="setup-progress" data-testid="setup-panel">
      <h2>Setup progress ({a.timeframe})</h2>
      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="setup-eligibility">
        State <strong>{a.currentState ?? "NO_SETUP"}</strong> · bias {a.bias.direction ?? "NONE"} (
        {a.bias.timeframes.join("+")}) · PO3 {a.po3.phase}
        {a.eligibleForDecision ? "" : ` · NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>
      {c ? (
        <>
          <p data-testid="setup-next">
            <span className="label">Missing next</span>
            <br />
            {c.direction} {c.setupType}: {c.nextRequiredEvent}
          </p>
          <ul className="event-list" data-testid="setup-steps">
            {c.steps.map((s) => (
              <li key={s.step} className={s.status === "DONE" ? "bull" : "muted"}>
                {MARK[s.status]} {s.step} · {s.detail}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="muted" data-testid="setup-none">
          No open setup{a.bias.direction ? "" : " (no common HTF bias)"}.
        </p>
      )}
      {last && (
        <p className="muted" data-testid="setup-last">
          Last closed setup: {last.direction} {last.state} · {last.reason}
        </p>
      )}
      <p className="muted">
        Setup states describe progress only. A confirmed plan stays BLOCKED: LONG_READY / SHORT_READY need clean risk and
        news gates and FULL verdict authority, and are never shown before then.
      </p>
    </section>
  );
}
