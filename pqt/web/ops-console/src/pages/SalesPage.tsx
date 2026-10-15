import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../api";
import {
  buildSalesChart, connectorRows, dayWindow, describeDay, joinBuckets, localDay, localTime, mainCurrency, money, quantity,
  SALES_CHART, summarize, TENDER_LABELS,
} from "../billing";
import type { EdgeHealth, Layout, Payments, Sales, Stock, StockRow, StoreState, Traffic } from "../types";

/** The stores' time zone until layouts carry their own (all demo stores are in India). */
const TZ = "Asia/Kolkata";
const TZ_NAME = "India Standard Time";
const STOCK_PAGE = 200;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

const REASONS: Record<string, string> = {
  purchase_receipt: "received", sale: "sold", customer_return: "returned by customers", supplier_return: "returned to supplier",
  transfer_in: "transferred in", transfer_out: "transferred out", adjustment: "adjusted", damage: "damaged",
};

function reasons(r: StockRow): string {
  const parts = [
    ...Object.entries(r.inByReason).map(([k, v]) => `${quantity(v, r.unit)} ${REASONS[k] ?? k}`),
    ...Object.entries(r.outByReason).map(([k, v]) => `${quantity(v, r.unit)} ${REASONS[k] ?? k}`),
  ];
  return parts.length ? parts.join(", ") : "no movement";
}

/**
 * X1 milestone B7: the billing system's sales and payments next to what the cameras saw (visitors and queue
 * length per 15 minutes), and stock movements per SKU, for one store and one day on the store's clock.
 */
