import { describe as suite, expect, it } from "vitest";
import { buildChart, describe, domainOf, niceMax, DEFAULT_SIZE } from "./timeline";
import type { IncidentTimelineData } from "./types";

const base: IncidentTimelineData = {
  incidentId: "00000000-0000-0000-0000-000000000001",
  from: "2026-01-01T08:50:00Z",
  to: "2026-01-01T10:50:00Z",
  onsetAt: "2026-01-01T09:00:00Z",
  detectedAt: "2026-01-01T09:01:00Z",
  resolvedAt: "2026-01-01T09:05:00Z",
  threshold: 6,
  kind: "queue",
  target: "checkout-1",
  series: [
    { t: "2026-01-01T08:50:00Z", length: 2, openRegisters: 1 },
    { t: "2026-01-01T08:58:00Z", length: 5, openRegisters: 1 },
    { t: "2026-01-01T09:00:00Z", length: 7, openRegisters: 1 },
    { t: "2026-01-01T09:04:00Z", length: 3, openRegisters: 2 },
  ],
  markers: [{ t: "2026-01-01T09:03:00Z", kind: "register.opened", registerId: "reg-2" }],
  truncated: false,
};

suite("timeline domain", () => {
  it("ends five minutes after clearing, not at the two-hour window end", () => {
    const [a, b] = domainOf(base);
    expect(new Date(a).toISOString()).toBe("2026-01-01T08:50:00.000Z");
    expect(new Date(b).toISOString()).toBe("2026-01-01T09:10:00.000Z");
  });

  it("extends to the last data point for an ongoing incident but never past the window", () => {
    const ongoing = { ...base, resolvedAt: null, series: [...base.series, { t: "2026-01-01T09:30:00Z", length: 8 }] };
    expect(new Date(domainOf(ongoing)[1]).toISOString()).toBe("2026-01-01T09:30:00.000Z");
    const late = { ...ongoing, series: [...ongoing.series, { t: "2026-01-01T12:00:00Z", length: 1 }] };
    expect(new Date(domainOf(late)[1]).toISOString()).toBe("2026-01-01T10:50:00.000Z");
  });
});

suite("niceMax", () => {
  it("rounds up to 1, 2 or 5 times a power of ten", () => {
    expect([0, 1, 3, 7, 11, 23, 51].map(niceMax)).toEqual([1, 1, 5, 10, 20, 50, 100]);
  });
});

suite("queue chart", () => {
  const m = buildChart(base);

  it("draws a step line that holds each length until the next event", () => {
    expect(m.linePath.startsWith(`M${DEFAULT_SIZE.padLeft},`)).toBe(true);
    // 4 points: one move, then three horizontal+vertical steps, then a final horizontal run to the domain end.
    expect(m.linePath.match(/H/g)?.length).toBe(4);
    expect(m.linePath.match(/V/g)?.length).toBe(3);
    expect(m.linePath.endsWith(`H${DEFAULT_SIZE.width - DEFAULT_SIZE.padRight}`)).toBe(true);
  });

  it("scales the y-axis to cover the peak and the threshold", () => {
    expect(m.yMax).toBe(10);
    expect(m.thresholdY).not.toBeNull();
    const top = DEFAULT_SIZE.padTop;
    const bottom = DEFAULT_SIZE.height - DEFAULT_SIZE.padBottom;
    expect(m.thresholdY!).toBeGreaterThan(top);
    expect(m.thresholdY!).toBeLessThan(bottom);
  });

  it("marks onset, detection and clearing in order, and the register opening", () => {
    expect(m.moments.map((x) => x.kind)).toEqual(["onset", "detected", "cleared"]);
    const xs = m.moments.map((x) => x.x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(m.markers).toEqual([{ x: expect.any(Number), label: "reg-2 opened at 09:03", opened: true }]);
  });

  it("puts x ticks inside the plot with HH:MM labels", () => {
    expect(m.xTicks.length).toBeGreaterThan(1);
    for (const t of m.xTicks) {
      expect(t.label).toMatch(/^\d\d:\d\d$/);
      expect(t.x).toBeGreaterThanOrEqual(DEFAULT_SIZE.padLeft);
      expect(t.x).toBeLessThanOrEqual(DEFAULT_SIZE.width - DEFAULT_SIZE.padRight);
    }
  });

  it("describes itself for screen readers", () => {
    expect(describe(base)).toBe("Length of queue checkout-1 over time, peaking at 7 against a threshold of 6, with 1 register change.");
  });
});

suite("zone chart", () => {
  const zone: IncidentTimelineData = {
    ...base,
    kind: "zone",
    target: "entrance",
    threshold: null,
    markers: [],
    resolvedAt: null,
    detectedAt: "2026-01-01T08:52:00Z",
    onsetAt: "2026-01-01T08:51:00Z",
    series: [
      { t: "2026-01-01T08:50:00Z", entries: 0 },
      { t: "2026-01-01T08:51:00Z", entries: 4 },
      { t: "2026-01-01T08:52:00Z", entries: 1 },
    ],
  };
  const m = buildChart(zone);

  it("draws one bar per minute with height proportional to entries", () => {
    expect(m.bars.map((b) => b.value)).toEqual([0, 4, 1]);
    const [b0, b1, b2] = m.bars;
    expect(b1!.h).toBeGreaterThan(b2!.h);
    expect(b0!.h).toBe(0);
    expect(m.linePath).toBe("");
    expect(m.thresholdY).toBeNull();
  });

  it("describes its peak", () => {
    expect(describe(zone)).toBe("Entries per minute into entrance, peaking at 4, from 08:50 UTC.");
  });
});

suite("empty timeline", () => {
  it("is flagged and described", () => {
    const none = { ...base, kind: "none" as const, series: [], markers: [], target: null };
    expect(buildChart(none).empty).toBe(true);
    expect(describe(none)).toBe("No events were recorded around this incident.");
  });
});
