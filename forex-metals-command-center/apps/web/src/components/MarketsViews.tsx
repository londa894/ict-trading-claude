"use client";

import type { MarketRow, ScanRow } from "@fmcc/shared-types";

import { ageLabel, progressLabel, type MarketsLoadState, type ScanLoadState } from "@/lib/scanner";

export type CenterView = "COMMAND_CENTER" | "MARKETS" | "WATCHLIST" | "SCANNER" | "ALERTS" | "JOURNAL" | "BACKTEST" | "REPLAY";

export function viewForHash(hash: string): CenterView {
  if (hash === "#markets") return "MARKETS";
  if (hash === "#watchlist") return "WATCHLIST";
  if (hash === "#scanner") return "SCANNER";
  if (hash === "#alerts") return "ALERTS";
  if (hash === "#journal" || hash === "#paper" || hash === "#analytics") return "JOURNAL";
  if (hash === "#backtest") return "BACKTEST";
  if (hash === "#replay") return "REPLAY";
  return "COMMAND_CENTER";
}

const DISCLAIMER =
  "Attention ranking, not a trade signal. Each row is that market's own Master Decision (verdict, blockers, risk).";

function ResearchBadge({ validated }: { validated: boolean }) {
  return validated ? (
    <span className="badge q-CURRENT">VALIDATED</span>
  ) : (
    <span className="badge q-STALE" title="Strategy parameters are validated on XAUUSD first">
      RESEARCH ONLY
    </span>
  );
}

