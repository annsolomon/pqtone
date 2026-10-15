package com.pqt.eventcore.billing;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * X1 milestone B3: sales, payments and stock read models over the V7 tables, one store per read-only
 * transaction (row-level security scope). Money and quantities stay integers (minor units, thousandths).
 *
 * <p>Sales follow the reconcile's rules (B6, ADR-034) so the console and the reconcile always agree: a bill
 * counts once, at its latest revision, unless that revision was voided; its time is the first line of that
 * revision. Duplicates were already removed at ingest by (source, id).
 */
@Service
public class BillingService {

    /** What a read covers. {@code runId} null means live data; {@code tillId} null means every till. */
    public record Window(String storeId, String runId, Instant from, Instant to, Bucket bucket, ZoneId zone,
                         String tillId) {
    }

    /** Bucket widths; each bins local wall-clock time from local midnight (PostgreSQL date_bin). */
    public enum Bucket {
        QUARTER_HOUR("15m", "15 minutes"), HOUR("hour", "1 hour"), DAY("day", "1 day");

        final String label;
        final String interval;

        Bucket(String label, String interval) {
            this.label = label;
            this.interval = interval;
        }

        public String label() {
            return label;
        }
    }

    private final JdbcTemplate jdbc;
    private final TransactionTemplate readOnly;

    public BillingService(JdbcTemplate jdbc, TransactionTemplate tx) {
        this.jdbc = jdbc;
        this.readOnly = new TransactionTemplate(tx.getTransactionManager(), tx);
        this.readOnly.setReadOnly(true);
    }

    public Map<String, Object> sales(Window w) {
        List<Map<String, Object>> buckets = readOnly.execute(status -> {
            StoreScope.set(jdbc, w.storeId());
            return jdbc.query("""
                    WITH touched AS (
                        SELECT DISTINCT bill_ref FROM pqt.pos_sale_line
                         WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time >= ? AND event_time < ?
                    ), latest AS (
                        SELECT l.bill_ref, max(l.revision) AS revision
                          FROM pqt.pos_sale_line l JOIN touched t ON t.bill_ref = l.bill_ref
                         WHERE l.store_id = ? AND l.sim_run_id IS NOT DISTINCT FROM ?
                         GROUP BY l.bill_ref
                    ), bill AS (
                        SELECT l.bill_ref, min(l.event_time) AS bill_time, min(l.till_id) AS till_id, l.currency,
                               sum(l.amount_minor) AS amount, sum(coalesce(l.tax_minor, 0)) AS tax, count(*) AS lines,
                               EXISTS (SELECT 1 FROM pqt.pos_till_event v
                                        WHERE v.store_id = l.store_id AND v.sim_run_id IS NOT DISTINCT FROM l.sim_run_id
                                          AND v.kind = 'void' AND v.bill_ref = l.bill_ref AND v.revision = x.revision) AS voided
                          FROM pqt.pos_sale_line l JOIN latest x ON x.bill_ref = l.bill_ref AND x.revision = l.revision
                         WHERE l.store_id = ? AND l.sim_run_id IS NOT DISTINCT FROM ?
                         GROUP BY l.store_id, l.sim_run_id, l.bill_ref, x.revision, l.currency
                    )
                    SELECT date_bin(?::interval, bill_time AT TIME ZONE ?, timestamp '2000-01-01 00:00:00') AS bucket_start, currency,
                           (count(*) FILTER (WHERE NOT voided))::int,
                           (coalesce(sum(amount) FILTER (WHERE NOT voided), 0))::bigint,
                           (coalesce(sum(tax) FILTER (WHERE NOT voided), 0))::bigint,
                           (coalesce(sum(lines) FILTER (WHERE NOT voided), 0))::bigint,
                           (count(*) FILTER (WHERE voided))::int
                      FROM bill
                     WHERE bill_time >= ? AND bill_time < ? AND (?::text IS NULL OR till_id = ?)
                     GROUP BY 1, 2
                     ORDER BY 1, 2""",
                    (rs, i) -> {
                        Map<String, Object> m = new LinkedHashMap<>();
                        m.put("start", start(rs.getObject(1, LocalDateTime.class), w.zone()));
                        m.put("currency", rs.getString(2));
                        m.put("bills", rs.getInt(3));
                        m.put("salesMinor", rs.getLong(4));
                        m.put("taxMinor", rs.getLong(5));
                        m.put("lines", rs.getLong(6));
                        m.put("voidedBills", rs.getInt(7));
                        return m;
                    },
                    w.storeId(), w.runId(), utc(w.from()), utc(w.to()),
                    w.storeId(), w.runId(),
                    w.storeId(), w.runId(),
                    w.bucket().interval, w.zone().getId(),
                    utc(w.from()), utc(w.to()), w.tillId(), w.tillId());
        });
        Map<String, Object> out = header(w);
        out.put("tillId", w.tillId());
        out.put("buckets", buckets);
        out.put("totals", totals(buckets, List.of("currency"), List.of("bills", "salesMinor", "taxMinor", "lines", "voidedBills")));
        return out;
    }

