package com.pip.eventcore.it;

import com.pip.eventcore.ingest.IngestService;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone E3: every migration applies cleanly to an empty database, and V5 applies cleanly to a
 * database that already ran V1-V4 and holds data, without touching that data. Same roles and
 * Flyway user as production.
 */
class MigrationsIT {

    static String latest(Db db) {
        return Flyway.configure().dataSource(db.container.getJdbcUrl(), "pip_migrator", db.migratorPassword)
                .schemas("pip").locations("classpath:db/migration").load().info().current().getVersion().getVersion();
    }

    static void assertV5Shape(JdbcTemplate su) {
        assertEquals("YES", su.queryForObject("""
                SELECT is_nullable FROM information_schema.columns
                 WHERE table_schema = 'pip' AND table_name = 'incident' AND column_name = 'assignee'""", String.class));
        assertEquals(Boolean.TRUE, su.queryForObject("""
                SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                 WHERE c.relname = 'incident_assignee_idx'""", Boolean.class), "the concurrent index must be valid");
        assertEquals(Boolean.TRUE, su.queryForObject(
                "SELECT has_column_privilege('pip_app', 'pip.incident', 'assignee', 'UPDATE')", Boolean.class));
        assertEquals(Boolean.FALSE, su.queryForObject(
                "SELECT has_column_privilege('pip_app', 'pip.incident', 'rule_id', 'UPDATE')", Boolean.class),
                "V5 grants only the new column");
    }

    @Test
    void anEmptyDatabaseMigratesToTheLatestVersion() throws Exception {
        try (Db db = new Db().start()) {
            db.migrate();
            assertEquals("5", latest(db));
            JdbcTemplate su = new JdbcTemplate(new org.springframework.jdbc.datasource.SingleConnectionDataSource(
                    db.container.getJdbcUrl(), db.container.getUsername(), db.container.getPassword(), true));
            assertV5Shape(su);
            db.migrate();   // running again is a no-op
            assertEquals("5", latest(db));
        }
    }

    @Test
    void aDatabaseAtV4WithDataMigratesToV5AndKeepsItsData() throws Exception {
        try (Db db = new Db().start()) {
            db.migrate("4");
            assertEquals("4", latest(db));

            // Data written by the V4-era application, as pip_app.
            IngestService ingest = Fixtures.ingest(db.app());
            for (int i = 0; i < 25; i++) ingest.ingest(Fixtures.event("pre-v5-" + i, i), "http", "it");
            JdbcTemplate app = new JdbcTemplate(db.app());
            UUID incident = UUID.randomUUID();
            app.update("""
                    INSERT INTO pip.incident (incident_id, rule_id, rule_version, mode, store_id, incident_key,
                                              severity, summary, onset_at, detected_at)
                    VALUES (?, 'R-QUEUE-001', '1.0.0', 'enforce', 'store-it', 'checkout-1', 'medium', 'pre-V5',
                            now() - interval '5 minutes', now())""", incident);
            List<Integer> before = app.queryForList("SELECT count(*)::int FROM pip.event", Integer.class);

            db.migrate();
            assertEquals("5", latest(db));

            JdbcTemplate su = new JdbcTemplate(new org.springframework.jdbc.datasource.SingleConnectionDataSource(
                    db.container.getJdbcUrl(), db.container.getUsername(), db.container.getPassword(), true));
            assertV5Shape(su);
            assertEquals(before, app.queryForList("SELECT count(*)::int FROM pip.event", Integer.class), "events untouched");
            assertNull(app.queryForObject("SELECT assignee FROM pip.incident WHERE incident_id = ?", String.class, incident),
                    "existing incidents are unassigned");

            // The application role can now assign, and still cannot touch anything else.
            assertEquals(1, app.update("UPDATE pip.incident SET assignee = 'reviewer' WHERE incident_id = ?", incident));
            Exception denied = assertThrows(Exception.class,
                    () -> app.update("UPDATE pip.incident SET summary = 'x' WHERE incident_id = ?", incident));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(denied).getMessage().contains("permission denied"));
            Exception tooLong = assertThrows(Exception.class,
                    () -> app.update("UPDATE pip.incident SET assignee = repeat('x', 129) WHERE incident_id = ?", incident));
            assertTrue(NestedExceptionUtils.getMostSpecificCause(tooLong).getMessage().contains("check constraint"));

            // The V4-era write path keeps working after the expand step.
            assertEquals(IngestService.Status.ACCEPTED, ingest.ingest(Fixtures.event("post-v5", 1), "http", "it").status());
        }
    }
}
