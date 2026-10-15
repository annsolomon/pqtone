/**
 * X1 milestone B7: pure helpers for the Sales page (no React, no DOM), unit-tested.
 *
 * Money arrives as integer minor units and quantities as thousandths of their unit; nothing here does
 * arithmetic on money other than adding integers and dividing once for display. Time zones are IANA names
 * resolved with Intl, so the page shows the store's local day whatever the browser's own zone is.
 */
import type { EdgeConnector, EdgeHealth, Payments, Sales, Tender, TillKind, Traffic } from "./types";

export const TENDER_LABELS: Record<Tender, string> = {
  cash: "Cash", card: "Card", upi: "UPI", wallet: "Wallet", credit: "Credit", voucher: "Voucher", other: "Other",
};

export const TILL_LABELS: Record<TillKind, string> = {
  void: "void", refund: "refund", no_sale: "no-sale", price_override: "price override", drawer_open: "drawer opened",
  shift_open: "shift opened", shift_close: "shift closed",
};

/** Digits after the decimal point for a currency's minor unit (INR 2, JPY 0, KWD 3). */
export function minorDigits(currency: string): number {
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** An integer amount in minor units, formatted as money. */
export function money(minor: number, currency: string, locale = "en-IN"): string {
  const digits = minorDigits(currency);
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency, minimumFractionDigits: digits, maximumFractionDigits: digits })
      .format(minor / 10 ** digits);
  } catch {
    return `${(minor / 10 ** digits).toFixed(digits)} ${currency}`;
  }
}

/** A quantity in thousandths of its unit: 2000 pcs -> "2 pcs", 1500 kg -> "1.5 kg". */
export function quantity(milli: number, unit: string): string {
  const v = milli / 1000;
  const text = Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return `${text} ${unit}`;
}

interface Parts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function parts(utcMs: number, tz: string): Parts {
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const out: Parts = { year: 0, month: 1, day: 1, hour: 0, minute: 0, second: 0 };
  for (const p of f.formatToParts(new Date(utcMs))) {
    if (p.type in out) out[p.type as keyof Parts] = Number(p.value);
  }
  return out;
}

/** The zone's offset from UTC at this instant, in milliseconds (+05:30 -> 19 800 000). */
export function zoneOffsetMs(utcMs: number, tz: string): number {
  const p = parts(utcMs, tz);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}

/** The instant of a local wall-clock time in a zone (the earlier one if the clock repeats). */
export function zonedToUtc(year: number, month: number, day: number, hour: number, minute: number, tz: string): number {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  let t = guess - zoneOffsetMs(guess, tz);
  t = guess - zoneOffsetMs(t, tz);  // a second pass settles a DST change between the guess and the answer
  return t;
}

/** The local calendar day (YYYY-MM-DD) of an instant in a zone. */
export function localDay(iso: string, tz: string): string {
  const p = parts(Date.parse(iso), tz);
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}

/** HH:MM on the zone's clock. */
export function localTime(iso: string, tz: string): string {
  const p = parts(Date.parse(iso), tz);
  return `${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")}`;
}

/** [local midnight, next local midnight) of a calendar day in a zone, as UTC ISO instants. */
export function dayWindow(date: string, tz: string): { from: string; to: string } {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) throw new Error(`not a date: ${date}`);
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const next = new Date(Date.UTC(y, mo - 1, d + 1));
  const from = zonedToUtc(y, mo, d, 0, 0, tz);
  const to = zonedToUtc(next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate(), 0, 0, tz);
  return { from: new Date(from).toISOString().replace(".000Z", "Z"), to: new Date(to).toISOString().replace(".000Z", "Z") };
}

/** The currency with the largest sales (the store's own); null when nothing was sold or paid. */
export function mainCurrency(sales: Sales | null, payments: Payments | null): string | null {
  const by = new Map<string, number>();
  for (const t of sales?.totals ?? []) by.set(t.currency, (by.get(t.currency) ?? 0) + Math.abs(t.salesMinor));
  for (const t of payments?.totals ?? []) by.set(t.currency, (by.get(t.currency) ?? 0) + Math.abs(t.inMinor));
  let best: string | null = null;
  for (const [c, v] of by) if (best === null || v > (by.get(best) ?? 0)) best = c;
  return best;
}

