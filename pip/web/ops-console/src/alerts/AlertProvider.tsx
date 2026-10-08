import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ruleName } from "../format";
import { useLive } from "../live";
import { alertReducer, initialAlertState, pulsingIds, type AlertState, type NotificationState } from "./alertReducer";

const SOUND_KEY = "pip.alerts.sound";

interface AlertApi {
  state: AlertState;
  pulsing: Set<string>;
  dismiss: (id: string) => void;
  dismissAll: () => void;
  setSound: (on: boolean) => void;
  requestNotifications: () => void;
}

const AlertContext = createContext<AlertApi | null>(null);

function readSoundPref(): boolean {
  try {
    return window.localStorage.getItem(SOUND_KEY) === "on";
  } catch {
    return false;
  }
}

function writeSoundPref(on: boolean): void {
  try {
    window.localStorage.setItem(SOUND_KEY, on ? "on" : "off");
  } catch {
    // storage blocked (private window, policy): the choice lasts for this tab only
  }
}

function currentPermission(): NotificationState {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission as NotificationState;
}

/** Two short tones. Needs an AudioContext created from a user gesture (the sound toggle). */
function chime(ctx: AudioContext): void {
  const start = ctx.currentTime;
  [880, 660].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const t = start + i * 0.22;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.2);
  });
}

/** Turns live "incident opened" changes into toasts, zone pulses, an optional chime and an optional desktop notification. */
export function AlertProvider({ children }: { children: ReactNode }) {
  const live = useLive();
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(alertReducer, undefined, () =>
    initialAlertState({ sound: readSoundPref(), notifications: currentPermission() }));
  const [now, setNow] = useState(() => Date.now());
  const audio = useRef<AudioContext | null>(null);

  useEffect(() => live.onIncident(({ change, incident }) =>
    dispatch({ type: "incident", change, incident, now: Date.now() })), [live]);

  // Pulses expire in wall-clock time; tick only while something is pulsing.
  const anyPulse = Object.keys(state.pulsing).length > 0;
  useEffect(() => {
    if (!anyPulse) return;
    const t = window.setInterval(() => {
      const ts = Date.now();
      setNow(ts);
      dispatch({ type: "tick", now: ts });
    }, 1_000);
    return () => window.clearInterval(t);
  }, [anyPulse]);

  // One chime and one desktop notification per new alert.
  const handled = useRef(0);
  useEffect(() => {
    if (state.alertSeq === handled.current) return;
    handled.current = state.alertSeq;
    setNow(Date.now());
    const inc = state.lastAlert;
    if (!inc) return;
    if (state.sound && audio.current) {
      void audio.current.resume().then(() => audio.current && chime(audio.current)).catch(() => undefined);
    }
    if (state.notifications === "granted" && document.visibilityState !== "visible") {
      try {
        const n = new Notification(ruleName(inc.ruleId), { body: inc.summary, tag: inc.incidentId });
        n.onclick = () => {
          window.focus();
          navigate(`/incidents/${inc.incidentId}`);
          n.close();
        };
      } catch {
        // some browsers only allow notifications from a service worker; the toast still shows
      }
    }
  }, [state.alertSeq, state.lastAlert, state.sound, state.notifications, navigate]);

  // Sound remembered as "on" from an earlier visit: browsers only allow audio after a user
  // gesture, so create the AudioContext on the first click or key press.
  useEffect(() => {
    if (!state.sound || audio.current || typeof window.AudioContext !== "function") return;
    const unlock = () => {
      if (!audio.current) audio.current = new window.AudioContext();
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, [state.sound]);

  const setSound = useCallback((on: boolean) => {
    if (on && !audio.current && typeof window.AudioContext === "function") {
      audio.current = new window.AudioContext(); // created inside the click handler, so browsers allow playback
    }
    writeSoundPref(on);
    dispatch({ type: "sound", on });
  }, []);

  const requestNotifications = useCallback(() => {
    if (!("Notification" in window)) return;
    void Notification.requestPermission().then((p) => dispatch({ type: "permission", value: p as NotificationState }));
  }, []);

  const api = useMemo<AlertApi>(() => ({
    state,
    pulsing: pulsingIds(state, now),
    dismiss: (id) => dispatch({ type: "dismiss", id }),
    dismissAll: () => dispatch({ type: "dismissAll" }),
    setSound,
    requestNotifications,
  }), [state, now, setSound, requestNotifications]);

  return <AlertContext.Provider value={api}>{children}</AlertContext.Provider>;
}

export function useAlerts(): AlertApi {
  const api = useContext(AlertContext);
  if (!api) throw new Error("useAlerts outside AlertProvider");
  return api;
}
