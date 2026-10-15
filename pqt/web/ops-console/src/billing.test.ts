import { describe, expect, it } from "vitest";
import { ago, buildSalesChart, connectorRows, connectorState, dayWindow, describeDay, joinBuckets, localDay, localTime, mainCurrency, minorDigits, money, per100, quantity, summarize, zoneOffsetMs } from "./billing";
import type { EdgeConnector, EdgeHealth, Payments, Sales, Traffic } from "./types";

const win = { storeId: "store-001", simRunId: null, from: "2026-05-27T18:30:00Z", to: "2026-05-28T18:30:00Z" };

const sales: Sales = {
  ...win, bucket: "15m", timeZone: "Asia/Kolkata", tillId: null,
  buckets: [
    { start: "2026-05-28T04:30:00Z", currency: "INR", bills: 1, salesMinor: 15000, taxMinor: 714, lines: 2, voidedBills: 0 },
    { start: "2026-05-28T05:30:00Z", currency: "INR", bills: 1, salesMinor: 3000, taxMinor: 143, lines: 1, voidedBills: 0 },
    { start: "2026-05-28T06:30:00Z", currency: "INR", bills: 0, salesMinor: 0, taxMinor: 0, lines: 0, voidedBills: 1 },
    { start: "2026-05-28T06:30:00Z", currency: "USD", bills: 1, salesMinor: 500, taxMinor: 0, lines: 1, voidedBills: 0 },
  ],
  totals: [
    { currency: "INR", bills: 2, salesMinor: 18000, taxMinor: 857, lines: 3, voidedBills: 1 },
    { currency: "USD", bills: 1, salesMinor: 500, taxMinor: 0, lines: 1, voidedBills: 0 },
  ],
};

const payments: Payments = {
  ...win, bucket: "day", timeZone: "Asia/Kolkata", tillId: null,
  buckets: [],
  totals: [
    { currency: "INR", tender: "card", inMinor: 15000, outMinor: 5000, netMinor: 10000, paymentsIn: 1, paymentsOut: 1 },
    { currency: "INR", tender: "cash", inMinor: 3000, outMinor: 0, netMinor: 3000, paymentsIn: 1, paymentsOut: 0 },
  ],
};

function traffic(starts: string[], visitors: (number | null)[], queues: [number | null, number | null][]): Traffic {
  return {
    ...win, bucket: "15m", timeZone: "Asia/Kolkata", entranceZones: ["entrance"], truncated: false,
    buckets: starts.map((start, i) => ({
      start, visitors: visitors[i] ?? null,
      queues: [{ queueId: "checkout-1", meanLength: queues[i]?.[0] ?? null, maxLength: queues[i]?.[1] ?? null }],
    })),
  };
}

describe("money and quantities", () => {
  it("formats integer minor units with the currency's own digits", () => {
    expect(minorDigits("INR")).toBe(2);
    expect(minorDigits("JPY")).toBe(0);
    expect(money(1500050, "INR")).toBe("₹15,000.50");
    expect(money(-5000, "INR")).toBe("-₹50.00");
    expect(money(1200, "JPY", "en")).toBe("¥1,200");
  });

  it("shows thousandths of a unit without float noise", () => {
    expect(quantity(2000, "pcs")).toBe("2 pcs");
    expect(quantity(1500, "kg")).toBe("1.5 kg");
    expect(quantity(125, "kg")).toBe("0.125 kg");
    expect(quantity(-3000, "pcs")).toBe("-3 pcs");
  });
});

describe("time zones", () => {
  it("knows India, Nepal and New York offsets", () => {
    expect(zoneOffsetMs(Date.parse("2026-05-28T00:00:00Z"), "Asia/Kolkata")).toBe(19_800_000);
    expect(zoneOffsetMs(Date.parse("2026-05-28T00:00:00Z"), "Asia/Kathmandu")).toBe(20_700_000);
    expect(zoneOffsetMs(Date.parse("2026-07-01T12:00:00Z"), "America/New_York")).toBe(-14_400_000);
    expect(zoneOffsetMs(Date.parse("2026-01-01T12:00:00Z"), "America/New_York")).toBe(-18_000_000);
  });

  it("turns a local calendar day into its UTC window", () => {
    expect(dayWindow("2026-05-28", "Asia/Kolkata")).toEqual({ from: "2026-05-27T18:30:00Z", to: "2026-05-28T18:30:00Z" });
    expect(dayWindow("2026-03-08", "America/New_York")).toEqual({ from: "2026-03-08T05:00:00Z", to: "2026-03-09T04:00:00Z" });
    expect(dayWindow("2026-12-31", "UTC")).toEqual({ from: "2026-12-31T00:00:00Z", to: "2027-01-01T00:00:00Z" });
    expect(() => dayWindow("28/05/2026", "UTC")).toThrow();
  });

  it("reads local days and clock times", () => {
    expect(localDay("2026-05-28T20:00:00Z", "Asia/Kolkata")).toBe("2026-05-29");
    expect(localDay("2026-05-28T20:00:00Z", "UTC")).toBe("2026-05-28");
    expect(localTime("2026-05-28T04:30:00Z", "Asia/Kolkata")).toBe("10:00");
  });
});

