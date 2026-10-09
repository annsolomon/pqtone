import { describe, expect, it } from "vitest";
import { registersOf } from "./floor";
import type { Layout } from "./types";

const base = { storeId: "s", name: "S", width: 1, height: 1, zones: [] } as const;

describe("registersOf (S2)", () => {
  it("gives each queue its own registers", () => {
    const layout: Layout = { ...base, zones: [], queues: [{ id: "checkout-1", zoneId: "a" }, { id: "checkout-2", zoneId: "b" }],
      registers: [{ id: "reg-1", queueId: "checkout-1" }, { id: "reg-2", queueId: "checkout-2" }, { id: "reg-3", queueId: "checkout-2" }] };
    expect(registersOf(layout, "checkout-1").map((r) => r.id)).toEqual(["reg-1"]);
    expect(registersOf(layout, "checkout-2").map((r) => r.id)).toEqual(["reg-2", "reg-3"]);
  });

  it("assigns registers without a queueId to the first queue (one-queue layouts)", () => {
    const layout: Layout = { ...base, zones: [], queues: [{ id: "checkout-1", zoneId: "checkout" }], registers: [{ id: "reg-1" }, { id: "reg-2" }] };
    expect(registersOf(layout, "checkout-1").map((r) => r.id)).toEqual(["reg-1", "reg-2"]);
  });
});

describe("floor table with two lines (S2 + C3)", () => {
  it("counts each line's own registers", async () => {
    const { floorRows } = await import("./floor");
    const layout: Layout = { ...base, zones: [
      { id: "a", name: "Checkout A", x: 0, y: 0, w: 1, h: 1, kind: "checkout" },
      { id: "b", name: "Checkout B", x: 0, y: 0, w: 1, h: 1, kind: "checkout" }],
      queues: [{ id: "checkout-1", zoneId: "a" }, { id: "checkout-2", zoneId: "b" }],
      registers: [{ id: "reg-1", queueId: "checkout-1" }, { id: "reg-2", queueId: "checkout-2" }, { id: "reg-3", queueId: "checkout-2" }] };
    const state = { storeId: "s", simRunId: null, zones: {}, queues: { "checkout-2": { length: 4, openRegisters: 1 } } };
    expect(floorRows(layout, state, []).queues.map((q) => [q.zoneName, q.waiting, q.openRegisters, q.registers]))
      .toEqual([["Checkout A", 0, 0, 1], ["Checkout B", 4, 1, 2]]);
  });
});
