"use client";

import type { RiskAssessment } from "@fmcc/shared-types";
import { useState, type FormEvent } from "react";

import { API_BASE_URL } from "@/lib/api";
import type { EvaluationLoadState } from "@/lib/evaluation";
import {
  buildCalculationRequest,
  calculateRisk,
  EMPTY_FORM,
  money,
  type CalculatorForm,
  type RiskLoadState,
} from "@/lib/risk";

const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(v));

function RiskView({ risk, testId }: { risk: RiskAssessment; testId: string }) {
  const ccy = risk.currency;
  const p = risk.position;
  const b = risk.budget;
  return (
    <div data-testid={testId}>
      <dl>
        <dt>Risk status</dt>
        <dd className={risk.status === "LOCKED" ? "sev-ERROR" : undefined} data-testid={`${testId}-status`}>
          {risk.status}
        </dd>
        <dt>Profile</dt>
        <dd>{risk.profile ? `${risk.profile} · ${ccy}` : "—"}</dd>
        <dt>Position size</dt>
        <dd>{risk.sizeStatus ?? "no plan to size"}</dd>
        <dt>News</dt>
        <dd>{risk.news} (news gate)</dd>
      </dl>
      {risk.status === "NOT_CONFIGURED" && (
        <p className="muted" data-testid={`${testId}-setup`}>
          No account profile. Copy <code>config/risk_profile.example.json</code> to a <code>*.local.json</code> file
          (git-ignored), enter your balance, today&apos;s state and your broker&apos;s contract spec, and set{" "}
          <code>RISK_PROFILE_PATH</code> for the API. Update the state every trading day.
        </p>
      )}
      {risk.profileError && <p className="sev-ERROR">{risk.profileError}</p>}
      {risk.locks.length > 0 && (
        <>
          <h2>Locks (final veto)</h2>
          <ul className="event-list" data-testid={`${testId}-locks`}>
            {risk.locks.map((l) => (
              <li key={l.lock} className="sev-ERROR">
                {l.lock}: {l.detail}
              </li>
            ))}
          </ul>
        </>
      )}
      {b && risk.limits && (
        <>
          <h2>Budget</h2>
          <table className="mtf" data-testid={`${testId}-budget`}>
            <tbody>
              <tr>
                <td>Per trade ({risk.limits.riskPerTradePct}%)</td>
                <td>{money(b.riskPerTradeAmount, ccy)}</td>
              </tr>
              <tr>
                <td>Daily left ({risk.limits.dailyRiskLimitPct}%)</td>
                <td>{money(b.dailyRemaining, ccy)}</td>
              </tr>
              <tr>
                <td>Weekly left ({risk.limits.weeklyRiskLimitPct}%)</td>
                <td>{money(b.weeklyRemaining, ccy)}</td>
              </tr>
              <tr>
                <td>Open risk / left</td>
                <td>
                  {money(b.openRisk, ccy)} / {money(b.openRiskRemaining, ccy)}
                </td>
              </tr>
              {b.propRemaining !== null && (
                <tr>
                  <td>Prop drawdown left</td>
                  <td>{money(b.propRemaining, ccy)}</td>
                </tr>
              )}
              <tr>
                <td>
                  <strong>Effective budget</strong>
                </td>
                <td>
                  <strong>{money(b.effectiveRiskAmount, ccy)}</strong>
                </td>
              </tr>
            </tbody>
          </table>
        </>
      )}
      {p && (
        <>
          <h2>Position size (not authorized)</h2>
          <table className="mtf" data-testid={`${testId}-position`}>
            <tbody>
              <tr>
                <td>
                  {p.direction} entry / stop
                </td>
                <td>
                  {p.entry} / {p.stop}
                </td>
              </tr>
              <tr>
                <td>Distance · points · pips</td>
                <td>
                  {p.priceDistance} · {fmt(p.points)} · {fmt(p.pips)}
                </td>
              </tr>
              <tr>
                <td>Spread · sizing distance</td>
                <td>
                  {fmt(p.spread)} · {p.sizingDistance}
                </td>
              </tr>
              <tr>
                <td>Volume</td>
                <td data-testid={`${testId}-volume`}>
                  {fmt(p.volume)}
                  {p.volumeStep !== null ? ` (step ${p.volumeStep}, min ${fmt(p.minVolume)})` : ""}
                </td>
              </tr>
              <tr>
                <td>Risk at stop</td>
                <td>
                  {money(p.riskAmount, ccy)}
                  {p.riskPct !== null ? ` (${p.riskPct}%)` : ""} · without spread {money(p.riskAmountWithoutSpread, ccy)}
                </td>
              </tr>
              <tr>
                <td>Margin</td>
                <td>{money(p.marginRequired, ccy)}</td>
              </tr>
            </tbody>
          </table>
          <p className="muted">{p.detail}</p>
        </>
      )}
      {risk.volatility && (
        <p className="muted">
          Volatility {risk.volatility.timeframe}: ATR {risk.volatility.atr} = {risk.volatility.ratio}x baseline (lock at{" "}
          {risk.volatility.lockRatio}x)
        </p>
      )}
      {risk.warnings.length > 0 && <p className="muted">Warnings: {risk.warnings.join(" · ")}</p>}
      {risk.blockers.length > 0 && <p className="muted">Blockers: {risk.blockers.join(", ")}</p>}
    </div>
  );
}

