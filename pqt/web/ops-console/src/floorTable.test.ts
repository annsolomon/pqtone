import { describe, expect, it } from "vitest";
import { alertingZones, floorRows, zoneForIncident } from "./floor";
import type { Incident, Layout, StoreState } from "./types";

const layout: Layout = {
  storeId: "store-001", name: "Demo", width: 100, height: 100,
  zones: [
    { id: "entrance", name: "Entrance", x: 0, y: 0, w: 1, h: 1, kind: "entrance" },
    { id: "fitting-rooms", name: "Fitting rooms", x: 0, y: 0, w: 1, h: 1, kind: "monitored" },
    { id: "checkout", name: "Checkout", x: 0, y: 0, w: 1, h: 1, kind: "checkout" },
  ],
  queues: [{ id: "checkout-1", zoneId: "checkout" }],
  registers: [{ id: "reg-1" }, { id: "reg-2" }],
};
const state: StoreState = { storeId: "store-001", simRunId: "r", queues: { "checkout-1": { length: 7, openRegisters: 1 } }, zones: { entrance: 3 } };
const inc = (subject: string, severity: Incident["severity"]): Incident => ({
  incidentId: subject + severity, ruleId: "R", ruleVersion: "1", mode: "enforce", storeId: "store-001", simRunId: "r", key: "k",
  subject, severity, summary: "", onsetAt: "", detectedAt: "", resolvedAt: null, status: "OPEN", evidence: [], attrs: {}, updatedAt: "", version: 1,
});

describe("floor table rows (C3)", () => {
  it("maps incidents to zones through their queue", () => {
    expect(zoneForIncident(inc("queue:checkout-1", "medium"), layout)).toBe("checkout");
    expect(zoneForIncident(inc("zone:fitting-rooms", "low"), layout)).toBe("fitting-rooms");
    expect(zoneForIncident(inc("queue:unknown", "low"), layout)).toBeNull();
  });

  it("keeps the highest severity per zone", () => {
    const a = alertingZones([inc("queue:checkout-1", "medium"), inc("queue:checkout-1", "high"), inc("queue:checkout-1", "low")], layout);
    expect(a.get("checkout")).toBe("high");
  });

  it("lists queues with waiting, open registers and status, and zones without the checkout", () => {
    const rows = floorRows(layout, state, [inc("queue:checkout-1", "medium")]);
    expect(rows.queues).toEqual([{ id: "checkout-1", zoneName: "Checkout", waiting: 7, openRegisters: 1, registers: 2, alert: "medium" }]);
    expect(rows.zones).toEqual([
      { id: "entrance", name: "Entrance", people: 3, alert: null },
      { id: "fitting-rooms", name: "Fitting rooms", people: 0, alert: null },
    ]);
  });
});
