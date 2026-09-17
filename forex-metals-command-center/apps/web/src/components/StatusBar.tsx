import type { DataReport, MasterDecision } from "@fmcc/shared-types";
import type { ReactNode } from "react";

import type { ChartState } from "@/lib/candles";
import { scoreLabel } from "@/lib/evaluation";
import { macroLabel, readMacroState } from "@/lib/macro";
import { newsLabel, readNewsState } from "@/lib/news";
import { clockLabel, dailyChangeLabel, type SessionLoadState } from "@/lib/sessions";

function Item({ label, value, testId }: { label: string; value: ReactNode; testId?: string }) {
  return (
    <div className="item">
      <span className="label">{label}</span>
      <span className="value" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}

const NA = (phase: string) => <span className="muted">N/A · {phase}</span>;

/** Top status bar. Every value comes from the Master Decision or validated chart series; nothing is invented. */
export function StatusBar({
  decision,
  data,
  chart,
  sessions = null,
}: {
  decision: MasterDecision;
  data: DataReport | null;
  chart: ChartState | null;
  sessions?: SessionLoadState | null;
}) {
  const ready = sessions?.status === "READY" ? sessions.analysis : null;
  const change = ready ? dailyChangeLabel(ready) : null;
  const last = chart?.status === "READY" ? chart.candles[chart.candles.length - 1] : undefined;
  return (
    <header className="statusbar" aria-label="Top status bar">
      <Item label="Symbol" value={decision.symbol} />
      <Item
        label={chart ? `Last close (${chart.timeframe})` : "Last close"}
        value={last ? String(last.close) : <span className="muted">UNAVAILABLE</span>}
        testId="last-close"
      />
      <Item
        label="Daily change (M15)"
        value={change ?? <span className="muted">UNAVAILABLE</span>}
        testId="daily-change"
      />
      <Item label="Spread" value={NA("no quote feed")} />
      <Item
        label="Session (NY)"
        value={ready ? clockLabel(ready) : <span className="muted">UNAVAILABLE</span>}
        testId="session"
      />
      <Item label="HTF bias" value={decision.htfBias} />
      <Item
        label="Verdict"
        value={
          <span className={`verdict verdict-${decision.verdict}`} data-testid="verdict">
            {decision.verdict}
          </span>
        }
      />
      <Item
        label="Score"
        value={scoreLabel(decision.setupScore ?? null, decision.setupGrade ?? null) ?? <span className="muted">—</span>}
        testId="score"
      />
      <Item label="Confidence" value={decision.decisionConfidence} />
      <Item label="News" value={newsLabel(readNewsState(decision))} testId="news" />
      <Item label="Macro" value={macroLabel(readMacroState(decision))} testId="macro" />
      <Item label="Risk" value={decision.riskStatus} />
      <Item label={`Data quality (${data?.timeframe ?? "M5"})`} value={decision.dataQuality} />
      <Item label="Market" value={data?.marketStatus ?? "UNKNOWN"} />
    </header>
  );
}
