import { useEffect, useState } from "react";
import { get } from "../api";
import { dateTime, ruleName } from "../format";
import { rateWithInterval, reasons, sampleNote, seconds } from "../metrics";
import type { ReviewMetrics } from "../types";

const PERIODS = [7, 30, 90] as const;

/** Milestone C5: how fast people act on alerts and how often they agree with each rule. Admins only. */
export function ReviewMetricsPage() {
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<ReviewMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    get<ReviewMetrics>(`/api/admin/review-metrics?days=${days}`)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [days]);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Review metrics</h1>
        <label className="small">
          Period{" "}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((p) => <option key={p} value={p}>Last {p} days</option>)}
          </select>
        </label>
      </div>
      <p className="intro">
        How quickly people act on alerts, and how often they agree with each rule. The confirm rate is the share of decided
        incidents that were confirmed; the range is a 95% interval, and its lower end is the cautious precision estimate to
        report for a real store.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.rules.length === 0 && <p className="empty">No alerting incidents in this period.</p>}
      {data && data.rules.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="small muted">Enforce rules, incidents created in the last {data.days} days. Generated {dateTime(data.generatedAt)}.</caption>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Incidents</th>
                <th scope="col">Waiting for a decision</th>
                <th scope="col">Time to first action (median / 90th pct)</th>
                <th scope="col">Confirm rate (95% range)</th>
                <th scope="col">Dismissed because</th>
              </tr>
            </thead>
            <tbody>
              {data.rules.map((r) => {
                const note = sampleNote(r);
                return (
                  <tr key={r.ruleId}>
                    <th scope="row">{ruleName(r.ruleId)}<div className="small muted">{r.ruleId}</div></th>
                    <td className="num">{r.incidents}</td>
                    <td className="num">{r.undecided}</td>
                    <td className="num">{seconds(r.timeToActionP50Seconds)} / {seconds(r.timeToActionP90Seconds)}</td>
                    <td className="num">
                      {rateWithInterval(r)}
                      <div className="small muted">{r.confirmed} confirmed, {r.dismissed} dismissed</div>
                      {note && <div className="small muted">{note}</div>}
                    </td>
                    <td className="small">{reasons(r.dismissReasons)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