type Field = keyof CalculatorForm;
const NUMBER_FIELDS: ReadonlyArray<[Field, string]> = [
  ["entry", "Entry"],
  ["stop", "Stop"],
  ["balance", "Balance"],
  ["leverage", "Leverage (optional)"],
  ["contractSize", "Contract size"],
  ["tickSize", "Tick size"],
  ["tickValue", "Tick value (quote ccy, 1.0 volume)"],
  ["minVolume", "Min volume"],
  ["volumeStep", "Volume step"],
  ["typicalSpread", "Typical spread (price)"],
  ["platformPipSize", "Platform pip size (optional)"],
  ["conversionRate", "1 quote ccy = ? account ccy"],
];

/** RISK tab: the server-side profile applied to the live plan, plus a what-if calculator that stores nothing. */
export function RiskTab({
  symbol,
  evaluation,
  fetcher,
}: {
  symbol: string;
  evaluation: EvaluationLoadState | null;
  fetcher?: typeof fetch;
}) {
  const [form, setForm] = useState<CalculatorForm>(EMPTY_FORM);
  const [result, setResult] = useState<RiskLoadState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: Field, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const built = buildCalculationRequest(symbol, form);
    if (!built.ok) {
      setError(built.error);
      setResult(null);
      return;
    }
    setError(null);
    setBusy(true);
    setResult(await calculateRisk(API_BASE_URL, built.request, fetcher));
    setBusy(false);
  };

  const live = evaluation?.status === "READY" ? evaluation.evaluation.risk : null;
  return (
    <div data-testid="risk-tab">
      <p className="banner-inline" data-testid="risk-authority">
        NOT AUTHORIZED · risk has final veto · verdict authority FAIL_SAFE_ONLY
      </p>
      <h2>Live assessment</h2>
      {!evaluation ? (
        <p className="muted">Loading risk…</p>
      ) : evaluation.status === "UNAVAILABLE" ? (
        <p className="not-available" data-testid="risk-unavailable">
          <strong>RISK UNAVAILABLE</strong>
          <br />
          {evaluation.reason}
        </p>
      ) : live ? (
        <RiskView risk={live} testId="risk-live" />
      ) : (
        <p className="not-available">The risk gate is not wired on this server.</p>
      )}

      <h2>What-if calculator</h2>
      <p className="muted">
        Values stay in this form only: nothing is saved. Account locks and volatility are not checked here. Leave the
        contract spec empty if you do not know it; the size then stays unverified.
      </p>
      <form className="risk-form" onSubmit={(e) => void onSubmit(e)} data-testid="risk-form">
        <label>
          Direction
          <select className="tz-select" value={form.direction} onChange={(e) => set("direction", e.target.value)}>
            <option value="BULLISH">BULLISH</option>
            <option value="BEARISH">BEARISH</option>
          </select>
        </label>
        <label>
          Profile
          <select className="tz-select" value={form.profile} onChange={(e) => set("profile", e.target.value)}>
            <option value="CONSERVATIVE">CONSERVATIVE</option>
            <option value="STANDARD">STANDARD</option>
            <option value="AGGRESSIVE">AGGRESSIVE</option>
          </select>
        </label>
        <label>
          Account ccy
          <input value={form.currency} maxLength={3} onChange={(e) => set("currency", e.target.value.toUpperCase())} />
        </label>
        <label>
          Quote ccy
          <input
            value={form.quoteCurrency}
            maxLength={3}
            onChange={(e) => set("quoteCurrency", e.target.value.toUpperCase())}
          />
        </label>
        {NUMBER_FIELDS.map(([key, label]) => (
          <label key={key}>
            {label}
            <input inputMode="decimal" value={form[key]} onChange={(e) => set(key, e.target.value)} name={key} />
          </label>
        ))}
        <button type="submit" className="tf" disabled={busy}>
          {busy ? "Calculating…" : "Calculate"}
        </button>
      </form>
      {error && (
        <p className="sev-ERROR" data-testid="risk-form-error">
          {error}
        </p>
      )}
      {result?.status === "UNAVAILABLE" && (
        <p className="sev-ERROR" data-testid="risk-calc-unavailable">
          {result.reason}
        </p>
      )}
      {result?.status === "READY" && <RiskView risk={result.risk} testId="risk-calc" />}
    </div>
  );
}
