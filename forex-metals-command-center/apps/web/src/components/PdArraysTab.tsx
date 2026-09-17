import type { MasterDecision, PdArrayZone } from "@fmcc/shared-types";

import type { PdArrayLoadState } from "@/lib/pdArrays";

const RECENT = 6;

function zoneLabel(z: PdArrayZone): string {
  const status = z.type === "IFVG" ? ` · ${z.ifvgStatus}` : "";
  return `${z.direction === "BULLISH" ? "Bull" : "Bear"} ${z.type} ${z.bottom}–${z.top}${status}`;
}

export function PdArraysTab({ pdArrays, decision }: { pdArrays: PdArrayLoadState | null; decision: MasterDecision }) {
  if (!pdArrays) return <p className="muted">Loading PD arrays…</p>;
  if (pdArrays.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="pd-unavailable">
        <strong>PD ARRAYS UNAVAILABLE</strong>
        <br />
        {pdArrays.reason}
      </p>
    );
  }
  const a = pdArrays.analysis;
  const active = a.zones.filter((z) => z.active).sort((x, y) => (y.qualityScore ?? 0) - (x.qualityScore ?? 0));
  const potential = a.zones.filter((z) => z.ifvgStatus === "POTENTIAL_IFVG");
  const recentDisp = a.displacements.filter((d) => d.grade !== "WEAK").slice(-RECENT).reverse();
  return (
    <div data-testid="pd-tab">
      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="pd-eligibility">
        {a.timeframe} · data {a.quality} ·{" "}
        {a.eligibleForDecision ? "eligible for the decision" : `NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>

      <h2>Active FVG / IFVG ({active.length})</h2>
      {active.length === 0 ? (
        <p className="muted">No active zones.</p>
      ) : (
        <table className="mtf" data-testid="active-zones">
          <tbody>
            {active.slice(0, 8).map((z) => (
              <tr key={z.id}>
                <td className={z.direction === "BULLISH" ? "state-BULLISH" : "state-BEARISH"}>{zoneLabel(z)}</td>
                <td className="muted">
                  {z.state} {z.fillPct}%
                </td>
                <td className="muted">q {z.qualityScore}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Potential IFVG ({potential.length})</h2>
      {potential.length === 0 ? (
        <p className="muted">None awaiting confirmation.</p>
      ) : (
        <ul className="event-list" data-testid="potential-ifvg">
          {potential.map((z) => (
            <li key={z.id}>{zoneLabel(z)} · needs displacement or acceptance</li>
          ))}
        </ul>
      )}

      <h2>Recent displacement (MODERATE+)</h2>
      {recentDisp.length === 0 ? (
        <p className="muted">No meaningful displacement in this window.</p>
      ) : (
        <ul className="event-list" data-testid="displacements">
          {recentDisp.map((d) => (
            <li key={d.id} className={d.direction === "BULLISH" ? "bull" : "bear"}>
              {d.time.replace("T", " ").slice(5, 16)} {d.direction} {d.grade} · {d.magnitudeAtr} ATR · {d.candleCount} candle(s)
            </li>
          ))}
        </ul>
      )}
      <p className="muted">
        Decision uses (M15): <strong>{decision.displacement ?? "none"}</strong>. Quality ranks zones; it is not a
        probability. Wicks can fill a zone; only a close through the far edge invalidates it. Order blocks, breakers,
        BPR and entry-zone ranking arrive in later phases.
      </p>
    </div>
  );
}
