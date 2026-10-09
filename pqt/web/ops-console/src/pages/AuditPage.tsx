import { useEffect, useState } from "react";
import { get } from "../api";
import { dateTime } from "../format";

interface Row {
  seq: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  details: string;
  row_hash: string;
}

export function AuditPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [verdict, setVerdict] = useState<{ valid: boolean; rows: number; brokenAtSeq?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<Row[]>("/api/admin/audit?limit=200").then(setRows).catch((e: Error) => setError(e.message));
  }, []);
  return (
    <main className="page">
      <h1>Audit log</h1>
      <p className="intro">Every review decision and rejected duplicate is recorded here. Entries are chained by hash, so any edit to past entries breaks the chain.</p>
      <div className="buttons">
        <button type="button" onClick={() => get<typeof verdict>("/api/admin/audit/verify").then(setVerdict).catch((e: Error) => setError(e.message))}>
          Check the chain
        </button>
        {verdict && (verdict.valid
          ? <span className="notice">Chain intact across {verdict.rows} entries.</span>
          : <span className="error">Chain broken at entry {verdict.brokenAtSeq}. Escalate to security (runbook RB-08).</span>)}
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead><tr><th scope="col">#</th><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Target</th><th scope="col">Hash</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.seq}>
                <td className="num">{r.seq}</td>
                <td className="num">{dateTime(r.at)}</td>
                <td>{r.actor}</td>
                <td>{r.action}</td>
                <td className="small">{r.target}</td>
                <td className="small hash">{r.row_hash.slice(0, 12)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
