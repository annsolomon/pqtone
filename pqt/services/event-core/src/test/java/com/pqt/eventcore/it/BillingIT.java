package com.pqt.eventcore.it;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.eventcore.billing.BillingService;
import com.pqt.eventcore.billing.BillingService.Bucket;
import com.pqt.eventcore.billing.BillingService.Window;
import com.pqt.eventcore.config.PqtProperties;
import com.pqt.eventcore.ingest.EventValidator;
import com.pqt.eventcore.ingest.IngestService;
import com.pqt.eventcore.ingest.SchemaRegistry;
import com.pqt.eventcore.ingest.ValidatedEvent;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * X1 milestone B3: billing and stock events go through the real contract (EventValidator with the repository
 * schemas and the application.yml ages) and the real write path into the V7 tables, as pqt_app; the read
 * models follow the reconcile's rules; row-level security keeps every store to itself. Synthetic data only:
 * references are made-up hex, no real bill numbers, no people.
 */
class BillingIT {
    static final String STORE = "store-it";
    static final String OTHER = "store-other";
    static final Instant NOW = Instant.parse("2026-06-01T00:00:00Z");
    static final ZoneId IST = ZoneId.of("Asia/Kolkata");
    static final ZoneId UTC = ZoneId.of("UTC");  // the IANA name, as the controller passes it (not the "Z" offset)

