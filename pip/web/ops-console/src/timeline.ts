/**
 * Milestone C4: pure geometry for the incident timeline chart (no React, no DOM), so it is unit-tested.
 *
 * Queue incidents draw queue length as a step line (a queue length holds until the next event) with the
 * rule threshold, the onset/detection/clear moments and register markers. Zone incidents draw entries
 * per minute as bars. The x-domain starts at the window start and ends at the later of detection + 5 min
 * and the last data point, never past the window end, so an ongoing incident isn't mostly empty space.
 */
import type { IncidentTimelineData } from "./types";

export interface ChartSize {
  width: number;
  height: number;
  padLeft: number;
  padRight: number;
  padTop: number;
  padBottom: number;
}

export const DEFAULT_SIZE: ChartSize = { width: 720, height: 220, padLeft: 40, padRight: 12, padTop: 12, padBottom: 28 };

export interface Tick {
  x: number;
  label: string;
}

export interface Moment {
  x: number;
  label: string;
  kind: "onset" | "detected" | "cleared";
}

export interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
  value: number;
  t: string;
}

export interface ChartModel {
  kind: "queue" | "zone" | "none";
  domain: [number, number];
  yMax: number;
  linePath: string;
  bars: Bar[];
  thresholdY: number | null;
  moments: Moment[];
  markers: { x: number; label: string; opened: boolean }[];
  xTicks: Tick[];
  yTicks: { y: number; label: string }[];
  empty: boolean;
}

const AFTER_MS = 5 * 60_000;

export function domainOf(d: IncidentTimelineData): [number, number] {
  const from = Date.parse(d.from);
  const to = Date.parse(d.to);
  let end = Date.parse(d.resolvedAt ?? d.detectedAt) + AFTER_MS;
  for (const p of d.series) end = Math.max(end, Date.parse(p.t) + (d.kind === "zone" ? 60_000 : 0));
  for (const m of d.markers) end = Math.max(end, Date.parse(m.t));
  end = Math.min(end, to);
  return [from, Math.max(end, from + 60_000)];
}

/** A "nice" axis maximum: at least 1, at least the threshold + 1, rounded up to 1, 2 or 5 x 10^k. */
export function niceMax(v: number): number {
  if (v <= 1) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function hhmm(ms: number): string {
  return new Date(ms).toISOString().slice(11, 16);
}

export function buildChart(d: IncidentTimelineData, size: ChartSize = DEFAULT_SIZE): ChartModel {
  const [x0, x1] = domainOf(d);
  const innerW = size.width - size.padLeft - size.padRight;
  const innerH = size.height - size.padTop - size.padBottom;
  const sx = (ms: number) => size.padLeft + ((Math.min(Math.max(ms, x0), x1) - x0) / (x1 - x0)) * innerW;

  const values = d.series.map((p) => (d.kind === "zone" ? p.entries ?? 0 : p.length ?? 0));
  const yMax = niceMax(Math.max(1, ...values, d.threshold != null ? d.threshold + 1 : 0));
  const sy = (v: number) => size.padTop + innerH - (v / yMax) * innerH;
  const r = (n: number) => Math.round(n * 10) / 10;

  let linePath = "";
  const bars: Bar[] = [];
  if (d.kind === "queue" && d.series.length > 0) {
    const pts = d.series.filter((p) => Date.parse(p.t) <= x1);
    pts.forEach((p, i) => {
      const x = r(sx(Date.parse(p.t)));
      const y = r(sy(p.length ?? 0));
      linePath += i === 0 ? `M${x},${y}` : `H${x}V${y}`;
    });
    if (pts.length > 0) linePath += `H${r(sx(x1))}`;
  } else if (d.kind === "zone") {
    for (const p of d.series) {
      const t = Date.parse(p.t);
      if (t >= x1) continue;
      const xa = sx(t);
      const xb = sx(t + 60_000);
      const v = p.entries ?? 0;
      bars.push({ x: r(xa + 0.5), y: r(sy(v)), w: r(Math.max(1, xb - xa - 1)), h: r(sy(0) - sy(v)), value: v, t: p.t });
    }
  }

  const moments: Moment[] = [
    { x: r(sx(Date.parse(d.onsetAt))), label: "breach started", kind: "onset" },
    { x: r(sx(Date.parse(d.detectedAt))), label: "alert raised", kind: "detected" },
  ];
  if (d.resolvedAt && Date.parse(d.resolvedAt) <= x1) {
    moments.push({ x: r(sx(Date.parse(d.resolvedAt))), label: "cleared", kind: "cleared" });
  }

  const markers = d.markers
    .filter((m) => Date.parse(m.t) <= x1)
    .map((m) => ({
      x: r(sx(Date.parse(m.t))),
      label: `${m.registerId} ${m.kind === "register.opened" ? "opened" : "closed"} at ${hhmm(Date.parse(m.t))}`,
      opened: m.kind === "register.opened",
    }));

  const span = x1 - x0;
  const step = [60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000, 3_600_000].find((s) => span / s <= 8) ?? 3_600_000;
  const xTicks: Tick[] = [];
  for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) xTicks.push({ x: r(sx(t)), label: hhmm(t) });

  const yStep = yMax <= 5 ? 1 : yMax / 5;
  const yTicks: { y: number; label: string }[] = [];
  for (let v = 0; v <= yMax + 1e-9; v += yStep) yTicks.push({ y: r(sy(v)), label: String(Math.round(v)) });

  return {
    kind: d.kind,
    domain: [x0, x1],
    yMax,
    linePath,
    bars,
    thresholdY: d.kind === "queue" && d.threshold != null ? r(sy(d.threshold)) : null,
    moments,
    markers,
    xTicks,
    yTicks,
    empty: d.series.length === 0,
  };
}

/** One sentence that says what the chart shows, for the SVG's accessible name. */
export function describe(d: IncidentTimelineData): string {
  if (d.kind === "none" || d.series.length === 0) return "No events were recorded around this incident.";
  if (d.kind === "zone") {
    const peak = Math.max(...d.series.map((p) => p.entries ?? 0));
    return `Entries per minute into ${d.target}, peaking at ${peak}, from ${hhmm(Date.parse(d.from))} UTC.`;
  }
  const peak = Math.max(...d.series.map((p) => p.length ?? 0));
  const thr = d.threshold != null ? ` against a threshold of ${d.threshold}` : "";
  const regs = d.markers.length ? `, with ${d.markers.length} register change${d.markers.length === 1 ? "" : "s"}` : "";
  return `Length of queue ${d.target} over time, peaking at ${peak}${thr}${regs}.`;
}
