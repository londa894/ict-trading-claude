import type { DolTarget, LiquidityEvent, LiquidityPool, MasterDecision } from "@fmcc/shared-types";

import type { LiquidityLoadState } from "@/lib/liquidity";

const RECENT_EVENTS = 8;
const NEAREST = 4;

function target(label: string, t: DolTarget | null) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{t ? `${t.label} @ ${t.price} · magnet ${t.magnetScore} · ${t.distanceAtr} ATR` : "—"}</dd>
    </>
  );
}

function eventLine(e: LiquidityEvent): string {
  const t = e.time.replace("T", " ").slice(5, 16);
  return `${t} ${e.type} ${e.side} ${e.poolType} @ ${e.price}`;
}

function nearest(pools: LiquidityPool[], side: "BSL" | "SSL"): LiquidityPool[] {
  return pools
    .filter((p) => !p.taken && p.side === side && p.distanceAtr !== null)
    .sort((a, b) => (a.distanceAtr ?? 0) - (b.distanceAtr ?? 0))
    .slice(0, NEAREST);
}

function PoolRows({ pools }: { pools: LiquidityPool[] }) {
  if (pools.length === 0) {
    return (
      <tr>
        <td className="muted">none</td>
      </tr>
    );
  }
  return (
    <>
      {pools.map((p) => (
        <tr key={p.id}>
          <td>{p.label}</td>
          <td>{p.price}</td>
          <td className={p.state === "APPROACHING" ? "state-TRANSITIONING" : "muted"}>{p.state}</td>
          <td className="muted">{p.magnetScore}</td>
        </tr>
      ))}
    </>
  );
}

export function LiquidityTab({ liquidity, decision }: { liquidity: LiquidityLoadState | null; decision: MasterDecision }) {
  if (!liquidity) return <p className="muted">Loading liquidity…</p>;
  if (liquidity.status === "UNAVAILABLE") {
    return (
      <p className="not-available" data-testid="liquidity-unavailable">
        <strong>LIQUIDITY UNAVAILABLE</strong>
        <br />
        {liquidity.reason}
      </p>
    );
  }
  const a = liquidity.analysis;
  const recent = a.events.filter((e) => e.type !== "TOUCH").slice(-RECENT_EVENTS).reverse();
  const keyLevels = ["PWH", "PWL", "PDH", "PDL"].map((type) =>
    a.pools.filter((p) => p.type === type).sort((x, y) => Date.parse(y.knownAt) - Date.parse(x.knownAt))[0],
  );
  return (
    <div data-testid="liquidity-tab">
      <p className={a.eligibleForDecision ? "muted" : "banner-inline"} data-testid="liquidity-eligibility">
        {a.timeframe} · data {a.quality} ·{" "}
        {a.eligibleForDecision ? "eligible for the decision" : `NOT used by the decision: ${a.ineligibility.join(", ")}`}
      </p>

      <h2>Draw on liquidity</h2>
      {a.dol ? (
        <dl data-testid="dol">
          <dt>Confidence</dt>
          <dd className={a.dol.confidence === "UNCLEAR" ? "state-TRANSITIONING" : "value"}>{a.dol.confidence}</dd>
          {target("Primary", a.dol.primary)}
          {target("Secondary", a.dol.secondary)}
          <dt>Why</dt>
          <dd>{a.dol.reason}</dd>
        </dl>
      ) : (
        <p className="muted">No DOL for this window.</p>
      )}

      <h2>Key levels</h2>
      <table className="mtf" data-testid="key-levels">
        <tbody>
          {keyLevels.map((p, i) =>
            p ? (
              <tr key={p.id}>
                <td>{p.type}</td>
                <td>{p.price}</td>
                <td className="muted">{p.state}</td>
              </tr>
            ) : (
              <tr key={i}>
                <td className="muted" colSpan={3}>
                  {["PWH", "PWL", "PDH", "PDL"][i]} unavailable
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>

      <h2>Nearest untaken · above (BSL)</h2>
      <table className="mtf" data-testid="nearest-bsl">
        <tbody>
          <PoolRows pools={nearest(a.pools, "BSL")} />
        </tbody>
      </table>
      <h2>Nearest untaken · below (SSL)</h2>
      <table className="mtf" data-testid="nearest-ssl">
        <tbody>
          <PoolRows pools={nearest(a.pools, "SSL")} />
        </tbody>
      </table>

      <h2>Recent liquidity events</h2>
      {recent.length === 0 ? (
        <p className="muted">No sweeps, breaks or runs in this window.</p>
      ) : (
        <ul className="event-list" data-testid="liquidity-events">
          {recent.map((e) => (
            <li key={e.id} className={e.side === "BSL" ? "bear" : "bull"}>
              {eventLine(e)}
            </li>
          ))}
        </ul>
      )}
      <p className="muted">
        Decision uses (H1 DOL, M15 event): <strong>{decision.primaryDol ?? "none"}</strong> · event{" "}
        <strong>{decision.liquidityEvent ?? "none"}</strong>. Magnet score ranks pools; it is not a probability. Session
        highs/lows arrive with Phase 6.
      </p>
    </div>
  );
}
