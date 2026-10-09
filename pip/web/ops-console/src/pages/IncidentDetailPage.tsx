import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, request } from "../api";
import { dateTime, duration, ruleName, statusLabel } from "../format";
import { IncidentTimeline } from "../components/IncidentTimeline";
import { hasRole, useMe } from "../session";
import type { Incident, Review } from "../types";

const REASONS: { code: string; label: string }[] = [
  { code: "FALSE_POSITIVE", label: "False alarm" },
  { code: "DUPLICATE", label: "Duplicate of another incident" },
  { code: "EXPECTED_BEHAVIOUR", label: "Expected, nothing to do" },
  { code: "TEST_EVENT", label: "Test or drill" },
  { code: "OTHER", label: "Other (explain in a note)" },
];

export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const me = useMe();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [etag, setEtag] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("FALSE_POSITIVE");
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const r = await request<{ incident: Incident; reviews: Review[] }>(`/api/incidents/${id}`);
    setIncident(r.data.incident);
    setReviews(r.data.reviews);
    setEtag(r.etag);
  }, [id]);

  useEffect(() => {
    load().catch((e: Error) => setError(e instanceof ApiError && e.status === 404 ? "This incident does not exist or you cannot see it." : e.message));
  }, [load]);

  async function act(action: "ack" | "confirm" | "dismiss" | "close") {
    if (!etag) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body = action === "dismiss" ? { action, reasonCode: reason, note: note.trim() || null } : { action };
      await request<Incident>(`/api/incidents/${id}/actions`, {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "If-Match": etag },
      });
      setDismissing(false);
      setNote("");
      await load();
      setNotice(action === "ack" ? "Acknowledged." : action === "confirm" ? "Confirmed." : action === "dismiss" ? "Dismissed." : "Closed.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) {
        await load();
        setError("Someone else updated this incident. The latest version is shown; check it and try again.");
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  if (!incident) {
    return <main className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</main>;
  }

  const s = incident.status;
  const shadow = incident.mode === "shadow";
  const canAck = !shadow && hasRole(me, "operator") && s === "OPEN";
  const canReview = !shadow && hasRole(me, "reviewer") && (s === "OPEN" || s === "ACKNOWLEDGED" || s === "AUTO_RESOLVED");
  const canClose = !shadow && hasRole(me, "reviewer") && s === "CONFIRMED";

  return (
    <main className="page detail">
      <p><Link to="/review">Back to review queue</Link></p>
      <div className={`detail-head sev-${incident.severity}`}>
        <h1>{ruleName(incident.ruleId)}</h1>
        <p className="lede">{incident.summary}</p>
      </div>
      <dl className="facts">
        <div><dt>Status</dt><dd>{statusLabel(s)}{shadow && " (shadow rule, read-only)"}</dd></div>
        <div><dt>Severity</dt><dd><span className={`sev sev-${incident.severity}`}>{incident.severity}</span></dd></div>
        <div><dt>Started</dt><dd className="num">{dateTime(incident.onsetAt)}</dd></div>
        <div><dt>Detected</dt><dd className="num">{dateTime(incident.detectedAt)}</dd></div>
        <div><dt>Cleared</dt><dd className="num">{incident.resolvedAt ? `${dateTime(incident.resolvedAt)} (after ${duration(incident.onsetAt, incident.resolvedAt)})` : "Still ongoing"}</dd></div>
        <div><dt>Store</dt><dd>{incident.storeId}</dd></div>
        <div><dt>Rule</dt><dd>{incident.ruleId} v{incident.ruleVersion}</dd></div>
        <div><dt>Evidence</dt><dd className="small">{incident.evidence.length ? incident.evidence.join(", ") : "–"}</dd></div>
      </dl>

      <IncidentTimeline incidentId={incident.incidentId} version={incident.version} />

      {(canAck || canReview || canClose) && (
        <section className="actions" aria-label="Review actions">
          <h2>Your decision</h2>
          <p className="muted small">A person makes every call here. Decisions are recorded in the audit log under your name.</p>
          <div className="buttons">
            {canAck && <button type="button" disabled={busy} onClick={() => void act("ack")}>Acknowledge</button>}
            {canReview && <button type="button" className="primary" disabled={busy} onClick={() => void act("confirm")}>Confirm incident</button>}
            {canReview && !dismissing && <button type="button" disabled={busy} onClick={() => setDismissing(true)}>Dismiss…</button>}
            {canClose && <button type="button" className="primary" disabled={busy} onClick={() => void act("close")}>Close incident</button>}
          </div>
          {dismissing && (
            <form className="dismiss" onSubmit={(e) => { e.preventDefault(); void act("dismiss"); }}>
              <label>
                Reason
                <select value={reason} onChange={(e) => setReason(e.target.value)}>
                  {REASONS.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
                </select>
              </label>
              <label>
                Note {reason === "OTHER" ? "(required)" : "(optional)"}
                <textarea maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
              </label>
              <div className="buttons">
                <button type="submit" className="danger" disabled={busy || (reason === "OTHER" && !note.trim())}>Dismiss incident</button>
                <button type="button" className="ghost" onClick={() => setDismissing(false)}>Cancel</button>
              </div>
            </form>
          )}
        </section>
      )}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <section>
        <h2>History</h2>
        {reviews.length === 0 ? <p className="empty">No one has reviewed this incident yet.</p> : (
          <ol className="history">
            {reviews.map((r, i) => (
              <li key={i}>
                <span className="num">{dateTime(r.at)}</span> {r.actor}: {statusLabel(r.from)} to {statusLabel(r.to)}
                {r.reasonCode && <> ({REASONS.find((x) => x.code === r.reasonCode)?.label ?? r.reasonCode})</>}
                {r.note && <div className="muted small">{r.note}</div>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}
