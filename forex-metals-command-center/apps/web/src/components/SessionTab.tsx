import type { MasterDecision, SessionInstance } from "@fmcc/shared-types";

import { formatSessionState, readSessionState, type SessionLoadState } from "@/lib/sessions";

const clockTime = (iso: string) => iso.slice(11, 16);
const utcTime = (iso: string) => iso.replace("T", " ").slice(5, 16);

function levels(i: SessionInstance): string {
  if (i.high === null || i.low === null) return "—";
  return `${i.low}–${i.high} (range ${i.range})`;
}

export function SessionTab({ sessions, decision }: { sessions: SessionLoadState | null; decision: MasterDecision }) {
  if (!sessions) return <p className="muted">Loading session clock…</p>;
  if (sessions.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="session-unavailable">
        <strong>SESSION DATA UNAVAILABLE</strong>
        <br />
        {sessions.reason}
      </p>
    );
  }
  const a = sessions.analysis;
  const c = a.clock;
  // Levels come from data: show the latest trading day in the data, which differs from the clock's when stale.
  const dataDay = a.instances.length ? a.instances[a.instances.length - 1]!.tradingDay : null;
  const day = a.instances.filter((i) => i.tradingDay === dataDay);
  const recentJudas = a.judas.slice(-5).reverse();
  const state = readSessionState(decision);
  return (
    <div data-testid="session-tab">
      <h2>Clock (time only)</h2>
      <dl data-testid="session-clock">
        <dt>New York / London</dt>
        <dd>
          {clockTime(c.newYorkTime)} / {clockTime(c.londonTime)}
        </dd>
        <dt>Trading day</dt>
        <dd>
          {c.tradingDay} · market {c.marketStatus}
        </dd>
        <dt>Active</dt>
        <dd>{[...c.activeSessions, ...c.activeKillZones].join(" + ") || "no session window"}</dd>
        <dt>Time quality</dt>
        <dd>{c.timeQuality}</dd>
        <dt>Next session</dt>
        <dd>{c.nextSession ? `${c.nextSession} at ${utcTime(c.nextSessionStart ?? "")} UTC` : "—"}</dd>
      </dl>

      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="session-eligibility">
        {a.sourceTimeframe} levels · data {a.quality} ·{" "}
        {a.eligibleForDecision ? "eligible for the decision" : `NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>

      {a.instances.length === 0 ? (
        <p className="muted">No session levels (data {a.quality}).</p>
      ) : (
        <>
          <h2>
            Sessions · trading day {dataDay}
            {dataDay !== c.tradingDay ? " (latest in data)" : ""}
          </h2>
          <table className="mtf" data-testid="session-instances">
            <tbody>
              {day.map((i) => (
                <tr key={i.id}>
                  <td>{i.session}</td>
                  <td className="muted">{i.state}</td>
                  <td>{levels(i)}</td>
                  <td className="muted">{i.asianRangeState ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl data-testid="session-levels">
            <dt>Session quality</dt>
            <dd>{a.sessionQuality ?? "—"}</dd>
            <dt>Daily / NY midnight / weekly open</dt>
            <dd>
              {a.opens.dailyOpen ?? "—"} / {a.opens.nyMidnightOpen ?? "—"} / {a.opens.weeklyOpen ?? "—"}
            </dd>
            <dt>Previous session</dt>
            <dd>
              {a.previousSession
                ? `${a.previousSession.session} ${a.previousSession.low}–${a.previousSession.high}`
                : "—"}
            </dd>
            <dt>ADR</dt>
            <dd>
              {a.adr
                ? `${a.adr.adr.toFixed(2)} (${a.adr.periodDays}d) · used ${a.adr.pctUsed ?? "—"}% · ${a.adr.expansion ?? "—"}`
                : "unavailable (not enough closed days)"}
            </dd>
          </dl>
          <h2>Judas swings</h2>
          {recentJudas.length === 0 ? (
            <p className="muted">None in this window.</p>
          ) : (
            <ul className="event-list" data-testid="session-judas">
              {recentJudas.map((j) => (
                <li key={j.id} className={j.direction === "BULLISH" ? "bull" : "bear"}>
                  {j.tradingDay} {j.session} {j.direction} {j.status} · {j.detail}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <p className="muted">
        Decision context: <strong>{state ? formatSessionState(state) : "none"}</strong>. Time never creates a trade by
        itself. Windows are New York time (DST-aware); holidays are not modelled, so a holiday session shows as
        INCOMPLETE. PO3 and setup expiration are part of the setup state machine (OVERVIEW).
      </p>
    </div>
  );
}
