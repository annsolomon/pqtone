import { clock, describeEvent } from "../format";
import type { LiveEvent } from "../types";

export function EventTicker({ events, skipped }: { events: LiveEvent[]; skipped: number }) {
  return (
    <section className="ticker" aria-label="Live events">
      <div className="section-head">
        <h2>Live events</h2>
        {skipped > 0 && <span className="muted">{skipped.toLocaleString()} more not shown at this speed</span>}
      </div>
      {events.length === 0 ? (
        <p className="empty">No events yet. Start a simulation with <code>make sim</code> to watch the floor fill up.</p>
      ) : (
        <ol className="ticker-list">
          {events.map((e) => (
            <li key={e.id}>
              <time>{clock(e.time)}</time>
              <span className={`tag t-${e.type.split(".")[0]}`}>{e.type.split(".")[0]}</span>
              <span>{describeEvent(e.type, e.data)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
