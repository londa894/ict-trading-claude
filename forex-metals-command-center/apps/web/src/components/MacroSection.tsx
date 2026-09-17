import type { MacroLoadState } from "@/lib/macro";

const signed = (v: number | null) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v}`);

/** MACRO tab (Phase 14): macro drivers, bias and the state versus the open setup. Context only: never blocks. */
export function MacroSection({ macro }: { macro: MacroLoadState | null }) {
  if (!macro) return <p className="muted">Loading macro…</p>;
  if (macro.status === "UNAVAILABLE") {
    return (
      <p className="not-available" role="alert" data-testid="macro-unavailable">
        <strong>MACRO UNAVAILABLE</strong>
        <br />
        {macro.reason}
      </p>
    );
  }
  const m = macro.macro;
  if (!m.available) {
    return (
      <div data-testid="macro-section">
        <p className="not-available" data-testid="macro-not-available">
          <strong>MACRO NOT EVALUATED</strong>
          <br />
          {m.reason} · the MACRO score factor is not evaluated; macro never blocks.
        </p>
      </div>
    );
  }
  return (
    <div data-testid="macro-section">
      <p className="muted">Macro is context: it adjusts score and confidence and never blocks or creates a trade.</p>
      <dl>
        <dt>Macro bias</dt>
        <dd data-testid="macro-bias">
          {m.bias} (score {signed(m.score)})
          {m.state ? ` · ${m.state} vs the ${m.direction} setup` : " · no open setup direction"}
        </dd>
        <dt>Correlation</dt>
        <dd data-testid="macro-correlation">
          {m.correlation ? `${m.correlation.series} ${m.correlation.regime}${m.correlation.coefficient !== null ? ` (r ${m.correlation.coefficient})` : ""}` : "—"}
        </dd>
        <dt>Data</dt>
        <dd data-testid="macro-data">
          {m.provider}
          {m.isSynthetic ? " · SYNTHETIC (not scored)" : ""}
          {m.source ? ` · ${m.source}` : ""}
        </dd>
      </dl>
      <div className="table-scroll">
        <table className="scan-table" data-testid="macro-drivers">
          <thead>
            <tr>
              <th>Driver</th>
              <th>Direction</th>
              <th>Rel.</th>
              <th>Weight</th>
              <th>Contribution</th>
            </tr>
          </thead>
          <tbody>
            {m.drivers.map((d) => (
              <tr key={`${d.configured}-${d.series}`}>
                <td title={d.detail}>
                  {d.series}
                  {d.series !== d.configured ? ` (for ${d.configured})` : ""}
                </td>
                <td>{d.direction ?? "NOT EVALUATED"}</td>
                <td>{d.relationship > 0 ? "+" : "−"}</td>
                <td>{d.weight}</td>
                <td>{signed(d.contribution)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {m.warnings.length > 0 && <p className="muted" data-testid="macro-warnings">{m.warnings.join(" · ")}</p>}
    </div>
  );
}
