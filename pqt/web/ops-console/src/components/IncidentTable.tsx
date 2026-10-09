import { Link } from "react-router-dom";
import { dateTime, ruleName, statusLabel } from "../format";
import type { Incident } from "../types";

export function IncidentTable({ incidents, empty }: { incidents: Incident[]; empty: string }) {
  if (incidents.length === 0) return <p className="empty">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Incident</th>
            <th scope="col">Severity</th>
            <th scope="col">Status</th>
            <th scope="col">Detected</th>
            <th scope="col">Store</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.incidentId}>
              <td>
                <Link to={`/incidents/${i.incidentId}`} className="row-link">{ruleName(i.ruleId)}</Link>
                <div className="muted small">{i.summary}</div>
              </td>
              <td><span className={`sev sev-${i.severity}`}>{i.severity}</span></td>
              <td>{statusLabel(i.status)}</td>
              <td className="num">{dateTime(i.detectedAt)}</td>
              <td>{i.storeId}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