/** Bills per 100 visitors, one decimal; null when the visitors are unknown or zero. */
export function per100(bills: number, visitors: number | null): number | null {
  if (visitors == null || visitors <= 0) return null;
  return Math.round((bills / visitors) * 1000) / 10;
}

export interface QuarterRow {
  start: string;
  bills: number;
  salesMinor: number;
  voidedBills: number;
  visitors: number | null;
  billsPer100: number | null;
  meanQueue: number | null;
  maxQueue: number | null;
}

/**
 * One row per traffic bucket (the traffic read returns every bucket of the day), with that bucket's sales in
 * one currency. Queue figures are summed over the store's queues (mean of a sum = sum of means); the peak is the
 * largest single queue.
 */
export function joinBuckets(sales: Sales, traffic: Traffic, currency: string): QuarterRow[] {
  const s = new Map(sales.buckets.filter((b) => b.currency === currency).map((b) => [Date.parse(b.start), b]));
  return traffic.buckets.map((t) => {
    const b = s.get(Date.parse(t.start));
    const known = t.queues.filter((q) => q.meanLength != null);
    const peaks = t.queues.map((q) => q.maxLength).filter((v): v is number => v != null);
    const bills = b?.bills ?? 0;
    return {
      start: t.start,
      bills,
      salesMinor: b?.salesMinor ?? 0,
      voidedBills: b?.voidedBills ?? 0,
      visitors: t.visitors,
      billsPer100: per100(bills, t.visitors),
      meanQueue: known.length ? Math.round(known.reduce((a, q) => a + (q.meanLength ?? 0), 0) * 100) / 100 : null,
      maxQueue: peaks.length ? Math.max(...peaks) : null,
    };
  });
}

export interface DaySummary {
  bills: number;
  salesMinor: number;
  voidedBills: number;
  refundsMinor: number;
  netMinor: number;
  visitors: number | null;
  billsPer100: number | null;
}

export function summarize(sales: Sales, payments: Payments, traffic: Traffic | null, currency: string): DaySummary {
  const st = sales.totals.find((t) => t.currency === currency);
  const pay = payments.totals.filter((t) => t.currency === currency);
  const visitors = traffic && traffic.buckets.some((b) => b.visitors != null)
    ? traffic.buckets.reduce((a, b) => a + (b.visitors ?? 0), 0) : null;
  const bills = st?.bills ?? 0;
  return {
    bills,
    salesMinor: st?.salesMinor ?? 0,
    voidedBills: st?.voidedBills ?? 0,
    refundsMinor: pay.reduce((a, t) => a + t.outMinor, 0),
    netMinor: pay.reduce((a, t) => a + t.netMinor, 0),
    visitors,
    billsPer100: per100(bills, visitors),
  };
}

export interface ChartSize {
  width: number;
  height: number;
  padLeft: number;
  padRight: number;
  padTop: number;
  padBottom: number;
}

export const SALES_CHART: ChartSize = { width: 960, height: 240, padLeft: 64, padRight: 40, padTop: 12, padBottom: 28 };

export interface SalesChart {
  bars: { x: number; y: number; w: number; h: number; row: QuarterRow }[];
  queuePath: string;
  moneyTicks: { y: number; value: number }[];
  queueTicks: { y: number; value: number }[];
  hourTicks: { x: number; label: string }[];
  maxMinor: number;
  maxQueue: number;
}

