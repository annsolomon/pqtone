import type { Incident } from "../types";

/**
 * Alert state for the console: which new incidents are shown as toasts, which floor zones
 * are pulsing, and the operator's sound and desktop-notification choices.
 *
 * Pure and framework-free, so every rule below is unit-tested (alertReducer.test.ts):
 *  - only enforce-mode incidents alert; shadow rules never notify anyone (ARCHITECTURE §8.5);
 *  - each incident alerts once, however often the live stream redelivers it;
 *  - a toast stays until someone dismisses it or the incident stops being open;
 *  - wall-clock time is passed in (`now`), never read here.
 */

export const MAX_TOASTS = 5;
export const MAX_SEEN = 500;
export const PULSE_MS = 12_000;

export type NotificationState = "default" | "granted" | "denied" | "unsupported";

export interface AlertToast {
  id: string;
  incident: Incident;
  raisedAt: number;
}

export interface AlertState {
  /** Newest first, at most MAX_TOASTS. */
  toasts: AlertToast[];
  /** Incident ids that have already alerted, oldest first, at most MAX_SEEN. */
  seen: string[];
  /** incidentId -> wall-clock ms until which its zone outline pulses. */
  pulsing: Record<string, number>;
  sound: boolean;
  notifications: NotificationState;
  /** Increments once per new alert; effects (sound, desktop notification) key off it. */
  alertSeq: number;
  lastAlert: Incident | null;
}

export type AlertAction =
  | { type: "incident"; change: "opened" | "resolved" | "reviewed"; incident: Incident; now: number }
  | { type: "dismiss"; id: string }
  | { type: "dismissAll" }
  | { type: "tick"; now: number }
  | { type: "sound"; on: boolean }
  | { type: "permission"; value: NotificationState };

const STILL_OPEN = new Set(["OPEN", "ACKNOWLEDGED"]);

export function initialAlertState(prefs: { sound?: boolean; notifications?: NotificationState } = {}): AlertState {
  return {
    toasts: [],
    seen: [],
    pulsing: {},
    sound: prefs.sound ?? false,
    notifications: prefs.notifications ?? "default",
    alertSeq: 0,
    lastAlert: null,
  };
}

function withoutKey(rec: Record<string, number>, key: string): Record<string, number> {
  if (!(key in rec)) return rec;
  const next = { ...rec };
  delete next[key];
  return next;
}

export function alertReducer(state: AlertState, action: AlertAction): AlertState {
  switch (action.type) {
    case "incident": {
      const inc = action.incident;
      const id = inc.incidentId;
      if (inc.mode !== "enforce") return state;
      const open = STILL_OPEN.has(inc.status);

      if (action.change === "opened" && open && !state.seen.includes(id)) {
        const seen = [...state.seen, id];
        return {
          ...state,
          toasts: [{ id, incident: inc, raisedAt: action.now }, ...state.toasts].slice(0, MAX_TOASTS),
          seen: seen.length > MAX_SEEN ? seen.slice(seen.length - MAX_SEEN) : seen,
          pulsing: { ...state.pulsing, [id]: action.now + PULSE_MS },
          alertSeq: state.alertSeq + 1,
          lastAlert: inc,
        };
      }

      const shown = state.toasts.some((t) => t.id === id);
      if (!shown && !(id in state.pulsing)) return state;
      if (open) {
        // Keep the toast in step with review changes; ignore redelivered or stale versions.
        const current = state.toasts.find((t) => t.id === id);
        if (!current || inc.version <= current.incident.version) return state;
        return { ...state, toasts: state.toasts.map((t) => (t.id === id ? { ...t, incident: inc } : t)) };
      }
      return { ...state, toasts: state.toasts.filter((t) => t.id !== id), pulsing: withoutKey(state.pulsing, id) };
    }
    case "dismiss":
      if (!state.toasts.some((t) => t.id === action.id)) return state;
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    case "dismissAll":
      return state.toasts.length === 0 ? state : { ...state, toasts: [] };
    case "tick": {
      const expired = Object.keys(state.pulsing).filter((k) => (state.pulsing[k] ?? 0) <= action.now);
      if (expired.length === 0) return state;
      let pulsing = state.pulsing;
      for (const k of expired) pulsing = withoutKey(pulsing, k);
      return { ...state, pulsing };
    }
    case "sound":
      return state.sound === action.on ? state : { ...state, sound: action.on };
    case "permission":
      return state.notifications === action.value ? state : { ...state, notifications: action.value };
  }
}

/** Incident ids whose zone outline should pulse at `now`. */
export function pulsingIds(state: AlertState, now: number): Set<string> {
  return new Set(Object.keys(state.pulsing).filter((k) => (state.pulsing[k] ?? 0) > now));
}