    public Map<String, Object> payments(Window w) {
        List<Map<String, Object>> buckets = readOnly.execute(status -> {
            StoreScope.set(jdbc, w.storeId());
            return jdbc.query("""
                    SELECT date_bin(?::interval, event_time AT TIME ZONE ?, timestamp '2000-01-01 00:00:00') AS bucket_start, currency, tender,
                           (coalesce(sum(amount_minor) FILTER (WHERE direction = 'in'), 0))::bigint,
                           (coalesce(sum(amount_minor) FILTER (WHERE direction = 'out'), 0))::bigint,
                           (count(*) FILTER (WHERE direction = 'in'))::int,
                           (count(*) FILTER (WHERE direction = 'out'))::int
                      FROM pqt.pos_payment
                     WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time >= ? AND event_time < ?
                       AND (?::text IS NULL OR till_id = ?)
                     GROUP BY 1, 2, 3
                     ORDER BY 1, 2, 3""",
                    (rs, i) -> {
                        Map<String, Object> m = new LinkedHashMap<>();
                        m.put("start", start(rs.getObject(1, LocalDateTime.class), w.zone()));
                        m.put("currency", rs.getString(2));
                        m.put("tender", rs.getString(3));
                        long in = rs.getLong(4);
                        long out = rs.getLong(5);
                        m.put("inMinor", in);
                        m.put("outMinor", out);
                        m.put("netMinor", in - out);
                        m.put("paymentsIn", rs.getInt(6));
                        m.put("paymentsOut", rs.getInt(7));
                        return m;
                    },
                    w.bucket().interval, w.zone().getId(), w.storeId(), w.runId(), utc(w.from()), utc(w.to()),
                    w.tillId(), w.tillId());
        });
        Map<String, Object> out = header(w);
        out.put("tillId", w.tillId());
        out.put("buckets", buckets);
        out.put("totals", totals(buckets, List.of("currency", "tender"),
                List.of("inMinor", "outMinor", "netMinor", "paymentsIn", "paymentsOut")));
        return out;
    }

