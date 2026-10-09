import { floorRows } from "../floor";
import type { Incident, Layout, StoreState } from "../types";

const ALERT_LABEL = { low: "Low alert", medium: "Alert", high: "High alert" } as const;

/** Milestone C3: the same information as the floor map, as tables, for keyboard and screen-reader users. */
export function FloorTable({ layout, state, incidents }: { layout: Layout; state: StoreState; incidents: Incident[] }) {
  const { zones, queues } = floorRows(layout, state, incidents);
  return (
    <details className="floor-table">
      <summary>Show the floor as a table</summary>
      <table>
        <caption>Checkout queues</caption>
        <thead>
          <tr><th scope="col">Queue</th><th scope="col">Waiting</th><th scope="col">Open registers</th><th scope="col">Status</th></tr>
        </thead>
        <tbody>
          {queues.map((q) => (
            <tr key={q.id}>
              <th scope="row">{q.zoneName} <span className="muted small">({q.id})</span></th>
              <td className="num">{q.waiting}</td>
              <td className="num">{q.openRegisters} of {q.registers}</td>
              <td>{q.alert ? ALERT_LABEL[q.alert] : "OK"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <caption>Zones</caption>
        <thead>
          <tr><th scope="col">Zone</th><th scope="col">People now</th><th scope="col">Status</th></tr>
        </thead>
        <tbody>
          {zones.map((z) => (
            <tr key={z.id}>
              <th scope="row">{z.name}</th>
              <td className="num">{z.people}</td>
              <td>{z.alert ? ALERT_LABEL[z.alert] : "OK"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
