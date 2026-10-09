import { useAlerts } from "./AlertProvider";

/** Sound toggle and desktop-notification opt-in. Permission is only ever requested from this click. */
export function AlertControls() {
  const { state, setSound, requestNotifications } = useAlerts();
  return (
    <div className="alert-controls">
      <button type="button" className="ghost small-button" aria-pressed={state.sound}
              title={state.sound ? "Alert sound is on" : "Alert sound is off"}
              onClick={() => setSound(!state.sound)}>
        {state.sound ? "Sound on" : "Sound off"}
      </button>
      {state.notifications === "default" && (
        <button type="button" className="ghost small-button" onClick={requestNotifications}>
          Desktop alerts
        </button>
      )}
      {state.notifications === "denied" && (
        <span className="muted small" title="Allow notifications for this site in your browser settings to turn them on">
          Desktop alerts blocked
        </span>
      )}
    </div>
  );
}
