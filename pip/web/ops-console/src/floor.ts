import type { Incident, Layout, LiveEvent, StoreState } from "./types";

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

/** The zone an incident is about: its own zone, or the zone that holds its queue. */
export function zoneForIncident(inc: Incident, layout: Layout): string | null {
  const s = inc.subject ?? "";
  if (s.startsWith("zone:")) return s.slice(5);
  if (s.startsWith("queue:")) return layout.queues.find((q) => q.id === s.slice(6))?.zoneId ?? null;
  return null;
}

const SEVERITY_RANK: Record<Incident["severity"], number> = { low: 1, medium: 2, high: 3 };

/** Highest severity of the open incidents per zone. */
export function alertingZones(incidents: Incident[], layout: Layout): Map<string, Incident["severity"]> {
  const out = new Map<string, Incident["severity"]>();
  for (const inc of incidents) {
    const z = zoneForIncident(inc, layout);
    if (!z) continue;
    const prev = out.get(z);
    if (!prev || SEVERITY_RANK[inc.severity] > SEVERITY_RANK[prev]) out.set(z, inc.severity);
  }
  return out;
}

export interface FloorZoneRow {
  id: string;
  name: string;
  people: number;
  alert: Incident["severity"] | null;
}

export interface FloorQueueRow {
  id: string;
  zoneName: string;
  waiting: number;
  openRegisters: number;
  registers: number;
  alert: Incident["severity"] | null;
}

/** Milestone C3: the floor map as rows, for the table alternative screen-reader and keyboard users get. */
export function floorRows(layout: Layout, state: StoreState, incidents: Incident[]): { zones: FloorZoneRow[]; queues: FloorQueueRow[] } {
  const alerting = alertingZones(incidents, layout);
  const zones = layout.zones
    .filter((z) => z.kind !== "checkout")
    .map((z) => ({ id: z.id, name: z.name, people: state.zones[z.id] ?? 0, alert: alerting.get(z.id) ?? null }));
  const queues = layout.queues.map((q) => {
    const st = state.queues[q.id];
    return {
      id: q.id,
      zoneName: layout.zones.find((z) => z.id === q.zoneId)?.name ?? q.zoneId,
      waiting: st?.length ?? 0,
      openRegisters: st?.openRegisters ?? 0,
      registers: layout.registers.length,
      alert: alerting.get(q.zoneId) ?? null,
    };
  });
  return { zones, queues };
}
