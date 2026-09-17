import type { LevelStructure, MasterDecision, MtfStructureResponse, StructureEvent } from "@fmcc/shared-types";

import { EVENT_TEXT, type StructureLoadState } from "@/lib/structure";

const RECENT_EVENTS = 8;

function eventLine(e: StructureEvent): string {
  const t = e.time.replace("T", " ").slice(5, 16);
  const status = e.status === "POTENTIAL" ? " (potential, wick-only)" : "";
  const liq = e.liquidityQualifier === "PRESENT" ? " · after liquidity taken" : "";
  const disp = e.displacementQualifier === "PRESENT" ? " · with displacement" : "";
  return `${t} ${e.level === "INTERNAL" ? "int" : "EXT"} ${EVENT_TEXT[e.type]} ${e.direction} @ ${e.price}${status}${liq}${disp}`;
}

function Level({ level }: { level: LevelStructure | null }) {
  if (!level) return null;
  return (
    <div className="structure-level" data-testid={`level-${level.level}`}>
      <h2>
        {level.level} <small className="muted">pivot {level.pivotLength}</small>
      </h2>
      <dl>
        <dt>State</dt>
        <dd className={`state-${level.state}`}>{level.state}</dd>
        <dt>Trend</dt>
        <dd>{level.trend}</dd>
        <dt>Protected high</dt>
        <dd>{level.protectedHigh ? level.protectedHigh.price : "—"}</dd>
        <dt>Protected low</dt>
        <dd>{level.protectedLow ? level.protectedLow.price : "—"}</dd>
        <dt>Bars since break</dt>
        <dd>{level.barsSinceLastBreak ?? "—"}</dd>
      </dl>
    </div>
  );
}

export function StructureTab({
  structure,
  alignment,
  decision,
}: {
  structure: StructureLoadState | null;
  alignment: MtfStructureResponse | null;
  decision: MasterDecision;
}) {
  if (!structure) return <p className="muted">Loading structure…</p>;
  if (structure.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="structure-unavailable">
        <strong>STRUCTURE UNAVAILABLE</strong>
        <br />
        {structure.reason}
      </p>
    );
  }
  const a = structure.analysis;
  const recent = a.events.slice(-RECENT_EVENTS).reverse();
  return (
    <div data-testid="structure-tab">
      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="structure-eligibility">
        {a.timeframe} · data {a.quality} ·{" "}
        {a.eligibleForDecision
          ? "eligible for the decision"
          : `NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>
      <Level level={a.external} />
      <Level level={a.internal} />

      <h2>Recent events</h2>
      {recent.length === 0 ? (
        <p className="muted">No structure events in this window.</p>
      ) : (
        <ul className="event-list" data-testid="structure-events">
          {recent.map((e) => (
            <li key={e.id} className={e.direction === "BULLISH" ? "bull" : "bear"}>
              {eventLine(e)}
            </li>
          ))}
        </ul>
      )}

      <h2>Multi-timeframe (external)</h2>
      {alignment ? (
        <>
          <p data-testid="mtf-alignment">
            Alignment <strong>{alignment.alignment}</strong> · HTF bias (D1+H4) <strong>{alignment.htfBias}</strong>
          </p>
          <table className="mtf">
            <tbody>
              {alignment.timeframes.map((t) => (
                <tr key={t.timeframe}>
                  <td>{t.timeframe}</td>
                  <td className={`state-${t.state ?? "UNCLEAR"}`}>{t.state ?? "—"}</td>
                  <td className="muted">{t.eligibleForDecision ? "eligible" : t.ineligibility.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted">Alignment unavailable.</p>
      )}
      <p className="muted">
        Decision uses: HTF bias <strong>{decision.htfBias}</strong> · structure event{" "}
        <strong>{decision.structureEvent ?? "none"}</strong>. Events marked “after liquidity taken” followed an opposite-side sweep/reclaim (Phase 3); “with
        displacement” means a same-direction MODERATE+ displacement within 3 bars of the break (Phase 4). A numeric alignment score arrives with scoring (Phase 8).
      </p>
    </div>
  );
}
