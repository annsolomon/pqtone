import type { Layout, LiveEvent, StoreState } from "./types";

/** Applies live events to the floor read model. Pure, so it is unit-tested. */
export function applyEvents(state: StoreState, events: LiveEvent[]): StoreState {
  let next: StoreState = state;
  for (const e of events) {
    if (e.storeId !== state.storeId) continue;
    if (e.simRunId && e.simRunId !== next.simRunId) {
      next = { storeId: next.storeId, simRunId: e.simRunId, queues: {}, zones: {} };
    }
    if (e.type === "zone.entered" || e.type === "zone.exited") {
      const zone = String(e.data["zoneId"] ?? "");
      if (!zone) continue;
      const delta = e.type === "zone.entered" ? 1 : -1;
      next = { ...next, zones: { ...next.zones, [zone]: Math.max(0, (next.zones[zone] ?? 0) + delta) } };
    } else if (e.type === "queue.length") {
      const q = String(e.data["queueId"] ?? "");
      const length = Number(e.data["length"]);
      const openRegisters = Number(e.data["openRegisters"]);
      if (!q || Number.isNaN(length)) continue;
      next = { ...next, queues: { ...next.queues, [q]: { length, openRegisters } } };
    }
  }
  return next;
}

/** Occupancy -> fill strength in [0.04, 0.55]; saturates at `full` people. */
export function occupancyStrength(count: number, full = 25): number {
  const c = Math.max(0, Math.min(count, full));
  return 0.04 + (c / full) * 0.51;
}

/** Positions for a serpentine queue trail inside a box. */
export function queueTrail(n: number, box: { x: number; y: number; w: number; h: number }, step = 22): { x: number; y: number }[] {
  const perRow = Math.max(1, Math.floor((box.w - step) / step));
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i++) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const x = row % 2 === 0 ? box.x + step / 2 + col * step : box.x + box.w - step / 2 - col * step;
    const y = box.y + box.h - step / 2 - row * step;
    if (y < box.y) break;
    out.push({ x, y });
  }
  return out;
}

/** Milestone S2: the registers that serve a queue (a register without queueId serves the first queue). */
export function registersOf(layout: Layout, queueId: string): { id: string; queueId?: string }[] {
  const first = layout.queues[0]?.id;
  return layout.registers.filter((r) => (r.queueId ?? first) === queueId);
}
