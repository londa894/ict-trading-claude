import type { EventView } from "@fmcc/shared-types";

import type { NewsLoadState } from "@/lib/news";

const time = (iso: string | null) => (iso ? `${iso.replace("T", " ").slice(0, 16)}Z` : "—");
const value = (v: EventView["actual"]) => (v === null || v === undefined ? "—" : String(v));

/** MACRO tab (news part, Phase 13): the news gate and the relevant economic calendar. */
export function NewsTab({ news }: { news: NewsLoadState | null }) {
  if (!news) return <p className="muted">Loading news…</p>;
  if (news.status === "UNAVAILABLE") {
    return (
      <p className="not-available" role="alert" data-testid="news-unavailable">
        <strong>NEWS UNAVAILABLE</strong>
        <br />
        {news.reason}
      </p>
    );
  }
  const n = news.news;
  return (
    <div data-testid="news-tab">
      <p className="muted"><strong>News gate</strong></p>
      <dl>
        <dt>News state</dt>
        <dd className={n.blockers.length ? "sev-ERROR" : undefined} data-testid="news-state">
          {n.state}
        </dd>
        <dt>Relevant currencies</dt>
        <dd>{n.relevantCurrencies.join(", ")}</dd>
        <dt>Window</dt>
        <dd>
          {n.windowStart ? `${time(n.windowStart)} → ${time(n.windowEnd)}` : "—"}
          {n.activeEvent ? ` · ${n.activeEvent.importance} ${n.activeEvent.currency} ${n.activeEvent.name}` : ""}
        </dd>
        <dt>Next event</dt>
        <dd data-testid="news-next">
          {n.nextEvent
            ? `${n.nextEvent.importance} ${n.nextEvent.currency} ${n.nextEvent.name} at ${time(n.nextEvent.scheduledTime)} (${Math.round(n.nextEvent.minutesToEvent)} min)`
            : "none in the calendar"}
        </dd>
        <dt>Calendar</dt>
        <dd data-testid="news-calendar">
          {n.calendar.provider}
          {n.calendar.isSynthetic ? " · SYNTHETIC" : ""}
          {n.calendar.available ? ` · fetched ${time(n.calendar.fetchedAt)} · covers to ${time(n.calendar.coverageEnd)}` : ` · ${n.calendar.reason}`}
        </dd>
      </dl>
      {n.blockers.length > 0 && <p className="sev-ERROR">Blockers: {n.blockers.join(", ")}</p>}
      {n.warnings.length > 0 && <p className="muted">{n.warnings.join(" · ")}</p>}
      {n.events.length > 0 && (
        <div className="table-scroll">
          <table className="scan-table" data-testid="news-events">
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Imp.</th>
                <th>Event</th>
                <th>Status</th>
                <th>Act / Fcst / Prev</th>
                <th>Surprise</th>
              </tr>
            </thead>
            <tbody>
              {n.events.map((e) => (
                <tr key={e.id}>
                  <td>{time(e.scheduledTime)}</td>
                  <td className={e.importance === "EXTREME" ? "sev-ERROR" : e.importance === "HIGH" ? "sev-WARNING" : "muted"}>{e.importance}</td>
                  <td>
                    {e.currency} {e.name}
                  </td>
                  <td>{e.status}</td>
                  <td>
                    {value(e.actual)} / {value(e.forecast)} / {value(e.previous)}
                  </td>
                  <td>{e.surprise === null ? "—" : `${e.surprise}${e.surprisePct !== null ? ` (${e.surprisePct}%)` : ""}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">Surprise is actual − forecast only; the macro engine maps released HIGH+ USD surprises to a USD move.</p>
    </div>
  );
}
