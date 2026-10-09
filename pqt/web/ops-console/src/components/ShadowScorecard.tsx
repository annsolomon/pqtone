import { useEffect, useState } from "react";
import { get } from "../api";
import { dateTime, ruleName } from "../format";
import { ordered, scoreCell, verdict } from "../scorecard";
import type { RuleScores } from "../types";

/**
 * Milestone R6: how each rule performs on live (e2e) runs of its current version, and whether a shadow
 * rule has earned a promotion proposal. Deliberately no button: promotion is a reviewed pull request.
 */
export function ShadowScorecard() {
  const [data, setData] = useState<RuleScores | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<RuleScores>("/api/admin/rule-scores").then(setData).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section className="scorecard" aria-labelledby="scorecard-h">
      <h2 id="scorecard-h">Live scores</h2>
      <p className="muted small">
        Precision and recall on end-to-end runs of each rule's current version, accumulated over the last
        {data ? ` ${data.windowDays}` : ""} days. The number after ≥ is the 95% lower bound.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {!data && !error && <p className="muted">Loading scores…</p>}
      {data && data.rules.length === 0 && <p className="empty">No end-to-end runs have been scored yet.</p>}
      {data && data.rules.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Runs</th>
                <th scope="col">Precision</th>
                <th scope="col">Recall</th>
                <th scope="col">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {ordered(data.rules).map((r) => {
                const v = verdict(r);
                return (
                  <tr key={r.ruleId}>
                    <th scope="row">
                      {ruleName(r.ruleId)}
                      <div className="small muted">{r.ruleId} v{r.ruleVersion} · {r.mode} · last run {dateTime(r.lastMeasuredAt)}</div>
                    </th>
                    <td className="num">{r.runs}<div className="small muted">{r.tp} hit, {r.fp} false, {r.fn} missed</div></td>
                    <td className="num">{scoreCell(r.precision, r.precisionLow, r.precisionMin)}</td>
                    <td className="num">{scoreCell(r.recall, r.recallLow, r.recallMin)}</td>
                    <td><span className={`verdict verdict-${v.tone}`}>{v.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <details className="promote-how">
        <summary>How to promote a shadow rule</summary>
        <ol className="small">
          <li>Wait for “Ready to propose for enforce”. It needs enough samples, not only a perfect score.</li>
          <li>Open a pull request with the <code>rule-promotion</code> template: set <code>mode: enforce</code> in
            <code> config/rules.yaml</code> and raise the rule's version (CI refuses an unchanged version).</li>
          <li>Paste this scorecard's numbers into the template. A reviewer approves; CI must be green.</li>
          <li>After merge, the change reaches running engines by deployment or by <code>make rules-publish</code>.</li>
        </ol>
        <p className="small muted">Runbook: docs/runbooks/RB-11-promote-shadow-rule.md. There is no switch in the console on purpose.</p>
      </details>
    </section>
  );
}
