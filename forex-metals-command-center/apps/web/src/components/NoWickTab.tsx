import type { MasterDecision, NoWickEvent, ScoreComponent } from "@fmcc/shared-types";

import { formatNoWickState, isMeaningful, readNoWickState, type NoWickLoadState } from "@/lib/noWick";

const RECENT = 6;

const when = (iso: string) => iso.replace("T", " ").slice(5, 16);

function componentText(c: ScoreComponent): string {
  return c.status === "NOT_EVALUATED" ? `${c.factor} n/e` : `${c.factor} ${c.points}/${c.maxPoints}`;
}

function eventLabel(e: NoWickEvent): string {
  return `${when(e.time)} ${e.classification} ${e.strength} · body ${e.bodyAtr} ATR${e.insideBar ? " · inside bar" : ""}`;
}

export function NoWickTab({ noWick, decision }: { noWick: NoWickLoadState | null; decision: MasterDecision }) {
  if (!noWick) return <p className="muted">Loading No Wick analysis…</p>;
  if (noWick.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="nw-unavailable">
        <strong>NO WICK UNAVAILABLE</strong>
        <br />
        {noWick.reason}
      </p>
    );
  }
  const a = noWick.analysis;
  const recent = a.events.filter(isMeaningful).slice(-RECENT).reverse();
  const latest = recent[0];
  const active = a.zones.filter((z) => z.active).sort((x, y) => y.relevanceScore - x.relevanceScore);
  const state = readNoWickState(decision);
  return (
    <div data-testid="nw-tab">
      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="nw-eligibility">
        {a.timeframe} · data {a.quality} ·{" "}
        {a.eligibleForDecision ? "eligible for the decision" : `NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>

      <h2>Recent no-wick candles (MEANINGFUL+)</h2>
      {recent.length === 0 ? (
        <p className="muted">No meaningful no-wick candle in this window.</p>
      ) : (
        <table className="mtf" data-testid="nw-events">
          <tbody>
            {recent.map((e) => (
              <tr key={e.id}>
                <td className={e.direction === "BULLISH" ? "state-BULLISH" : "state-BEARISH"}>{eventLabel(e)}</td>
                <td className="muted">
                  q {e.candleQualityScore} · ctx {e.contextScore} · rel {e.relevanceScore}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {latest && (
        <>
          <h2>Latest context components</h2>
          <p className="muted" data-testid="nw-components">
            {latest.contextComponents.map(componentText).join(" · ")}
          </p>
        </>
      )}

      <h2>Active rebalance zones ({active.length})</h2>
      {active.length === 0 ? (
        <p className="muted">No active zones.</p>
      ) : (
        <table className="mtf" data-testid="nw-zones">
          <tbody>
            {active.slice(0, 8).map((z) => (
              <tr key={z.id}>
                <td className={z.direction === "BULLISH" ? "state-BULLISH" : "state-BEARISH"}>
                  {z.direction === "BULLISH" ? "Bull" : "Bear"} {z.closeLevel}→{z.openLevel} (50% {z.level50})
                </td>
                <td className="muted">
                  {z.state} {z.rebalancePct}%
                </td>
                <td className="muted">
                  rel {z.relevanceScore}
                  {z.fvgOverlapIds.length ? ` · FVG×${z.fvgOverlapIds.length}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="muted">
        Decision context (M15): <strong>{state ? formatNoWickState(state) : "none"}</strong>. Scores rank candles; they
        are not probabilities, and No Wick never authorizes a trade on its own. n/e = not evaluated: news arrives in
        Phase 13, order-block overlap in a later phase. Session points come from the candle's time quality.
      </p>
    </div>
  );
}
