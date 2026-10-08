import { describe, expect, it } from "vitest";
import { MAX_SEEN, MAX_TOASTS, PULSE_MS, alertReducer, initialAlertState, pulsingIds } from "./alertReducer";
import type { Incident, IncidentStatus } from "../types";

let n = 0;
function incident(over: Partial<Incident> = {}): Incident {
  n += 1;
  return {
    incidentId: `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`,
    ruleId: "R-QUEUE-001",
    ruleVersion: "1.0.0",
    mode: "enforce",
    storeId: "store-001",
    simRunId: "run-a",
    key: "checkout-1",
    subject: "queue:checkout-1",
    severity: "medium",
    summary: "Queue checkout-1 has been at or above 6 for 60 s",
    onsetAt: "2026-01-01T09:00:00Z",
    detectedAt: "2026-01-01T09:01:00Z",
    resolvedAt: null,
    status: "OPEN",
    evidence: [],
    attrs: {},
    updatedAt: "2026-01-01T09:01:00Z",
    version: 1,
    ...over,
  };
}

const opened = (i: Incident, now = 1_000) => ({ type: "incident" as const, change: "opened" as const, incident: i, now });
const changed = (i: Incident, status: IncidentStatus, change: "resolved" | "reviewed" = "reviewed", now = 2_000) =>
  ({ type: "incident" as const, change, incident: { ...i, status, version: i.version + 1 }, now });

describe("alertReducer", () => {
  it("raises a toast, a pulse and a new alert for an opened enforce incident", () => {
    const i = incident();
    const s = alertReducer(initialAlertState(), opened(i, 1_000));
    expect(s.toasts.map((t) => t.id)).toEqual([i.incidentId]);
    expect(s.pulsing[i.incidentId]).toBe(1_000 + PULSE_MS);
    expect(s.alertSeq).toBe(1);
    expect(s.lastAlert?.incidentId).toBe(i.incidentId);
  });

  it("never alerts on shadow-mode incidents", () => {
    const s0 = initialAlertState();
    const s = alertReducer(s0, opened(incident({ mode: "shadow", ruleId: "R-ABS-001" })));
    expect(s).toBe(s0);
  });

  it("ignores an incident that is no longer open when its opened event arrives", () => {
    const s0 = initialAlertState();
    expect(alertReducer(s0, opened(incident({ status: "CONFIRMED" })))).toBe(s0);
  });

  it("alerts once per incident, even if the stream redelivers it", () => {
    const i = incident();
    const s1 = alertReducer(initialAlertState(), opened(i));
    const s2 = alertReducer(s1, opened(i, 5_000));
    expect(s2).toBe(s1);
    // ...including after the toast was dismissed
    const s3 = alertReducer(alertReducer(s1, { type: "dismiss", id: i.incidentId }), opened(i, 6_000));
    expect(s3.toasts).toEqual([]);
    expect(s3.alertSeq).toBe(1);
  });

  it("keeps the newest toasts first and caps the stack", () => {
    let s = initialAlertState();
    const ids: string[] = [];
    for (let k = 0; k < MAX_TOASTS + 2; k++) {
      const i = incident();
      ids.push(i.incidentId);
      s = alertReducer(s, opened(i, 1_000 + k));
    }
    expect(s.toasts).toHaveLength(MAX_TOASTS);
    expect(s.toasts[0]?.id).toBe(ids[ids.length - 1]);
    expect(s.toasts.map((t) => t.id)).not.toContain(ids[0]);
    expect(s.alertSeq).toBe(MAX_TOASTS + 2);
  });

  it("removes the toast and pulse once the incident is resolved, confirmed or dismissed", () => {
    for (const status of ["AUTO_RESOLVED", "CONFIRMED", "DISMISSED", "CLOSED"] as const) {
      const i = incident();
      const s = alertReducer(alertReducer(initialAlertState(), opened(i)), changed(i, status, "resolved"));
      expect(s.toasts).toEqual([]);
      expect(s.pulsing[i.incidentId]).toBeUndefined();
    }
  });

  it("keeps an acknowledged incident on screen with its new status", () => {
    const i = incident();
    const s = alertReducer(alertReducer(initialAlertState(), opened(i)), changed(i, "ACKNOWLEDGED"));
    expect(s.toasts[0]?.incident.status).toBe("ACKNOWLEDGED");
    expect(s.alertSeq).toBe(1);
  });

  it("ignores redelivered or stale versions of a shown incident", () => {
    const i = incident({ version: 3 });
    const s1 = alertReducer(initialAlertState(), opened(i));
    expect(alertReducer(s1, { type: "incident", change: "reviewed", incident: { ...i, status: "ACKNOWLEDGED", version: 2 }, now: 3_000 })).toBe(s1);
  });

  it("returns the same state for changes to incidents it never alerted on", () => {
    const s0 = alertReducer(initialAlertState(), opened(incident()));
    expect(alertReducer(s0, changed(incident(), "CONFIRMED"))).toBe(s0);
  });

  it("expires pulses on tick and is a no-op when nothing expires", () => {
    const i = incident();
    const s1 = alertReducer(initialAlertState(), opened(i, 1_000));
    expect(alertReducer(s1, { type: "tick", now: 1_000 + PULSE_MS - 1 })).toBe(s1);
    const s2 = alertReducer(s1, { type: "tick", now: 1_000 + PULSE_MS });
    expect(s2.pulsing).toEqual({});
    expect(s2.toasts).toHaveLength(1);
    expect(pulsingIds(s1, 1_000)).toEqual(new Set([i.incidentId]));
    expect(pulsingIds(s1, 1_000 + PULSE_MS)).toEqual(new Set());
  });

  it("dismisses one or all toasts", () => {
    const a = incident();
    const b = incident();
    const s = alertReducer(alertReducer(initialAlertState(), opened(a)), opened(b));
    expect(alertReducer(s, { type: "dismiss", id: a.incidentId }).toasts.map((t) => t.id)).toEqual([b.incidentId]);
    expect(alertReducer(s, { type: "dismissAll" }).toasts).toEqual([]);
    expect(alertReducer(s, { type: "dismiss", id: "unknown" })).toBe(s);
  });

  it("stores the sound and notification preferences", () => {
    const s0 = initialAlertState({ sound: false, notifications: "default" });
    const s1 = alertReducer(s0, { type: "sound", on: true });
    expect(s1.sound).toBe(true);
    expect(alertReducer(s1, { type: "sound", on: true })).toBe(s1);
    expect(alertReducer(s1, { type: "permission", value: "granted" }).notifications).toBe("granted");
  });

  it("bounds the memory of alerted incidents", () => {
    let s = initialAlertState();
    for (let k = 0; k < MAX_SEEN + 10; k++) s = alertReducer(s, opened(incident(), k));
    expect(s.seen.length).toBe(MAX_SEEN);
  });

  it("does not mutate the previous state", () => {
    const s0 = Object.freeze(initialAlertState());
    expect(() => alertReducer(s0, opened(incident()))).not.toThrow();
    expect(s0.toasts).toEqual([]);
  });
});
