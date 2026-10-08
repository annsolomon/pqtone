import { describe, expect, it } from "vitest";
import { applyEvents, occupancyStrength, queueTrail } from "./floor";
import { readCookie } from "./api";
import { describeEvent, duration, ruleName } from "./format";
import type { LiveEvent, StoreState } from "./types";

const base: StoreState = { storeId: "store-001", simRunId: "run-a", queues: {}, zones: {} };
const ev = (type: string, data: Record<string, unknown>, simRunId = "run-a", storeId = "store-001"): LiveEvent => ({
  id: Math.random().toString(36), type, time: "2026-01-01T09:00:00.000Z", storeId, subject: "x", simRunId, data,
});

describe("applyEvents", () => {
  it("tracks zone occupancy and never goes negative", () => {
    const s = applyEvents(base, [
      ev("zone.entered", { zoneId: "produce" }),
      ev("zone.entered", { zoneId: "produce" }),
      ev("zone.exited", { zoneId: "produce" }),
      ev("zone.exited", { zoneId: "grocery" }),
    ]);
    expect(s.zones).toEqual({ produce: 1, grocery: 0 });
  });

  it("takes the latest queue length", () => {
    const s = applyEvents(base, [ev("queue.length", { queueId: "checkout-1", length: 7, openRegisters: 2 })]);
    expect(s.queues["checkout-1"]).toEqual({ length: 7, openRegisters: 2 });
  });

  it("resets when a new simulation run starts and ignores other stores", () => {
    const s1 = applyEvents(base, [ev("zone.entered", { zoneId: "produce" })]);
    const s2 = applyEvents(s1, [ev("zone.entered", { zoneId: "apparel" }, "run-b"), ev("zone.entered", { zoneId: "x" }, "run-b", "store-002")]);
    expect(s2.simRunId).toBe("run-b");
    expect(s2.zones).toEqual({ apparel: 1 });
  });

  it("does not mutate its input", () => {
    const frozen = Object.freeze({ ...base, zones: Object.freeze({}) }) as StoreState;
    expect(() => applyEvents(frozen, [ev("zone.entered", { zoneId: "produce" })])).not.toThrow();
  });
});

describe("helpers", () => {
  it("bounds occupancy strength", () => {
    expect(occupancyStrength(0)).toBeCloseTo(0.04);
    expect(occupancyStrength(1000)).toBeCloseTo(0.55);
  });

  it("lays out a serpentine queue inside the box", () => {
    const pts = queueTrail(20, { x: 0, y: 0, w: 110, h: 200 }, 22);
    expect(pts).toHaveLength(20);
    for (const p of pts) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(110);
    }
  });

  it("reads the CSRF cookie", () => {
    expect(readCookie("XSRF-TOKEN", "a=1; XSRF-TOKEN=abc%3D%3D; b=2")).toBe("abc==");
    expect(readCookie("missing", "a=1")).toBeUndefined();
  });

  it("formats durations and names", () => {
    expect(duration("2026-01-01T09:00:00Z", "2026-01-01T09:01:05Z")).toBe("1 min 5 s");
    expect(ruleName("R-ABS-001")).toBe("No register opened");
    expect(describeEvent("queue.length", { queueId: "checkout-1", length: 3, openRegisters: 1 })).toContain("3 waiting");
  });
});