export function ScanTable({
  rows,
  onOpen,
  testId,
}: {
  rows: ScanRow[];
  onOpen: (symbol: string) => void;
  testId: string;
}) {
  if (rows.length === 0) return <p className="muted">No market matches.</p>;
  return (
    <div className="table-scroll">
      <table className="scan-table" data-testid={testId}>
        <thead>
          <tr>
            <th>#</th>
            <th>Market</th>
            <th>Verdict</th>
            <th>Data</th>
            <th>HTF</th>
            <th>Setup</th>
            <th>Score</th>
            <th>Conf.</th>
            <th>Risk</th>
            <th>Blockers</th>
            <th>Next required event</th>
            <th>Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} data-testid={`${testId}-row-${r.symbol}`}>
              <td>{r.rank}</td>
              <td>
                <button type="button" className="link-button" onClick={() => onOpen(r.symbol)}>
                  {r.symbol}
                </button>{" "}
                <ResearchBadge validated={r.deeplyValidated} />
                <br />
                <span className="muted">{r.marketStatus}</span>
              </td>
              <td>
                <span className={`verdict verdict-${r.verdict}`}>{r.verdict}</span>
                {r.error && <div className="sev-ERROR">{r.error}</div>}
              </td>
              <td>{r.dataQuality}</td>
              <td>{r.htfBias}</td>
              <td>{progressLabel(r)}</td>
              <td>{r.setupScore === null ? "—" : `${r.setupScore} (${r.setupGrade})`}</td>
              <td>{r.decisionConfidence}</td>
              <td>{r.riskStatus}</td>
              <td title={r.blockers.join(", ")}>{r.blockers.length}</td>
              <td className="muted">{r.nextRequiredEvent ?? "—"}</td>
              <td className="muted">{ageLabel(r.cacheAgeSeconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScanStatus({ scan, busy }: { scan: ScanLoadState | null; busy: boolean }) {
  if (busy && !scan) return <p className="muted">Scanning… (a cold scan evaluates every market; allow ~15 s)</p>;
  if (scan?.status === "UNAVAILABLE") {
    return (
      <p className="not-available" role="alert" data-testid="scan-unavailable">
        <strong>SCAN UNAVAILABLE</strong>
        <br />
        {scan.reason}
      </p>
    );
  }
  if (scan?.status === "READY") {
    return (
      <p className="muted" data-testid="scan-meta">
        {scan.scan.rows.length} of {scan.scan.requestedSymbols.length} markets · {scan.scan.durationMs} ms · rows cached up to{" "}
        {scan.scan.cacheSeconds}s · authority {scan.scan.verdictAuthority}
        {busy ? " · refreshing…" : ""}
      </p>
    );
  }
  return null;
}

export function MarketsView({
  markets,
  watchlist,
  activeSymbol,
  onToggle,
  onOpen,
}: {
  markets: MarketsLoadState | null;
  watchlist: string[];
  activeSymbol: string;
  onToggle: (symbol: string) => void;
  onOpen: (symbol: string) => void;
}) {
  return (
    <section className="center-view" data-testid="markets-view">
      <h2>Markets</h2>
      <p className="muted">
        XAUUSD is the first deeply validated market. Other markets run the same engines but are research only and carry
        the MARKET_NOT_VALIDATED blocker.
      </p>
      {!markets ? (
        <p className="muted">Loading markets…</p>
      ) : markets.status === "UNAVAILABLE" ? (
        <p className="not-available" role="alert">
          <strong>MARKETS UNAVAILABLE</strong>
          <br />
          {markets.reason}
        </p>
      ) : (
        <div className="table-scroll">
          <table className="scan-table" data-testid="markets-table">
            <thead>
              <tr>
                <th>Market</th>
                <th>Class</th>
                <th>Validation</th>
                <th>Market hours</th>
                <th>Position size</th>
                <th>Watchlist</th>
              </tr>
            </thead>
            <tbody>
              {markets.markets.map((m: MarketRow) => (
                <tr key={m.symbol} className={m.symbol === activeSymbol ? "row-active" : undefined}>
                  <td>
                    <button type="button" className="link-button" onClick={() => onOpen(m.symbol)}>
                      {m.symbol}
                    </button>
                  </td>
                  <td>{m.assetClass}</td>
                  <td>
                    <ResearchBadge validated={m.deeplyValidated} />
                  </td>
                  <td>{m.marketStatus}</td>
                  <td>{m.positionSizeStatus}</td>
                  <td>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={watchlist.includes(m.symbol)}
                        onChange={() => onToggle(m.symbol)}
                        aria-label={`Watch ${m.symbol}`}
                      />
                      watch
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function WatchlistView({
  watchlist,
  scan,
  busy,
  onRefresh,
  onOpen,
}: {
  watchlist: string[];
  scan: ScanLoadState | null;
  busy: boolean;
  onRefresh: () => void;
  onOpen: (symbol: string) => void;
}) {
  return (
    <section className="center-view" data-testid="watchlist-view">
      <h2>Watchlist</h2>
      <p className="muted">{DISCLAIMER} The watchlist is saved in this browser only; edit it in Markets.</p>
      {watchlist.length === 0 ? (
        <p className="muted" data-testid="watchlist-empty">
          The watchlist is empty. Add markets from the Markets view.
        </p>
      ) : (
        <>
          <button type="button" className="tf" onClick={onRefresh} disabled={busy}>
            Refresh
          </button>
          <ScanStatus scan={scan} busy={busy} />
          {scan?.status === "READY" && <ScanTable rows={scan.scan.rows} onOpen={onOpen} testId="watchlist-table" />}
        </>
      )}
    </section>
  );
}

export type ScanFilters = { minScore: string; onlySetups: boolean };

export function ScannerView({
  scan,
  busy,
  filters,
  onFilters,
  onRefresh,
  onOpen,
}: {
  scan: ScanLoadState | null;
  busy: boolean;
  filters: ScanFilters;
  onFilters: (f: ScanFilters) => void;
  onRefresh: () => void;
  onOpen: (symbol: string) => void;
}) {
  return (
    <section className="center-view" data-testid="scanner-view">
      <h2>Scanner</h2>
      <p className="muted">{DISCLAIMER}</p>
      {scan?.status === "READY" && (
        <ol className="muted ranking-rules" data-testid="ranking-rules">
          {scan.scan.ranking.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ol>
      )}
      <div className="chart-toolbar">
        <label className="toggle">
          Min score
          <select
            className="tz-select"
            value={filters.minScore}
            onChange={(e) => onFilters({ ...filters, minScore: e.target.value })}
            aria-label="Minimum score"
          >
            <option value="">any</option>
            <option value="20">20+</option>
            <option value="40">40+</option>
            <option value="60">60+ (C)</option>
            <option value="80">80+ (A)</option>
          </select>
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.onlySetups}
            onChange={(e) => onFilters({ ...filters, onlySetups: e.target.checked })}
          />
          open setups only
        </label>
        <button type="button" className="tf" onClick={onRefresh} disabled={busy}>
          Scan
        </button>
      </div>
      <ScanStatus scan={scan} busy={busy} />
      {scan?.status === "READY" && <ScanTable rows={scan.scan.rows} onOpen={onOpen} testId="scanner-table" />}
    </section>
  );
}
