package com.pqt.eventcore.billing;

import com.fasterxml.jackson.databind.JsonNode;
import com.pqt.eventcore.ingest.Canonical;
import com.pqt.eventcore.ingest.ValidatedEvent;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Set;

/**
 * X1 milestone B3: writes each accepted billing or stock event into its typed table (V7), inside the ingest
 * transaction, so the tables and pqt.event never disagree. Only allow-listed fields are copied (ADR-033);
 * references are already HMACs. A billing type without a projection here is kept in pqt.event only.
 *
 * <p>The tables have forced row-level security: the transaction is scoped to the event's store first.
 */
@Component
public class BillingProjector {
    static final Set<String> TYPES = Set.of("com.pqt.pos.sale.line", "com.pqt.pos.payment", "com.pqt.pos.till.event",
            "com.pqt.stock.movement", "com.pqt.stock.level", "com.pqt.pos.day.total");

    private final JdbcTemplate jdbc;

    public BillingProjector(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public static boolean handles(String type) {
        return TYPES.contains(type);
    }

    /** Must run inside the ingest transaction, after the event row is written. */
    public void project(ValidatedEvent e) {
        if (!handles(e.type())) return;
        StoreScope.set(jdbc, e.storeId());
        JsonNode d = e.node().get("data");
        OffsetDateTime t = e.time().atOffset(ZoneOffset.UTC);
        switch (e.type()) {
            case "com.pqt.pos.sale.line" -> jdbc.update("""
                    INSERT INTO pqt.pos_sale_line (source, event_id, store_id, sim_run_id, event_time, till_id, bill_ref,
                                                   revision, line_no, sku, qty_milli, unit, amount_minor, tax_minor, currency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, text(d, "tillId"), text(d, "billRef"),
                    integer(d, "revision"), integer(d, "lineNo"), text(d, "sku"), longValue(d, "qtyMilli"), text(d, "unit"),
                    longValue(d, "amountMinor"), longValue(d, "taxMinor"), text(d, "currency"));
            case "com.pqt.pos.payment" -> jdbc.update("""
                    INSERT INTO pqt.pos_payment (source, event_id, store_id, sim_run_id, event_time, till_id, bill_ref,
                                                 payment_no, tender, direction, amount_minor, currency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, text(d, "tillId"), text(d, "billRef"),
                    integer(d, "paymentNo"), text(d, "tender"), text(d, "direction"), longValue(d, "amountMinor"),
                    text(d, "currency"));
            case "com.pqt.pos.till.event" -> jdbc.update("""
                    INSERT INTO pqt.pos_till_event (source, event_id, store_id, sim_run_id, event_time, till_id, kind,
                                                    bill_ref, revision, amount_minor, currency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, text(d, "tillId"), text(d, "kind"),
                    text(d, "billRef"), integer(d, "revision"), longValue(d, "amountMinor"), text(d, "currency"));
            case "com.pqt.stock.movement" -> jdbc.update("""
                    INSERT INTO pqt.stock_movement (source, event_id, store_id, sim_run_id, event_time, sku, direction,
                                                    reason, qty_milli, unit, doc_ref, line_no)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, text(d, "sku"), text(d, "direction"),
                    text(d, "reason"), longValue(d, "qtyMilli"), text(d, "unit"), text(d, "docRef"), integer(d, "lineNo"));
            case "com.pqt.stock.level" -> jdbc.update("""
                    INSERT INTO pqt.stock_level (source, event_id, store_id, sim_run_id, event_time, sku, qty_milli, unit,
                                                 reported_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, text(d, "sku"), longValue(d, "qtyMilli"),
                    text(d, "unit"), text(d, "source"));
            case "com.pqt.pos.day.total" -> jdbc.update("""
                    INSERT INTO pqt.pos_day_total (source, event_id, store_id, sim_run_id, event_time, business_date,
                                                   sales_minor, refunds_minor, bills, payments_by_tender, currency, reported_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?)""",
                    e.source(), e.id(), e.storeId(), e.simRunId(), t, LocalDate.parse(text(d, "businessDate")),
                    longValue(d, "salesMinor"), longValue(d, "refundsMinor"), integer(d, "bills"),
                    Canonical.json(d.get("paymentsByTender")), text(d, "currency"), text(d, "source"));
            default -> throw new IllegalStateException("no projection for " + e.type());
        }
    }

    private static String text(JsonNode d, String field) {
        JsonNode v = d.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    private static Integer integer(JsonNode d, String field) {
        JsonNode v = d.get(field);
        return v == null || v.isNull() ? null : v.intValue();
    }

    private static Long longValue(JsonNode d, String field) {
        JsonNode v = d.get(field);
        return v == null || v.isNull() ? null : v.longValue();
    }
}