    static Db db;
    static IngestService ingest;
    static EventValidator validator;
    static JdbcTemplate app;
    static BillingService billing;
    static int seq;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        ingest = Fixtures.ingest(db.app());
        app = new JdbcTemplate(db.app());
        billing = new BillingService(app, Fixtures.tx(db.app()));
        SchemaRegistry reg = new SchemaRegistry(Path.of(System.getProperty("pqt.schemas", "../../schemas")), Fixtures.MAPPER);
        validator = new EventValidator(reg, new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536,
                1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(),
                Map.of("com.pqt.pos.", Duration.ofDays(35), "com.pqt.stock.", Duration.ofDays(35))),
                Clock.fixed(NOW, ZoneOffset.UTC));

        // One trading day, 28 May 2026 in India (UTC+05:30), three to four days before NOW.
        // B1: two lines at 10:00 IST, paid by card; later part-refunded.
        sale(1, 0, 1, "till-1", "ATTA-5KG", 2000, 10000, 476, "2026-05-28T04:30:00Z");
        sale(1, 0, 2, "till-1", "MILK-1L", 1000, 5000, 238, "2026-05-28T04:30:00.001Z");
        payment(1, 1, "till-1", "card", "in", 15000, "2026-05-28T04:30:00.002Z");
        // B2: revision 0 voided, revision 1 keeps one line; paid in cash.
        sale(2, 0, 1, "till-2", "MILK-1L", 1000, 3000, 143, "2026-05-28T05:30:00Z");
        sale(2, 0, 2, "till-2", "ATTA-5KG", 1000, 2000, 95, "2026-05-28T05:30:00.001Z");
        till("void", 2, 0, "till-2", 5000, "2026-05-28T05:30:30Z");
        sale(2, 1, 1, "till-2", "MILK-1L", 1000, 3000, 143, "2026-05-28T05:31:00Z");
        payment(2, 1, "till-2", "cash", "in", 3000, "2026-05-28T05:31:00.001Z");
        // B3: a single-line bill cancelled outright: nothing sold.
        sale(3, 0, 1, "till-1", "ATTA-5KG", 1000, 7000, 333, "2026-05-28T06:30:00Z");
        till("void", 3, 0, "till-1", 7000, "2026-05-28T06:30:10Z");
        // B4: 01:30 IST on 29 May, which is still 28 May in UTC.
        sale(4, 0, 1, "till-1", "MILK-1L", 1000, 4000, 190, "2026-05-28T20:00:00Z");
        payment(4, 1, "till-1", "upi", "in", 4000, "2026-05-28T20:00:00.001Z");
        // Refund of B1's milk at 14:30 IST: money out on card, stock back in.
        till("refund", 1, null, "till-1", 5000, "2026-05-28T09:00:00Z");
        payment(1, 2, "till-1", "card", "out", 5000, "2026-05-28T09:00:00.001Z");

        level("ATTA-5KG", 10000, "pcs", "2026-05-28T00:00:00Z");
        movement("ATTA-5KG", "out", "sale", 2000, "pcs", "2026-05-28T04:30:01Z");
        movement("ATTA-5KG", "in", "purchase_receipt", 5000, "pcs", "2026-05-28T08:00:00Z");
        level("ATTA-5KG", 13000, "pcs", "2026-05-28T23:00:00Z");
        movement("MILK-1L", "out", "sale", 1000, "pcs", "2026-05-28T05:31:01Z");
        level("rice.loose", 25000, "kg", "2026-05-20T00:00:00Z");

        dayTotal("2026-05-28", 22000, 5000, 3, Map.of("card", 10000L, "cash", 3000L, "upi", 4000L), "2026-05-28T20:30:00Z");

        // Another store's bill, the same day.
        accept(event(OTHER, "com.pqt.pos.sale.line", "pos/sale.line", "till:till-1", "2026-05-28T05:00:00Z", Map.of(
                "storeId", OTHER, "tillId", "till-1", "billRef", ref("bill", 99), "revision", 0, "lineNo", 1,
                "sku", "ATTA-5KG", "qtyMilli", 1000, "unit", "pcs", "amountMinor", 99900, "currency", "INR")));
    }

    @AfterAll
    static void stop() {
        if (db != null) db.close();
    }

    // ---- event builders: every event passes the real contract first ----

    static String ref(String kind, int n) {
        return kind + "-" + String.format("%032x", n);
    }

    static ValidatedEvent event(String store, String type, String schemaPath, String subject, String time, Map<String, Object> data) {
        ObjectNode n = Fixtures.MAPPER.createObjectNode();
        n.put("specversion", "1.0");
        n.put("id", "it-billing-" + (++seq));
        n.put("source", "urn:pqt:edge:" + store + ":pos-file");
        n.put("type", type);
        n.put("time", time);
        n.put("subject", subject);
        n.put("dataschema", "https://schemas.pqt.local/" + schemaPath + "/1.0.0");
        n.put("datacontenttype", "application/json");
        n.put("storeid", store);
        n.put("partitionkey", store);
        n.set("data", Fixtures.MAPPER.valueToTree(data));
        return validator.validate(n);
    }

    static void accept(ValidatedEvent e) {
        assertEquals(IngestService.Status.ACCEPTED, ingest.ingest(e, "http", "it").status(), e.type());
    }

    static void sale(int bill, int rev, int line, String till, String sku, long qty, long amount, long tax, String time) {
        accept(event(STORE, "com.pqt.pos.sale.line", "pos/sale.line", "till:" + till, time, Map.ofEntries(
                Map.entry("storeId", STORE), Map.entry("tillId", till), Map.entry("billRef", ref("bill", bill)),
                Map.entry("revision", rev), Map.entry("lineNo", line), Map.entry("sku", sku), Map.entry("qtyMilli", qty),
                Map.entry("unit", "pcs"), Map.entry("amountMinor", amount), Map.entry("taxMinor", tax),
                Map.entry("currency", "INR"))));
    }

    static void payment(int bill, int no, String till, String tender, String direction, long amount, String time) {
        accept(event(STORE, "com.pqt.pos.payment", "pos/payment", "till:" + till, time, Map.of(
                "storeId", STORE, "tillId", till, "billRef", ref("bill", bill), "paymentNo", no, "tender", tender,
                "direction", direction, "amountMinor", amount, "currency", "INR", "providerRef", ref("pay", bill * 10 + no))));
    }

    static void till(String kind, int bill, Integer rev, String till, long amount, String time) {
        Map<String, Object> d = new java.util.LinkedHashMap<>(Map.of("storeId", STORE, "tillId", till, "kind", kind,
                "billRef", ref("bill", bill), "amountMinor", amount, "currency", "INR"));
        if (rev != null) d.put("revision", rev);
        accept(event(STORE, "com.pqt.pos.till.event", "pos/till.event", "till:" + till, time, d));
    }

    static void movement(String sku, String direction, String reason, long qty, String unit, String time) {
        accept(event(STORE, "com.pqt.stock.movement", "stock/movement", "sku:" + sku, time, Map.of(
                "storeId", STORE, "sku", sku, "direction", direction, "reason", reason, "qtyMilli", qty, "unit", unit,
                "docRef", ref("doc", seq))));
    }

    static void level(String sku, long qty, String unit, String time) {
        accept(event(STORE, "com.pqt.stock.level", "stock/level", "sku:" + sku, time, Map.of(
                "storeId", STORE, "sku", sku, "qtyMilli", qty, "unit", unit, "source", "file")));
    }

    static void dayTotal(String date, long sales, long refunds, int bills, Map<String, Long> tenders, String time) {
        accept(event(STORE, "com.pqt.pos.day.total", "pos/day.total", "store:" + STORE, time, Map.of(
                "storeId", STORE, "businessDate", date, "salesMinor", sales, "refundsMinor", refunds, "bills", bills,
                "paymentsByTender", tenders, "currency", "INR", "source", "file")));
    }

    static Window window(String from, String to, Bucket bucket, ZoneId zone, String till) {
        return new Window(STORE, null, Instant.parse(from), Instant.parse(to), bucket, zone, till);
    }

    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> list(Map<String, Object> m, String key) {
        return (List<Map<String, Object>>) m.get(key);
    }

    // ---- the write path ----

    @Test
    void everyBillingTypeLandsInItsTableInTheSameTransactionAsTheEvent() throws Exception {
        try (Connection su = db.superuser(); Statement s = su.createStatement()) {  // the superuser bypasses RLS: counts only
            for (Map.Entry<String, Integer> e : Map.of("pos_sale_line", 7, "pos_payment", 4, "pos_till_event", 3,
                    "stock_movement", 3, "stock_level", 3, "pos_day_total", 1).entrySet()) {
                try (ResultSet rs = s.executeQuery("SELECT count(*) FROM pqt." + e.getKey() + " WHERE store_id = 'store-it'")) {
                    rs.next();
                    assertEquals((int) e.getValue(), rs.getInt(1), e.getKey());
                }
            }
            try (ResultSet rs = s.executeQuery("""
                    SELECT count(*) FROM pqt.event WHERE store_id = 'store-it' AND (type LIKE 'com.pqt.pos.%' OR type LIKE 'com.pqt.stock.%')""")) {
                rs.next();
                assertEquals(21, rs.getInt(1), "every projected row has its event");
            }
            try (ResultSet rs = s.executeQuery("SELECT DISTINCT tableoid::regclass::text FROM pqt.pos_sale_line")) {
                while (rs.next()) assertTrue(rs.getString(1).matches("pqt\\.pos_sale_line_p[0-9]{8}|pos_sale_line_p[0-9]{8}"),
                        "rows go to today's daily partition, not the default one: " + rs.getString(1));
            }
        }
    }

    @Test
    void aRetriedEventIsADuplicateAndAddsNoRow() {
        String store = "store-retry";  // its own store, so the other tests' counts do not depend on test order
        ValidatedEvent e = event(store, "com.pqt.pos.payment", "pos/payment", "till:till-9", "2026-05-25T10:00:00Z", Map.of(
                "storeId", store, "tillId", "till-9", "paymentNo", 1, "tender", "cash", "direction", "in",
                "amountMinor", 100, "currency", "INR"));
        accept(e);
        assertEquals(IngestService.Status.DUPLICATE, ingest.ingest(e, "http", "it").status());
        Integer rows = Fixtures.tx(db.app()).execute(s -> {
            app.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, store);
            return app.queryForObject("SELECT count(*)::int FROM pqt.pos_payment WHERE till_id = 'till-9'", Integer.class);
        });
        assertEquals(1, rows);
    }

    // ---- row-level security ----

    @Test
    void withoutAStoreScopeTheApplicationSeesNoBillingRow() {
        for (String t : List.of("pos_sale_line", "pos_payment", "pos_till_event", "stock_movement", "stock_level", "pos_day_total")) {
            assertEquals(0, app.queryForObject("SELECT count(*)::int FROM pqt." + t, Integer.class), t);
        }
    }

    @Test
    void aStoreScopeShowsThatStoreOnly() {
        TransactionTemplate tx = Fixtures.tx(db.app());
        List<String> stores = tx.execute(s -> {
            app.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, STORE);
            return app.queryForList("SELECT DISTINCT store_id FROM pqt.pos_sale_line", String.class);
        });
        assertEquals(List.of(STORE), stores);
        List<String> other = tx.execute(s -> {
            app.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, OTHER);
            return app.queryForList("SELECT DISTINCT store_id FROM pqt.pos_sale_line WHERE store_id IN ('store-it', 'store-other')", String.class);
        });
        assertEquals(List.of(OTHER), other, "even a query that names both stores sees only the scoped one");
        assertEquals(0, app.queryForObject("SELECT count(*)::int FROM pqt.pos_sale_line", Integer.class),
                "the scope ends with its transaction");
    }

    @Test
    void aRowForAnotherStoreCannotBeWrittenUnderAStoreScope() {
        Exception denied = assertThrows(Exception.class, () -> Fixtures.tx(db.app()).execute(s -> {
            app.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, STORE);
            return app.update("""
                    INSERT INTO pqt.pos_payment (source, event_id, store_id, event_time, till_id, payment_no, tender, direction,
                                                 amount_minor, currency)
                    VALUES ('urn:pqt:it', 'forged-1', 'store-other', now(), 'till-1', 1, 'cash', 'in', 1, 'INR')""");
        }));
        assertTrue(NestedExceptionUtils.getMostSpecificCause(denied).getMessage().contains("row-level security"),
                NestedExceptionUtils.getMostSpecificCause(denied).getMessage());
    }

    @Test
    void billingRowsAreAppendOnlyForTheApplication() {
        for (String sql : List.of("UPDATE pqt.pos_sale_line SET amount_minor = 0", "DELETE FROM pqt.pos_payment")) {
            Exception denied = assertThrows(Exception.class, () -> Fixtures.tx(db.app()).execute(s -> {
                app.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, STORE);
                return app.update(sql);
            }));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(denied).getMessage().contains("permission denied"), sql);
        }
    }

    @Test
    void theReadOnlyRoleReadsEveryStore() throws Exception {
        try (Connection c = DriverManager.getConnection(db.container.getJdbcUrl(), "pqt_read", db.readPassword)) {
            JdbcTemplate read = new JdbcTemplate(new SingleConnectionDataSource(c, true));
            assertEquals(List.of(STORE, OTHER), read.queryForList(
                    "SELECT DISTINCT store_id FROM pqt.pos_sale_line ORDER BY 1", String.class));
            Exception denied = assertThrows(Exception.class, () -> read.update("DELETE FROM pqt.pos_sale_line"));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(denied).getMessage().matches("(?s).*(read-only|permission denied).*"));
        }
    }

    // ---- partitions and retention ----

    @Test
    void partitionsAreKeptAheadAndOldOnesAreDropped() throws Exception {
        assertEquals(0, app.queryForObject("SELECT pqt.ensure_billing_partitions(7)", Integer.class),
                "the migration already created today's and the next week's partitions");
        try (Connection m = DriverManager.getConnection(db.container.getJdbcUrl(), "pqt_migrator", db.migratorPassword);
             Statement s = m.createStatement()) {
            s.execute("CREATE TABLE pqt.stock_level_p20200101 PARTITION OF pqt.stock_level "
                    + "FOR VALUES FROM ('2020-01-01 00:00+00') TO ('2020-01-02 00:00+00')");
        }
        assertEquals(1, app.queryForObject("SELECT pqt.drop_billing_partitions_older_than(30)", Integer.class));
        try (Connection su = db.superuser(); Statement s = su.createStatement();
             ResultSet rs = s.executeQuery("SELECT to_regclass('pqt.stock_level_p20200101') IS NULL")) {
            rs.next();
            assertTrue(rs.getBoolean(1));
        }
    }

    // ---- read models ----

    @Test
    void salesPerDayInTheStoresTimeZoneFollowTheReconcileRules() {
        Map<String, Object> r = billing.sales(window("2026-05-27T18:30:00Z", "2026-05-29T18:30:00Z", Bucket.DAY, IST, null));
        List<Map<String, Object>> b = list(r, "buckets");
        assertEquals(2, b.size(), b.toString());
        assertEquals("2026-05-27T18:30:00Z", b.get(0).get("start"));   // 28 May, IST
        assertEquals(2, b.get(0).get("bills"));                        // B1, and B2 at revision 1
        assertEquals(18000L, b.get(0).get("salesMinor"));              // 15000 + 3000; B3 was cancelled
        assertEquals(476L + 238L + 143L, b.get(0).get("taxMinor"));
        assertEquals(3L, b.get(0).get("lines"));
        assertEquals(1, b.get(0).get("voidedBills"));                  // B3
        assertEquals("2026-05-28T18:30:00Z", b.get(1).get("start"));   // 29 May, IST
        assertEquals(1, b.get(1).get("bills"));
        assertEquals(4000L, b.get(1).get("salesMinor"));
        Map<String, Object> total = list(r, "totals").get(0);
        assertEquals("INR", total.get("currency"));
        assertEquals(3L, total.get("bills"));
        assertEquals(22000L, total.get("salesMinor"), "the billing system's own day total says 22000");
    }

    @Test
    void theSameBillsInUtcFallOnOneDay() {
        List<Map<String, Object>> b = list(billing.sales(window("2026-05-28T00:00:00Z", "2026-05-29T00:00:00Z", Bucket.DAY,
                UTC, null)), "buckets");
        assertEquals(1, b.size());
        assertEquals("2026-05-28T00:00:00Z", b.get(0).get("start"));
        assertEquals(3, b.get(0).get("bills"));
        assertEquals(22000L, b.get(0).get("salesMinor"));
    }

    @Test
    void hourlySalesAndOneTill() {
        List<Map<String, Object>> b = list(billing.sales(window("2026-05-28T04:00:00Z", "2026-05-28T07:00:00Z", Bucket.HOUR,
                IST, null)), "buckets");
        assertEquals(List.of("2026-05-28T04:30:00Z", "2026-05-28T05:30:00Z", "2026-05-28T06:30:00Z"),
                b.stream().map(x -> x.get("start")).toList(), "10:00, 11:00 and 12:00 IST");
        assertEquals(15000L, b.get(0).get("salesMinor"));
        assertEquals(3000L, b.get(1).get("salesMinor"), "B2 counts at the time of its latest revision");
        assertEquals(0, b.get(2).get("bills"));
        assertEquals(1, b.get(2).get("voidedBills"));

        List<Map<String, Object>> till2 = list(billing.sales(window("2026-05-28T00:00:00Z", "2026-05-29T00:00:00Z", Bucket.DAY,
                UTC, "till-2")), "buckets");
        assertEquals(1, till2.size());
        assertEquals(1, till2.get(0).get("bills"));
        assertEquals(3000L, till2.get(0).get("salesMinor"));
    }

    @Test
    void quarterHourBucketsFollowTheLocalClockEvenAtAQuarterHourOffset() {
        // Nepal is UTC+05:45: card at 04:30Z is 10:15 local, cash at 05:31Z is 11:16 local -> the 11:15 bucket.
        List<Map<String, Object>> b = list(billing.payments(window("2026-05-28T04:00:00Z", "2026-05-28T06:00:00Z",
                Bucket.QUARTER_HOUR, ZoneId.of("Asia/Kathmandu"), null)), "buckets");
        assertEquals(List.of("2026-05-28T04:30:00Z", "2026-05-28T05:30:00Z"), b.stream().map(x -> x.get("start")).toList());
        assertEquals(List.of("card", "cash"), b.stream().map(x -> x.get("tender")).toList());
        List<Map<String, Object>> sales = list(billing.sales(window("2026-05-28T04:00:00Z", "2026-05-28T07:00:00Z",
                Bucket.QUARTER_HOUR, IST, null)), "buckets");
        assertEquals(List.of("2026-05-28T04:30:00Z", "2026-05-28T05:30:00Z", "2026-05-28T06:30:00Z"),
                sales.stream().map(x -> x.get("start")).toList(), "10:00, 11:00 (B2's 11:01 revision) and 12:00 IST");
    }

    @Test
    void paymentsInAndOutPerTenderAddUpToSalesMinusRefunds() {
        Map<String, Object> r = billing.payments(window("2026-05-27T18:30:00Z", "2026-05-29T18:30:00Z", Bucket.DAY, IST, null));
        List<Map<String, Object>> b = list(r, "buckets");
        assertEquals(List.of("card", "cash", "upi"), b.stream().map(x -> x.get("tender")).toList());
        Map<String, Object> card = b.get(0);
        assertEquals("2026-05-27T18:30:00Z", card.get("start"));
        assertEquals(15000L, card.get("inMinor"));
        assertEquals(5000L, card.get("outMinor"));
        assertEquals(10000L, card.get("netMinor"));
        assertEquals(1, card.get("paymentsIn"));
        assertEquals(1, card.get("paymentsOut"));
        assertEquals("2026-05-28T18:30:00Z", b.get(2).get("start"), "the UPI payment is on 29 May in India");
        long net = list(r, "totals").stream().mapToLong(t -> (Long) t.get("netMinor")).sum();
        assertEquals(22000L - 5000L, net, "net money in equals sales minus refunds");
    }

    @Test
    void stockPerSkuWithReasonsAndTheLatestLevelPagedInByteOrder() {
        Map<String, Object> p1 = billing.stock(STORE, null, Instant.parse("2026-05-28T00:00:00Z"),
                Instant.parse("2026-05-29T00:00:00Z"), null, 2);
        List<Map<String, Object>> skus = list(p1, "skus");
        assertEquals(List.of("ATTA-5KG", "MILK-1L"), skus.stream().map(x -> x.get("sku")).toList());
        Map<String, Object> atta = skus.get(0);
        assertEquals(5000L, atta.get("inMilli"));
        assertEquals(2000L, atta.get("outMilli"));
        assertEquals(3000L, atta.get("netMilli"));
        assertEquals(Map.of("purchase_receipt", 5000L), atta.get("inByReason"));
        assertEquals(Map.of("sale", 2000L), atta.get("outByReason"));
        assertEquals(Map.of("qtyMilli", 13000L, "at", "2026-05-28T23:00:00Z"), atta.get("level"));
        assertNull(skus.get(1).get("level"), "no level was ever reported for milk");
        assertEquals("MILK-1L", p1.get("next"));

        Map<String, Object> p2 = billing.stock(STORE, null, Instant.parse("2026-05-28T00:00:00Z"),
                Instant.parse("2026-05-29T00:00:00Z"), "MILK-1L", 2);
        List<Map<String, Object>> rest = list(p2, "skus");
        assertEquals(1, rest.size());
        assertEquals("rice.loose", rest.get(0).get("sku"));
        assertEquals("kg", rest.get(0).get("unit"));
        assertEquals(0, rest.get(0).get("movements"));
        assertEquals(Map.of("qtyMilli", 25000L, "at", "2026-05-20T00:00:00Z"), rest.get(0).get("level"),
                "the latest level before the window's end, even from before the window");
        assertNull(p2.get("next"));
    }

    @Test
    void anotherStoresSalesAreItsOwn() {
        Map<String, Object> r = billing.sales(new Window(OTHER, null, Instant.parse("2026-05-28T00:00:00Z"),
                Instant.parse("2026-05-29T00:00:00Z"), Bucket.DAY, UTC, null));
        assertEquals(99900L, list(r, "totals").get(0).get("salesMinor"));
        Map<String, Object> mine = billing.sales(window("2026-05-28T00:00:00Z", "2026-05-29T00:00:00Z", Bucket.DAY,
                UTC, null));
        assertEquals(22000L, list(mine, "totals").get(0).get("salesMinor"), "store-other's 99900 is not in store-it's sales");
    }

    @Test
    void aSimulatedRunIsSeparateFromLiveData() {
        Map<String, Object> r = billing.sales(new Window(STORE, "run-0123456789ab", Instant.parse("2026-05-28T00:00:00Z"),
                Instant.parse("2026-05-29T00:00:00Z"), Bucket.DAY, UTC, null));
        assertEquals(List.of(), list(r, "buckets"));
        assertEquals("run-0123456789ab", r.get("simRunId"));
    }
}
