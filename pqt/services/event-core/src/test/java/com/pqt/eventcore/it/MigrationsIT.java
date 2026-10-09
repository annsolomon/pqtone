package com.pqt.eventcore.it;

import com.pqt.eventcore.ingest.IngestService;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone E3: every migration applies cleanly to an empty database, and V5 applies cleanly to a
 * database that already ran V1-V4 and holds data, without touching that data. Same roles and
 * Flyway user as production.
 *
 * <p>The timeout turns a blocked migration (e.g. CREATE INDEX CONCURRENTLY waiting on an open
 * transaction) into a failure instead of a CI job that hangs until GitHub kills it.
 */
@Timeout(value = 5, unit = TimeUnit.MINUTES)
class MigrationsIT {
    /** The newest migration in db/migration. A new migration changes this on purpose (and adds its shape check). */
    static final String LATEST = "6";

    static String latest(Db db) {
        return Flyway.configure().dataSource(db.container.getJdbcUrl(), "pqt_migrator", db.migratorPassword)
                .schemas("pqt").locations("classpath:db/migration").load().info().current().getVersion().getVersion();
    }

    static void assertV5Shape(JdbcTemplate su) {
        assertEquals("YES", su.queryForObject("""
                SELECT is_nullable FROM information_schema.columns
                 WHERE table_schema = 'pqt' AND table_name = 'incident' AND column_name = 'assignee'""", String.class));
        assertEquals(Boolean.TRUE, su.queryForObject("""
                SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                 WHERE c.relname = 'incident_assignee_idx'""", Boolean.class), "the concurrent index must be valid");
        assertEquals(Boolean.TRUE, su.queryForObject(
                "SELECT has_column_privilege('pqt_app', 'pqt.incident', 'assignee', 'UPDATE')", Boolean.class));
        assertEquals(Boolean.FALSE, su.queryForObject(
                "SELECT has_column_privilege('pqt_app', 'pqt.incident', 'rule_id', 'UPDATE')", Boolean.class),
                "V5 grants only the new column");
    }

    /** Milestone R6: pqt.rule_score is append-only and pqt_app may only insert and read it. */
    static void assertV6Shape(JdbcTemplate su) {
        assertEquals(Boolean.TRUE, su.queryForObject("SELECT has_table_privilege('pqt_app', 'pqt.rule_score', 'INSERT')", Boolean.class));
        assertEquals(Boolean.TRUE, su.queryForObject("SELECT has_table_privilege('pqt_app', 'pqt.rule_score', 'SELECT')", Boolean.class));
        assertEquals(Boolean.FALSE, su.queryForObject("SELECT has_table_privilege('pqt_app', 'pqt.rule_score', 'UPDATE')", Boolean.class));
        assertEquals(Boolean.FALSE, su.queryForObject("SELECT has_table_privilege('pqt_app', 'pqt.rule_score', 'DELETE')", Boolean.class));
        assertEquals(Boolean.TRUE, su.queryForObject("SELECT has_table_privilege('pqt_read', 'pqt.rule_score', 'SELECT')", Boolean.class));
        assertEquals(2, su.queryForObject("""
                SELECT count(*)::int FROM pg_trigger WHERE tgrelid = 'pqt.rule_score'::regclass
                   AND tgname IN ('rule_score_append_only', 'rule_score_no_truncate')""", Integer.class));
    }

    @Test
    void anEmptyDatabaseMigratesToTheLatestVersion() throws Exception {
        try (Db db = new Db().start()) {
            db.migrate();
            assertEquals(LATEST, latest(db));
            JdbcTemplate su = new JdbcTemplate(new org.springframework.jdbc.datasource.SingleConnectionDataSource(
                    db.container.getJdbcUrl(), db.container.getUsername(), db.container.getPassword(), true));
            assertV5Shape(su);
            assertV6Shape(su);
            db.migrate();   // running again is a no-op
            assertEquals(LATEST, latest(db));
        }
    }

    @Test
    void aDatabaseAtV4WithDataMigratesToV5AndKeepsItsData() throws Exception {
        try (Db db = new Db().start()) {
            db.migrate("4");
            assertEquals("4", latest(db));

            // Data written by the V4-era application, as pqt_app.
            IngestService ingest = Fixtures.ingest(db.app());
            for (int i = 0; i < 25; i++) ingest.ingest(Fixtures.event("pre-v5-" + i, i), "http", "it");
            JdbcTemplate app = new JdbcTemplate(db.app());
            UUID incident = UUID.randomUUID();
            app.update("""
                    INSERT INTO pqt.incident (incident_id, rule_id, rule_version, mode, store_id, incident_key,
                                              severity, summary, onset_at, detected_at)
                    VALUES (?, 'R-QUEUE-001', '1.0.0', 'enforce', 'store-it', 'checkout-1', 'medium', 'pre-V5',
                            now() - interval '5 minutes', now())""", incident);
            List<Integer> before = app.queryForList("SELECT count(*)::int FROM pqt.event", Integer.class);

            db.migrate();
            assertEquals(LATEST, latest(db));

            JdbcTemplate su = new JdbcTemplate(new org.springframework.jdbc.datasource.SingleConnectionDataSource(
                    db.container.getJdbcUrl(), db.container.getUsername(), db.container.getPassword(), true));
            assertV5Shape(su);
            assertV6Shape(su);
            assertEquals(before, app.queryForList("SELECT count(*)::int FROM pqt.event", Integer.class), "events untouched");
            assertNull(app.queryForObject("SELECT assignee FROM pqt.incident WHERE incident_id = ?", String.class, incident),
                    "existing incidents are unassigned");

            // The application role can now assign, and still cannot touch anything else.
            assertEquals(1, app.update("UPDATE pqt.incident SET assignee = 'reviewer' WHERE incident_id = ?", incident));
            Exception denied = assertThrows(Exception.class,
                    () -> app.update("UPDATE pqt.incident SET summary = 'x' WHERE incident_id = ?", incident));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(denied).getMessage().contains("permission denied"));
            Exception tooLong = assertThrows(Exception.class,
                    () -> app.update("UPDATE pqt.incident SET assignee = repeat('x', 129) WHERE incident_id = ?", incident));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(tooLong).getMessage().contains("check constraint"));

            // The V4-era write path keeps working after the expand step.
            assertEquals(IngestService.Status.ACCEPTED, ingest.ingest(Fixtures.event("post-v5", 1), "http", "it").status());
        }
    }
}