function nice(v: number): number {
  if (v <= 1) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/** Sales as bars and the store's mean queue length as a step line, on two axes, one bar per bucket. */
export function buildSalesChart(rows: QuarterRow[], tz: string, size: ChartSize = SALES_CHART): SalesChart {
  const innerW = size.width - size.padLeft - size.padRight;
  const innerH = size.height - size.padTop - size.padBottom;
  const maxMinor = nice(Math.max(1, ...rows.map((r) => r.salesMinor)));
  const maxQueue = nice(Math.max(1, ...rows.map((r) => r.meanQueue ?? 0)));
  const w = rows.length ? innerW / rows.length : innerW;
  const r1 = (n: number) => Math.round(n * 10) / 10;
  const yMoney = (v: number) => size.padTop + innerH - (Math.max(0, v) / maxMinor) * innerH;
  const yQueue = (v: number) => size.padTop + innerH - (v / maxQueue) * innerH;
  const bars = rows.map((row, i) => {
    const y = yMoney(row.salesMinor);
    return { x: r1(size.padLeft + i * w + 0.5), y: r1(y), w: r1(Math.max(1, w - 1)), h: r1(size.padTop + innerH - y), row };
  });
  let queuePath = "";
  let drawing = false;
  rows.forEach((row, i) => {
    if (row.meanQueue == null) {
      drawing = false;
      return;
    }
    const x0 = r1(size.padLeft + i * w);
    const y = r1(yQueue(row.meanQueue));
    queuePath += drawing ? `V${y}H${r1(x0 + w)}` : `M${x0},${y}H${r1(x0 + w)}`;
    drawing = true;
  });
  const moneyTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ y: r1(yMoney(f * maxMinor)), value: f * maxMinor }));
  const queueTicks = [0, 0.5, 1].map((f) => ({ y: r1(yQueue(f * maxQueue)), value: f * maxQueue }));
  const hourTicks: { x: number; label: string }[] = [];
  rows.forEach((row, i) => {
    const t = localTime(row.start, tz);
    if (t.endsWith(":00") && Number(t.slice(0, 2)) % 3 === 0) hourTicks.push({ x: r1(size.padLeft + i * w), label: t });
  });
  return { bars, queuePath, moneyTicks, queueTicks, hourTicks, maxMinor, maxQueue };
}

/** One sentence for the chart's accessible name. */
export function describeDay(summary: DaySummary, rows: QuarterRow[], currency: string, tz: string): string {
  if (summary.bills === 0 && rows.every((r) => !r.visitors)) return "No sales and no visitors were recorded on this day.";
  const busiest = rows.reduce<QuarterRow | null>((a, r) => (a == null || r.salesMinor > a.salesMinor ? r : a), null);
  const peakQueue = Math.max(0, ...rows.map((r) => r.maxQueue ?? 0));
  const busy = busiest && busiest.salesMinor > 0 ? `, busiest at ${localTime(busiest.start, tz)} with ${money(busiest.salesMinor, currency)}` : "";
  return `${summary.bills} bill${summary.bills === 1 ? "" : "s"} for ${money(summary.salesMinor, currency)}${busy}; the longest queue was ${peakQueue}.`;
}

// -- X1 B5f: billing sources on the edge box ------------------------------------------------------------------------

const SOURCE_KINDS: Record<EdgeConnector["type"], string> = {
  watch: "Drop folder", sql: "Database", http: "Cloud API", tally: "TallyPrime", push: "Push API",
};

/** "40 s ago", "2 min ago", "3 h ago", "2 days ago". */
export function ago(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} days ago`;
}

export interface SourceState { label: string; tone: "ok" | "warn" | "bad" }

export function connectorState(c: EdgeConnector, agentStale: boolean): SourceState {
  if (agentStale) return { label: "Unknown (edge box silent)", tone: "bad" };
  if (c.stale) return { label: "No recent success", tone: "bad" };
  if (!c.ok) {
    const n = `${c.failures} failed attempt${c.failures === 1 ? "" : "s"}`;
    return c.failures >= 3 ? { label: `Failing (${n})`, tone: "bad" } : { label: `Retrying (${n})`, tone: "warn" };
  }
  if (c.rowErrors) return { label: `Working, ${c.rowErrors} record${c.rowErrors === 1 ? "" : "s"} need${c.rowErrors === 1 ? "s" : ""} fixing`, tone: "warn" };
  return { label: "Working", tone: "ok" };
}

export interface SourceRow { agentId: string; id: string; kind: string; state: SourceState; lastSuccess: string; events: number }

export function connectorRows(edge: EdgeHealth): SourceRow[] {
  return edge.agents.flatMap((a) => a.connectors.map((c) => ({
    agentId: a.agentId, id: c.id, kind: SOURCE_KINDS[c.type] ?? c.type, state: connectorState(c, a.stale),
    lastSuccess: c.lastSuccessAgeSeconds == null ? "not yet" : ago(c.lastSuccessAgeSeconds), events: c.events ?? 0,
  })));
}