    /**
     * Stock per SKU (and unit) in the window: movements in and out by reason, and the latest reported level at or
     * before the end of the window. Paged by SKU in byte order: {@code next} is the {@code after} of the next page.
     */
    public Map<String, Object> stock(String storeId, String runId, Instant from, Instant to, String after, int limit) {
        Map<String, Object> page = readOnly.execute(status -> {
            StoreScope.set(jdbc, storeId);
            List<String> skus = jdbc.queryForList("""
                    SELECT sku FROM (
                        SELECT sku FROM pqt.stock_movement
                         WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time >= ? AND event_time < ?
                        UNION
                        SELECT sku FROM pqt.stock_level
                         WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time < ?
                    ) s
                     WHERE (?::text IS NULL OR sku COLLATE "C" > ?::text COLLATE "C")
                     ORDER BY sku COLLATE "C"
                     LIMIT ?""", String.class,
                    storeId, runId, utc(from), utc(to), storeId, runId, utc(to), after, after, limit + 1);
            String next = null;
            if (skus.size() > limit) {
                skus = skus.subList(0, limit);
                next = skus.get(limit - 1);
            }
            Map<String, Map<String, Object>> rows = new TreeMap<>();
            if (!skus.isEmpty()) {
                String first = skus.get(0);
                String last = skus.get(skus.size() - 1);
                jdbc.query("""
                        SELECT sku, unit, direction, reason, sum(qty_milli)::bigint, count(*)::int
                          FROM pqt.stock_movement
                         WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time >= ? AND event_time < ?
                           AND sku COLLATE "C" >= ?::text COLLATE "C" AND sku COLLATE "C" <= ?::text COLLATE "C"
                         GROUP BY sku, unit, direction, reason""",
                        rs -> {
                            Map<String, Object> r = row(rows, rs.getString(1), rs.getString(2));
                            String dir = rs.getString(3);
                            long qty = rs.getLong(5);
                            @SuppressWarnings("unchecked")
                            Map<String, Long> by = (Map<String, Long>) r.get(dir.equals("in") ? "inByReason" : "outByReason");
                            by.merge(rs.getString(4), qty, Long::sum);
                            r.merge(dir.equals("in") ? "inMilli" : "outMilli", qty, (a, b) -> (Long) a + (Long) b);
                            r.merge("movements", rs.getInt(6), (a, b) -> (Integer) a + (Integer) b);
                        },
                        storeId, runId, utc(from), utc(to), first, last);
                jdbc.query("""
                        SELECT DISTINCT ON (sku, unit) sku, unit, qty_milli, event_time
                          FROM pqt.stock_level
                         WHERE store_id = ? AND sim_run_id IS NOT DISTINCT FROM ? AND event_time < ?
                           AND sku COLLATE "C" >= ?::text COLLATE "C" AND sku COLLATE "C" <= ?::text COLLATE "C"
                         ORDER BY sku, unit, event_time DESC, received_at DESC, event_id DESC""",
                        rs -> {
                            Map<String, Object> level = new LinkedHashMap<>();
                            level.put("qtyMilli", rs.getLong(3));
                            level.put("at", rs.getObject(4, OffsetDateTime.class).toInstant().toString());
                            row(rows, rs.getString(1), rs.getString(2)).put("level", level);
                        },
                        storeId, runId, utc(to), first, last);
            }
            List<Map<String, Object>> list = new ArrayList<>();
            for (Map<String, Object> r : rows.values()) {
                r.put("netMilli", (Long) r.get("inMilli") - (Long) r.get("outMilli"));
                list.add(r);
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("skus", list);
            m.put("next", next);
            return m;
        });
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("storeId", storeId);
        out.put("simRunId", runId);
        out.put("from", from.toString());
        out.put("to", to.toString());
        out.putAll(page);
        return out;
    }

    /** One entry per (sku, unit), keyed so that TreeMap order is the SKU's byte order, then the unit. */
    private static Map<String, Object> row(Map<String, Map<String, Object>> rows, String sku, String unit) {
        return rows.computeIfAbsent(sku + "\u0000" + unit, k -> {
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("sku", sku);
            r.put("unit", unit);
            r.put("inMilli", 0L);
            r.put("outMilli", 0L);
            r.put("inByReason", new TreeMap<String, Long>());
            r.put("outByReason", new TreeMap<String, Long>());
            r.put("movements", 0);
            r.put("level", null);
            return r;
        });
    }

    private static Map<String, Object> header(Window w) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("storeId", w.storeId());
        out.put("simRunId", w.runId());
        out.put("from", w.from().toString());
        out.put("to", w.to().toString());
        out.put("bucket", w.bucket().label);
        out.put("timeZone", w.zone().getId());
        return out;
    }

    /** Sums of the numeric fields over all buckets, per distinct value of the key fields, in key order. */
    static List<Map<String, Object>> totals(List<Map<String, Object>> buckets, List<String> keys, List<String> sums) {
        Map<String, Map<String, Object>> by = new TreeMap<>();
        for (Map<String, Object> b : buckets) {
            StringBuilder k = new StringBuilder();
            for (String key : keys) k.append(b.get(key)).append('\u0000');
            Map<String, Object> t = by.computeIfAbsent(k.toString(), x -> {
                Map<String, Object> m = new LinkedHashMap<>();
                for (String key : keys) m.put(key, b.get(key));
                for (String s : sums) m.put(s, 0L);
                return m;
            });
            for (String s : sums) t.put(s, (Long) t.get(s) + ((Number) b.get(s)).longValue());
        }
        return new ArrayList<>(by.values());
    }

    /** A bucket's local wall-clock start, as the instant it denotes in the store's zone. */
    static String start(LocalDateTime local, ZoneId zone) {
        return local.atZone(zone).toInstant().toString();
    }

    private static OffsetDateTime utc(Instant i) {
        return i.atOffset(ZoneOffset.UTC);
    }
}