export function SalesPage() {
  const [params, setParams] = useSearchParams();
  const [stores, setStores] = useState<{ storeId: string; name: string }[]>([]);
  const [state, setState] = useState<StoreState | null>(null);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [sales, setSales] = useState<Sales | null>(null);
  const [payments, setPayments] = useState<Payments | null>(null);
  const [traffic, setTraffic] = useState<Traffic | null>(null);
  const [stock, setStock] = useState<Stock | null>(null);
  const [edge, setEdge] = useState<EdgeHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const storeId = params.get("store") ?? stores[0]?.storeId ?? null;
  const simRun = state?.simRunId ?? null;
  const source = params.get("source") ?? (simRun ? "sim" : "live");
  const run = source === "sim" ? simRun : null;
  const date = params.get("date") ?? (run && state?.lastEventAt ? localDay(state.lastEventAt, TZ) : localDay(new Date().toISOString(), TZ));
  const till = params.get("till") ?? "";

  const set = useCallback((key: string, value: string | null) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });
  }, [setParams]);

  useEffect(() => {
    get<{ storeId: string; name: string }[]>("/api/stores").then(setStores).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!storeId) return;
    setState(null);
    Promise.all([
      get<StoreState>(`/api/stores/${storeId}/state`).then(setState),
      get<Layout>(`/api/stores/${storeId}/layout`).then(setLayout),
    ]).catch((e: Error) => setError(e.message));
    setEdge(null);
    // X1 B5f: how the billing sources on the store's edge box are doing (optional: the page works without it).
    get<EdgeHealth>(`/api/stores/${storeId}/edge`).then(setEdge).catch(() => setEdge(null));
  }, [storeId]);

  useEffect(() => {
    if (!storeId || !state || !DATE.test(date) || (source === "sim" && !run)) return;
    const w = dayWindow(date, TZ);
    const base = `from=${encodeURIComponent(w.from)}&to=${encodeURIComponent(w.to)}&tz=${encodeURIComponent(TZ)}${run ? `&run=${run}` : ""}`;
    const tillQ = till ? `&till=${encodeURIComponent(till)}` : "";
    let live = true;
    setLoading(true);
    setError(null);
    Promise.all([
      get<Sales>(`/api/stores/${storeId}/sales?${base}&bucket=15m${tillQ}`),
      get<Payments>(`/api/stores/${storeId}/payments?${base}&bucket=day${tillQ}`),
      get<Traffic>(`/api/stores/${storeId}/traffic?${base}&bucket=15m`),
      get<Stock>(`/api/stores/${storeId}/stock?from=${encodeURIComponent(w.from)}&to=${encodeURIComponent(w.to)}&limit=${STOCK_PAGE}${run ? `&run=${run}` : ""}`),
    ]).then(([s, p, t, st]) => {
      if (!live) return;
      setSales(s);
      setPayments(p);
      setTraffic(t);
      setStock(st);
    }).catch((e: Error) => live && setError(e.message)).finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [storeId, state, date, source, run, till]);

  const moreStock = useCallback(() => {
    if (!storeId || !stock?.next) return;
    const w = dayWindow(date, TZ);
    get<Stock>(`/api/stores/${storeId}/stock?from=${encodeURIComponent(w.from)}&to=${encodeURIComponent(w.to)}&limit=${STOCK_PAGE}`
      + `&after=${encodeURIComponent(stock.next)}${run ? `&run=${run}` : ""}`)
      .then((more) => setStock((cur) => (cur ? { ...more, skus: [...cur.skus, ...more.skus] } : more)))
      .catch((e: Error) => setError(e.message));
  }, [storeId, stock, date, run]);

  const currency = mainCurrency(sales, payments) ?? "INR";
  const rows = useMemo(() => (sales && traffic ? joinBuckets(sales, traffic, currency) : []), [sales, traffic, currency]);
  const summary = sales && payments ? summarize(sales, payments, traffic, currency) : null;
  const chart = useMemo(() => buildSalesChart(rows, TZ), [rows]);
  const busyRows = rows.filter((r) => r.bills || r.voidedBills || r.visitors || r.maxQueue);
  const tenders = (payments?.totals ?? []).filter((t) => t.currency === currency);
  const otherCurrencies = [...new Set([...(sales?.totals ?? []), ...(payments?.totals ?? [])].map((t) => t.currency))].filter((c) => c !== currency);
  const size = SALES_CHART;
  const sources = edge ? connectorRows(edge) : [];

  return (
    <main className="page sales-page">
      <div className="page-head">
        <h1>Sales and stock</h1>
      </div>
      <form className="sales-controls" onSubmit={(e) => e.preventDefault()} aria-label="What to show">
        {stores.length > 1 && (
          <div className="field">
            <label htmlFor="sales-store">Store</label>
            <select id="sales-store" value={storeId ?? ""} onChange={(e) => { set("store", e.target.value); set("till", null); }}>
              {stores.map((s) => <option key={s.storeId} value={s.storeId}>{s.name}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="sales-source">Data</label>
          <select id="sales-source" value={source} onChange={(e) => { set("source", e.target.value); set("date", null); }}>
            <option value="live">Live</option>
            {simRun && <option value="sim">Simulation {simRun}</option>}
          </select>
        </div>
        <div className="field">
          <label htmlFor="sales-day">Day</label>
          <input id="sales-day" type="date" value={date} onChange={(e) => set("date", e.target.value || null)} />
        </div>
        <div className="field">
          <label htmlFor="sales-till">Till</label>
          <select id="sales-till" value={till} onChange={(e) => set("till", e.target.value || null)}>
            <option value="">All tills</option>
            {(layout?.registers ?? []).map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}
          </select>
        </div>
      </form>
      <p className="muted small">Times in {TZ_NAME} ({TZ}). Sales come from the billing system; visitors and queues from the cameras. Bills count once, at their latest revision; voided bills are listed apart.</p>
      {error && <p className="error">Could not load the sales: {error}</p>}
      {loading && <p className="muted">Loading sales…</p>}

      {summary && (
        <section aria-labelledby="facts-h">
        <h2 id="facts-h">The day in figures</h2>
        <dl className="facts sales-facts">
          <div><dt>Sales</dt><dd data-testid="sales-total">{money(summary.salesMinor, currency)}</dd></div>
          <div><dt>Bills</dt><dd data-testid="sales-bills">{summary.bills}</dd></div>
          <div><dt>Voided bills</dt><dd>{summary.voidedBills}</dd></div>
          <div><dt>Refunds paid out</dt><dd>{money(summary.refundsMinor, currency)}</dd></div>
          <div><dt>Net received</dt><dd>{money(summary.netMinor, currency)}</dd></div>
          <div><dt>Visitors</dt><dd>{summary.visitors ?? "not counted"}</dd></div>
          <div><dt>Bills per 100 visitors</dt><dd>{summary.billsPer100 ?? "–"}</dd></div>
        </dl>
        </section>
      )}
      {otherCurrencies.length > 0 && <p className="muted small">Also sold in {otherCurrencies.join(", ")}; the figures above are in {currency}.</p>}
      {traffic?.truncated && <p className="muted small">Queue figures stop early: too many queue events in one day.</p>}

      {summary && (
        <section className="sales-chart-section" aria-labelledby="sales-chart-h">
          <h2 id="sales-chart-h">Sales and queue, every 15 minutes</h2>
          <svg className="sales-chart" viewBox={`0 0 ${size.width} ${size.height}`} role="img"
            aria-label={describeDay(summary, rows, currency, TZ)} preserveAspectRatio="xMidYMid meet">
            {chart.moneyTicks.map((t) => (
              <g key={`m${t.value}`} className="tl-grid">
                <line x1={size.padLeft} x2={size.width - size.padRight} y1={t.y} y2={t.y} />
                <text x={size.padLeft - 6} y={t.y} dy="0.32em" textAnchor="end">{money(t.value, currency).replace(/\.00$/, "")}</text>
              </g>
            ))}
            {chart.queueTicks.map((t) => (
              <text key={`q${t.value}`} className="tl-xtick" x={size.width - size.padRight + 6} y={t.y} dy="0.32em">{t.value}</text>
            ))}
            {chart.bars.map((b) => (
              <rect key={b.row.start} className="sales-bar" x={b.x} y={b.y} width={b.w} height={b.h}>
                <title>{`${localTime(b.row.start, TZ)}: ${money(b.row.salesMinor, currency)}, ${b.row.bills} bills`}</title>
              </rect>
            ))}
            {chart.queuePath && <path className="sales-queue" d={chart.queuePath} />}
            {chart.hourTicks.map((t) => (
              <text key={t.label} className="tl-xtick" x={t.x} y={size.height - 8} textAnchor="middle">{t.label}</text>
            ))}
          </svg>
          <ul className="tl-legend small" aria-hidden="true">
            <li><span className="sw sw-bar" /> Sales ({currency}, left)</li>
            <li><span className="sw sw-line" /> Mean queue length (people, right)</li>
          </ul>
          <details className="tl-table">
            <summary>Show the 15-minute figures as a table</summary>
            {busyRows.length === 0 ? <p className="empty">Nothing happened on this day.</p> : (
              <table aria-label="Sales and queue every 15 minutes">
                <thead>
                  <tr>
                    <th scope="col">From</th><th scope="col">Bills</th><th scope="col">Sales</th><th scope="col">Voided</th>
                    <th scope="col">Visitors</th><th scope="col">Bills per 100 visitors</th><th scope="col">Mean queue</th><th scope="col">Longest queue</th>
                  </tr>
                </thead>
                <tbody>
                  {busyRows.map((r) => (
                    <tr key={r.start}>
                      <td className="num">{localTime(r.start, TZ)}</td>
                      <td className="num">{r.bills}</td>
                      <td className="num">{money(r.salesMinor, currency)}</td>
                      <td className="num">{r.voidedBills}</td>
                      <td className="num">{r.visitors ?? "–"}</td>
                      <td className="num">{r.billsPer100 ?? "–"}</td>
                      <td className="num">{r.meanQueue ?? "–"}</td>
                      <td className="num">{r.maxQueue ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </details>
        </section>
      )}

      {payments && (
        <section aria-labelledby="tenders-h">
          <h2 id="tenders-h">Payments by tender</h2>
          {tenders.length === 0 ? <p className="empty">No payments on this day.</p> : (
            <div className="table-wrap">
              <table aria-label="Payments by tender">
                <thead>
                  <tr><th scope="col">Tender</th><th scope="col">Received</th><th scope="col">Refunded</th><th scope="col">Net</th><th scope="col">Payments</th></tr>
                </thead>
                <tbody>
                  {tenders.map((t) => (
                    <tr key={t.tender}>
                      <th scope="row">{TENDER_LABELS[t.tender] ?? t.tender}</th>
                      <td className="num">{money(t.inMinor, currency)}</td>
                      <td className="num">{money(t.outMinor, currency)}</td>
                      <td className="num">{money(t.netMinor, currency)}</td>
                      <td className="num">{t.paymentsIn + t.paymentsOut}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {stock && (
        <section aria-labelledby="stock-h">
          <h2 id="stock-h">Stock movements</h2>
          {stock.skus.length === 0 ? <p className="empty">No stock movements or levels for this day.</p> : (
            <>
              <div className="table-wrap">
                <table aria-label="Stock movements per SKU">
                  <thead>
                    <tr><th scope="col">SKU</th><th scope="col">In</th><th scope="col">Out</th><th scope="col">Net</th><th scope="col">What happened</th><th scope="col">Last reported level</th></tr>
                  </thead>
                  <tbody>
                    {stock.skus.map((r) => (
                      <tr key={`${r.sku}/${r.unit}`}>
                        <th scope="row"><code>{r.sku}</code></th>
                        <td className="num">{quantity(r.inMilli, r.unit)}</td>
                        <td className="num">{quantity(r.outMilli, r.unit)}</td>
                        <td className="num">{quantity(r.netMilli, r.unit)}</td>
                        <td>{reasons(r)}</td>
                        <td className="num">{r.level ? `${quantity(r.level.qtyMilli, r.unit)} at ${localDay(r.level.at, TZ) === date ? localTime(r.level.at, TZ) : localDay(r.level.at, TZ)}` : "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {stock.next && <p><button type="button" onClick={moreStock}>Show more SKUs</button></p>}
            </>
          )}
        </section>
      )}

      {edge && (
        <section aria-labelledby="sources-h">
          <h2 id="sources-h">Billing sources</h2>
          {edge.agents.length === 0 ? <p className="empty">No edge box has reported for this store in the last week.</p>
            : sources.length === 0 ? <p className="empty">The edge box reports no billing sources (files are imported by hand).</p> : (
              <div className="table-wrap">
                <table aria-label="Billing sources on the edge box">
                  <thead>
                    <tr><th scope="col">Source</th><th scope="col">Kind</th><th scope="col">Edge box</th><th scope="col">State</th><th scope="col">Last worked</th><th scope="col">Events sent</th></tr>
                  </thead>
                  <tbody>
                    {sources.map((r) => (
                      <tr key={`${r.agentId}/${r.id}`}>
                        <th scope="row"><code>{r.id}</code></th>
                        <td>{r.kind}</td>
                        <td><code>{r.agentId}</code></td>
                        <td><span className={`source-state tone-${r.state.tone}`}>{r.state.label}</span></td>
                        <td>{r.lastSuccess}</td>
                        <td className="num">{r.events}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </section>
      )}
    </main>
  );
}
