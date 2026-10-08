import { Link } from "react-router-dom";
import { clock, ruleName, statusLabel } from "../format";
import { useAlerts } from "./AlertProvider";

/** New enforce incidents, bottom-right on every page, until dismissed or no longer open. */
export function ToastStack() {
  const { state, dismiss, dismissAll } = useAlerts();
  return (
    <section className="toasts" aria-live="assertive" aria-relevant="additions" aria-label="New alerts">
      {state.toasts.length > 1 && (
        <button type="button" className="ghost toast-clear" onClick={dismissAll}>Dismiss all</button>
      )}
      {state.toasts.map(({ id, incident }) => (
        <article key={id} className={`toast sev-${incident.severity}`} data-testid="alert-toast">
          <div className="toast-head">
            <strong>{ruleName(incident.ruleId)}</strong>
            <span className="muted small num">{clock(incident.detectedAt)}</span>
          </div>
          <p className="toast-body">{incident.summary}</p>
          <div className="toast-actions">
            <Link to={`/incidents/${id}`} onClick={() => dismiss(id)}>Open incident</Link>
            <span className="muted small">{incident.storeId} · {statusLabel(incident.status)}</span>
            <button type="button" className="ghost" aria-label={`Dismiss alert: ${ruleName(incident.ruleId)}`}
                    onClick={() => dismiss(id)}>Dismiss</button>
          </div>
        </article>
      ))}
    </section>
  );
}
