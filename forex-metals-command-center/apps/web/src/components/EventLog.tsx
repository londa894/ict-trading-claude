export type EventEntry = {
  key: string;
  at: string;
  severity: "INFO" | "WARNING" | "ERROR";
  text: string;
};

/** Bottom event area: decision and chart events of this page view plus alerts from the server-side monitor. */
export function EventLog({ entries }: { entries: EventEntry[] }) {
  return (
    <section className="event-log" aria-label="Events">
      <h2>
        Events <small className="muted">decisions, data issues and monitor alerts (newest first)</small>
      </h2>
      {entries.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : (
        <ol reversed>
          {entries.map((e) => (
            <li key={e.key} className={`sev-${e.severity}`}>
              <time dateTime={e.at}>{e.at.replace("T", " ").slice(0, 19)}Z</time> {e.text}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const MAX_EVENTS = 200;

/** Newest first, deduplicated by key, bounded. */
export function appendEvents(existing: EventEntry[], incoming: EventEntry[]): EventEntry[] {
  const known = new Set(existing.map((e) => e.key));
  const fresh = incoming.filter((e) => !known.has(e.key));
  return fresh.length === 0 ? existing : [...fresh.reverse(), ...existing].slice(0, MAX_EVENTS);
}
