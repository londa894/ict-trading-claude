"use client";

import type { DataReport, MasterDecision, MtfStructureResponse, SystemStatus } from "@fmcc/shared-types";
import { useEffect, useState } from "react";

import type { LiquidityLoadState } from "@/lib/liquidity";
import { formatNoWickState, readNoWickState, type NoWickLoadState } from "@/lib/noWick";
import { formatSessionState, readSessionState, type SessionLoadState } from "@/lib/sessions";
import type { EvaluationLoadState } from "@/lib/evaluation";
import type { MacroLoadState } from "@/lib/macro";
import type { NewsLoadState } from "@/lib/news";
import type { SetupLoadState } from "@/lib/setups";
import type { PdArrayLoadState } from "@/lib/pdArrays";
import type { StructureLoadState } from "@/lib/structure";

import { LiquidityTab } from "./LiquidityTab";
import { NoWickTab } from "./NoWickTab";
import { PdArraysTab } from "./PdArraysTab";
import { AiAnalysisTab } from "./AiAnalysisTab";
import { EntryTab } from "./EntryTab";
import { MacroSection } from "./MacroSection";
import { NewsTab } from "./NewsTab";
import { RiskTab } from "./RiskTab";
import { SessionTab } from "./SessionTab";
import { SetupPanel } from "./SetupPanel";

import { StructureTab } from "./StructureTab";

export const PANEL_TABS: ReadonlyArray<{ id: string; availableInPhase: number | null }> = [
  { id: "OVERVIEW", availableInPhase: null },
  { id: "STRUCTURE", availableInPhase: null },
  { id: "LIQUIDITY", availableInPhase: null },
  { id: "PD_ARRAYS", availableInPhase: null },
  { id: "NO_WICK", availableInPhase: null },
  { id: "SESSION", availableInPhase: null },
  { id: "MACRO", availableInPhase: null },
  { id: "ENTRY", availableInPhase: null },
  { id: "RISK", availableInPhase: null },
  { id: "AI_ANALYSIS", availableInPhase: null },
];

type Props = {
  decision: MasterDecision;
  data: DataReport | null;
  status: SystemStatus | null;
  trusted: boolean;
  structure?: StructureLoadState | null;
  alignment?: MtfStructureResponse | null;
  liquidity?: LiquidityLoadState | null;
  pdArrays?: PdArrayLoadState | null;
  noWick?: NoWickLoadState | null;
  sessions?: SessionLoadState | null;
  setups?: SetupLoadState | null;
  evaluation?: EvaluationLoadState | null;
  news?: NewsLoadState | null;
  macro?: MacroLoadState | null;
};

export function IntelligencePanel({
  decision,
  data,
  status,
  trusted,
  structure = null,
  alignment = null,
  liquidity = null,
  pdArrays = null,
  noWick = null,
  sessions = null,
  setups = null,
  evaluation = null,
  news = null,
  macro = null,
}: Props) {
  const [tab, setTab] = useState("OVERVIEW");
  useEffect(() => {
    // Left-nav deep link: #risk opens the RISK tab.
    const sync = () => {
      if (window.location.hash === "#risk") setTab("RISK");
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const current = PANEL_TABS.find((t) => t.id === tab) ?? PANEL_TABS[0]!;
  const noWickState = readNoWickState(decision);
  const noWickText = noWickState ? formatNoWickState(noWickState) : "—";
  const sessionState = readSessionState(decision);
  const sessionText = sessionState ? formatSessionState(sessionState) : "—";

  return (
    <aside className="intel-panel" id="risk" aria-label="Intelligence panel">
      <div role="tablist" className="tabs">
        {PANEL_TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={t.id === tab}
            className={t.id === tab ? "tab active" : t.availableInPhase === null ? "tab" : "tab tab-pending"}
            onClick={() => setTab(t.id)}
          >
            {t.id}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="tab-body">
        {current.id === "STRUCTURE" ? (
          <StructureTab structure={structure} alignment={alignment} decision={decision} />
        ) : current.id === "PD_ARRAYS" ? (
          <PdArraysTab pdArrays={pdArrays} decision={decision} />
        ) : current.id === "MACRO" ? (
          <>
            <MacroSection macro={macro} />
            <NewsTab news={news} />
          </>
        ) : current.id === "AI_ANALYSIS" ? (
          <AiAnalysisTab symbol={decision.symbol} />
        ) : current.id === "RISK" ? (
          <RiskTab symbol={decision.symbol} evaluation={evaluation} />
        ) : current.id === "ENTRY" ? (
          <EntryTab evaluation={evaluation} />
        ) : current.id === "SESSION" ? (
          <SessionTab sessions={sessions} decision={decision} />
        ) : current.id === "NO_WICK" ? (
          <NoWickTab noWick={noWick} decision={decision} />
        ) : current.id === "LIQUIDITY" ? (
          <LiquidityTab liquidity={liquidity} decision={decision} />
        ) : current.availableInPhase !== null ? (
          <p className="not-available" data-testid="tab-not-available">
            <strong>NOT AVAILABLE</strong>
            <br />
            The {current.id} engine is scheduled for Phase {current.availableInPhase}. No values are shown until its
            deterministic outputs exist.
          </p>
        ) : (
          <>
            <h2>Why not yet</h2>
            <ul data-testid="blockers">
              {decision.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
            <p>
              <span className="label">Next required event</span>
              <br />
              {decision.nextRequiredEvent ?? "—"}
            </p>
            <SetupPanel setups={setups} />
            <h2>Decision</h2>
            <dl>
              <dt>Verdict</dt>
              <dd>{decision.verdict}</dd>
              <dt>Primary DOL</dt>
              <dd>{decision.primaryDol ?? "—"}</dd>
              <dt>Liquidity event</dt>
              <dd>{decision.liquidityEvent ?? "—"}</dd>
              <dt>Displacement</dt>
              <dd>{decision.displacement ?? "—"}</dd>
              <dt>No wick (context only)</dt>
              <dd>{noWickText}</dd>
              <dt>Session (context only)</dt>
              <dd>{sessionText}</dd>
              <dt>Setup state</dt>
              <dd>
                {decision.setupState}
                {decision.setupType ? ` (${decision.setupType})` : ""}
              </dd>
              <dt>Strategy</dt>
              <dd>{decision.strategyVersion}</dd>
              <dt>Payload trusted</dt>
              <dd>{trusted ? "yes" : "no"}</dd>
            </dl>
            <h2>Data (execution timeframe)</h2>
            <dl>
              <dt>Provider</dt>
              <dd>{data?.provider ?? "unreachable"}</dd>
              <dt>Last closed bar</dt>
              <dd>{data?.latestClosedOpenTime ?? "—"}</dd>
              <dt>Position size</dt>
              <dd>{data?.positionSizeStatus ?? "UNKNOWN"}</dd>
            </dl>
            <h2>System</h2>
            <dl>
              <dt>Phase</dt>
              <dd>{status ? `${status.phase} — ${status.phaseName}` : "UNKNOWN"}</dd>
              <dt>Verdict authority</dt>
              <dd>{status?.verdictAuthority ?? "UNKNOWN"}</dd>
              <dt>Broker / execution</dt>
              <dd>NONE / NONE</dd>
            </dl>
          </>
        )}
      </div>
    </aside>
  );
}
