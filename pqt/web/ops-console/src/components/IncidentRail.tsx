import { Link } from "react-router-dom";
import { clock, ruleName, statusLabel } from "../format";
import type { Incident } from "../types";

export function IncidentRail({ incidents }: { incidents: Incident[] }) {
  return (
    <aside className="rail" aria-label="Open incidents">
      <div className="section-head">
        <h2>Open incidents</h2>
        <span className="count">{incidents.length}</span>
      </div>
      {incidents.length === 0 ? (
        <p className="empty">Nothing needs attention on this floor.</p>
      ) : (
        <ul className="rail-list">
          {incidents.map((i) => (
            <li key={i.incidentId} className={`sev-${i.severity}`}>
              <Link to={`/incidents/${i.incidentId}`}>
                <span className="rail-title">{ruleName(i.ruleId)}</span>
                <span className="rail-summary">{i.summary}</span>
                <span className="rail-meta">
                  {statusLabel(i.status)} at {clock(i.detectedAt)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
