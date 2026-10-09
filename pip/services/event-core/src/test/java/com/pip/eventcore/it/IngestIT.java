package com.pip.eventcore.it;

import com.pip.eventcore.audit.AuditService;
import com.pip.eventcore.ingest.IngestService;
import com.pip.eventcore.ingest.IngestService.Status;
import com.pip.eventcore.ingest.ValidatedEvent;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone E4: the ingest write path against a real PostgreSQL 16, as pip_app.
 * The race tests release two transactions at the same instant through a barrier, many times over.
 */
class IngestIT {
    static Db db;
    static IngestService ingest;
    static JdbcTemplate jdbc;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        ingest = Fixtures.ingest(db.app());
        jdbc = new JdbcTemplate(db.app());
    }

    @AfterAll
    static void stop() {
        if (db != null) db.close();
    }

    int rows(String table, String id) {
        String where = table.equals("pip.outbox") ? "payload::jsonb ->> 'id' = ?" : "id = ?";
        Integer n = jdbc.queryForObject("SELECT count(*) FROM " + table + " WHERE " + where, Integer.class, id);
        return n == null ? 0 : n;
    }

    List<Status> race(ValidatedEvent a, ValidatedEvent b) throws Exception {
        CyclicBarrier go = new CyclicBarrier(2);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            List<Callable<Status>> work = List.of(
                    () -> { go.await(10, TimeUnit.SECONDS); return ingest.ingest(a, "http", "it-1").status(); },
                    () -> { go.await(10, TimeUnit.SECONDS); return ingest.ingest(b, "http", "it-2").status(); });
            List<Status> out = new ArrayList<>();
            for (Future<Status> f : pool.invokeAll(work, 60, TimeUnit.SECONDS)) out.add(f.get());
            out.sort(null);
            return out;
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void twoConcurrentIdenticalInsertsGiveOneAcceptedAndOneDuplicate() throws Exception {
        for (int i = 0; i < 40; i++) {
            String id = "race-same-" + i;
            ValidatedEvent e = Fixtures.event(id, 3);
            assertEquals(List.of(Status.ACCEPTED, Status.DUPLICATE), race(e, e), "round " + i);
            assertEquals(1, rows("pip.event", id), "one event row, round " + i);
            assertEquals(1, rows("pip.event_dedup", id), "one ledger row, round " + i);
            assertEquals(1, rows("pip.outbox", id), "one outbox row, round " + i);
        }
    }

    @Test
    void twoConcurrentInsertsWithDifferentPayloadsGiveOneAcceptedAndOneConflict() throws Exception {
        for (int i = 0; i < 20; i++) {
            String id = "race-diff-" + i;
            assertEquals(List.of(Status.ACCEPTED, Status.CONFLICT),
                    race(Fixtures.event(id, 3), Fixtures.event(id, 9)), "round " + i);
            assertEquals(1, rows("pip.event", id), "the conflicting payload is never stored, round " + i);
            assertEquals(1, rows("pip.outbox", id), "and never published, round " + i);
        }
    }

    @Test
    void conflictIsRejectedAuditedAndKeepsTheFirstPayload() {
        String id = "conflict-1";
        assertEquals(Status.ACCEPTED, ingest.ingest(Fixtures.event(id, 4), "http", "it").status());
        assertEquals(Status.DUPLICATE, ingest.ingest(Fixtures.event(id, 4), "kafka", "it").status());
        assertEquals(Status.CONFLICT, ingest.ingest(Fixtures.event(id, 8), "http", "it").status());

        assertEquals(1, rows("pip.event", id));
        assertEquals(4, jdbc.queryForObject(
                "SELECT (data ->> 'length')::int FROM pip.event WHERE id = ?", Integer.class, id));
        Integer audited = jdbc.queryForObject("""
                SELECT count(*) FROM pip.audit_log
                 WHERE action = 'ingest.conflicting-duplicate' AND target = ?""", Integer.class,
                "urn:pip:edge:store-it:gw-1#" + id);
        assertEquals(1, audited);
        var verify = new AuditService(jdbc, Fixtures.MAPPER, Clock.systemUTC()).verify();
        assertEquals(true, verify.get("valid"), verify.toString());
    }

    @Test
    void theAppRoleCannotRewriteHistory() {
        String id = "append-only-1";
        ingest.ingest(Fixtures.event(id, 2), "http", "it");
        var ex = org.junit.jupiter.api.Assertions.assertThrows(Exception.class,
                () -> jdbc.update("UPDATE pip.event_dedup SET payload_sha256 = repeat('0', 64) WHERE id = ?", id));
        assertTrue(ex.getMessage().contains("permission denied") || ex.getMessage().contains("append-only"),
                ex.getMessage());
    }
}