describe("the day's figures", () => {
  it("picks the store's main currency", () => {
    expect(mainCurrency(sales, payments)).toBe("INR");
    expect(mainCurrency(null, null)).toBeNull();
  });

  it("gives bills per 100 visitors only when visitors are known", () => {
    expect(per100(3, 40)).toBe(7.5);
    expect(per100(1, 3)).toBe(33.3);
    expect(per100(2, 0)).toBeNull();
    expect(per100(2, null)).toBeNull();
  });

  it("lines sales up with every traffic bucket in one currency", () => {
    const t = traffic(["2026-05-28T04:30:00Z", "2026-05-28T04:45:00Z", "2026-05-28T06:30:00Z"], [10, 0, 4], [[2.5, 4], [null, null], [1, 1]]);
    const rows = joinBuckets(sales, t, "INR");
    expect(rows.map((r) => [r.bills, r.salesMinor, r.visitors, r.billsPer100, r.meanQueue, r.maxQueue])).toEqual([
      [1, 15000, 10, 10, 2.5, 4],
      [0, 0, 0, null, null, null],
      [0, 0, 4, 0, 1, 1],
    ]);
    expect(rows[2]?.voidedBills).toBe(1);
  });

  it("sums the day: refunds are money paid out, net is in minus out", () => {
    const t = traffic(["2026-05-28T04:30:00Z", "2026-05-28T04:45:00Z"], [30, 10], [[1, 2], [0, 0]]);
    expect(summarize(sales, payments, t, "INR")).toEqual({
      bills: 2, salesMinor: 18000, voidedBills: 1, refundsMinor: 5000, netMinor: 13000, visitors: 40, billsPer100: 5,
    });
    expect(summarize(sales, payments, null, "INR").visitors).toBeNull();
  });
});

describe("the sales chart", () => {
  const t = traffic(["2026-05-28T03:30:00Z", "2026-05-28T03:45:00Z", "2026-05-28T04:30:00Z"], [1, 1, 1], [[1, 1], [null, null], [3, 4]]);
  const rows = joinBuckets(sales, t, "INR");
  const chart = buildSalesChart(rows, "Asia/Kolkata", { width: 340, height: 120, padLeft: 30, padRight: 10, padTop: 10, padBottom: 10 });

  it("draws one bar per bucket scaled to a nice maximum", () => {
    expect(chart.maxMinor).toBe(20000);
    expect(chart.bars).toHaveLength(3);
    expect(chart.bars[2]?.h).toBeCloseTo(75, 0);  // 15 000 of 20 000 over 100 px
    expect(chart.bars[0]?.h).toBe(0);
  });

  it("breaks the queue line where nothing is known", () => {
    expect(chart.queuePath.match(/M/g)).toHaveLength(2);
  });

  it("marks every third hour on the store's clock", () => {
    expect(chart.hourTicks.map((h) => h.label)).toEqual(["09:00"]);
  });

  it("describes the day in one sentence", () => {
    const summary = summarize(sales, payments, t, "INR");
    expect(describeDay(summary, rows, "INR", "Asia/Kolkata")).toBe("2 bills for ₹180.00, busiest at 10:00 with ₹150.00; the longest queue was 4.");
  });
});

describe("billing sources on the edge box (X1 B5f)", () => {
  const conn = (over: Partial<EdgeConnector>): EdgeConnector => ({
    id: "shop-db", type: "sql", ok: true, stale: false, failures: 0, lastSuccessAgeSeconds: 40, events: 12, rowErrors: 0, ...over,
  });
  const edge = (connectors: EdgeConnector[], stale = false): EdgeHealth => ({
    storeId: "store-001", generatedAt: "2026-10-15T10:00:00Z", staleAfterSeconds: 120,
    agents: [{ agentId: "edge-1", source: "urn:pqt:edge:store-001:edge-1", at: "2026-10-15T09:59:40Z", stale, agentVersion: "0.1.0",
      uptimeSeconds: 60, alarms: [], cameras: [], outbox: {}, connectors, clockOffsetMs: 0 }],
  });

  it("says plainly how each source is doing", () => {
    expect(connectorState(conn({}), false)).toEqual({ label: "Working", tone: "ok" });
    expect(connectorState(conn({ ok: false, failures: 1 }), false)).toEqual({ label: "Retrying (1 failed attempt)", tone: "warn" });
    expect(connectorState(conn({ ok: false, failures: 4 }), false)).toEqual({ label: "Failing (4 failed attempts)", tone: "bad" });
    expect(connectorState(conn({ stale: true }), false)).toEqual({ label: "No recent success", tone: "bad" });
    expect(connectorState(conn({ rowErrors: 3 }), false)).toEqual({ label: "Working, 3 records need fixing", tone: "warn" });
    expect(connectorState(conn({}), true)).toEqual({ label: "Unknown (edge box silent)", tone: "bad" });
  });

  it("lists every source of every box, with how long ago it last worked", () => {
    const rows = connectorRows(edge([conn({}), conn({ id: "push", type: "push", lastSuccessAgeSeconds: null })]));
    expect(rows.map((r) => [r.id, r.kind, r.lastSuccess])).toEqual([
      ["shop-db", "Database", "40 s ago"], ["push", "Push API", "not yet"],
    ]);
    expect(connectorRows({ ...edge([]), agents: [] })).toEqual([]);
  });

  it("formats ages for people", () => {
    expect(ago(5)).toBe("5 s ago");
    expect(ago(125)).toBe("2 min ago");
    expect(ago(7300)).toBe("2 h ago");
    expect(ago(200000)).toBe("2 days ago");
  });
});
