import type { CenterView } from "./MarketsViews";

/** Left navigation (spec STEP 11). Only surfaces backed by implemented engines are enabled. */
export const NAV_ITEMS: ReadonlyArray<{ label: string; availableInPhase: number | null; href?: string; view?: CenterView }> = [
  { label: "Command Center", availableInPhase: null, href: "#command-center", view: "COMMAND_CENTER" },
  { label: "Chart", availableInPhase: null, href: "#command-center", view: "COMMAND_CENTER" },
  { label: "Markets", availableInPhase: null, href: "#markets", view: "MARKETS" },
  { label: "Watchlist", availableInPhase: null, href: "#watchlist", view: "WATCHLIST" },
  { label: "Scanner", availableInPhase: null, href: "#scanner", view: "SCANNER" },
  { label: "Setups", availableInPhase: null, href: "#setup-progress" },
  { label: "Alerts", availableInPhase: null, href: "#alerts", view: "ALERTS" },
  { label: "Macro", availableInPhase: 14 },
  { label: "Risk", availableInPhase: null, href: "#risk" },
  { label: "Journal", availableInPhase: null, href: "#journal", view: "JOURNAL" },
  { label: "Backtest", availableInPhase: null, href: "#backtest", view: "BACKTEST" },
  { label: "Replay", availableInPhase: null, href: "#replay", view: "REPLAY" },
  { label: "Playbook", availableInPhase: 20 },
  { label: "Settings", availableInPhase: 20 },
];

export function LeftNav({ view = "COMMAND_CENTER" }: { view?: CenterView }) {
  return (
    <nav className="left-nav" aria-label="Main">
      <ul>
        {NAV_ITEMS.map(({ label, availableInPhase, href, view: target }) => (
          <li key={label}>
            {availableInPhase === null ? (
              <a href={href ?? "#"} aria-current={label !== "Chart" && target === view ? "page" : undefined}>
                {label}
              </a>
            ) : (
              <span className="nav-disabled" aria-disabled="true" title={`Available from Phase ${availableInPhase}`}>
                {label} <small>P{availableInPhase}</small>
              </span>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
