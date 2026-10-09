package com.pqt.eventcore.it;

import com.pqt.eventcore.review.ReviewMetricsService;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * Milestone C5: review metrics against a real PostgreSQL 16, as pqt_app.
 * Proves the period filter, enforce-only, first-action timing, latest decision and dismiss reasons.
 * Synthetic incidents and reviewer names only.
 */
class ReviewMetricsIT {
    static Db db;
    static JdbcTemplate jdbc;
    static ReviewMetricsService metrics;
    static final Instant NOW = Instant.now();

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        jdbc = new JdbcTemplate(db.app());
        metrics = new ReviewMetricsService(jdbc);

        Instant t0 = NOW.minus(Duration.ofHours(1));
        UUID a = incident("R-QUEUE-001", "enforce", t0, "CONFIRMED");
        review(a, "ack", "OPEN", "ACKNOWLEDGED", null, t0.plusSeconds(30));
        review(a, "confirm", "ACKNOWLEDGED", "CONFIRMED", null, t0.plusSeconds(200));
        UUID b = incident("R-QUEUE-001", "enforce", t0, "CONFIRMED");
        review(b, "confirm", "OPEN", "CONFIRMED", null, t0.plusSeconds(90));
        UUID c = incident("R-QUEUE-001", "enforce", t0, "DISMISSED");
        review(c, "dismiss", "OPEN", "DISMISSED", "FALSE_POSITIVE", t0.plusSeconds(150));
        incident("R-QUEUE-001", "enforce", t0, "OPEN");
        // Outside the 30-day period, and a shadow incident: neither counts.
        UUID old = incident("R-QUEUE-001", "enforce", NOW.minus(Duration.ofDays(100)), "CONFIRMED");
        review(old, "confirm", "OPEN", "CONFIRMED", null, NOW.minus(Duration.ofDays(100)).plusSeconds(5));
        incident("R-QUEUE-001", "shadow", t0, "OPEN");
        incident("R-DWELL-001", "enforce", t0, "OPEN");
    }

    @AfterAll
    static void stop() {
        if (db != null) db.close();
    }

    @Test
    @SuppressWarnings("unchecked")
    void perRuleCountsTimingAndConfirmRate() {
        Map<String, Object> m = metrics.metrics(30);
        assertEquals(30, m.get("days"));
        List<Map<String, Object>> rules = (List<Map<String, Object>>) m.get("rules");
        assertEquals(List.of("R-DWELL-001", "R-QUEUE-001"), rules.stream().map(r -> r.get("ruleId")).toList());

        Map<String, Object> q = rules.get(1);
        assertEquals(4, q.get("incidents"));
        assertEquals(3, q.get("acted"));
        assertEquals(3, q.get("decided"));
        assertEquals(2, q.get("confirmed"));
        assertEquals(1, q.get("dismissed"));
        assertEquals(1, q.get("undecided"));
        assertEquals(0.6667, (Double) q.get("confirmRate"), 1e-9);
        // first actions at 30 s (an ack), 90 s and 150 s after the incident reached the console
        assertEquals(90.0, (Double) q.get("timeToActionP50Seconds"), 0.11);
        assertEquals(138.0, (Double) q.get("timeToActionP90Seconds"), 0.11);
        assertEquals(Map.of("FALSE_POSITIVE", 1), q.get("dismissReasons"));

        Map<String, Object> d = rules.get(0);
        assertEquals(1, d.get("incidents"));
        assertNull(d.get("confirmRate"));
        assertNull(d.get("timeToActionP50Seconds"));
    }

    @Test
    void reviewerNamesAreNotReturned() throws Exception {
        String json = Fixtures.MAPPER.writeValueAsString(metrics.metrics(90));
        assertFalse(json.contains("it-reviewer"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void ninetyDaysStillExcludesTheHundredDayOldIncident() {
        List<Map<String, Object>> rules = (List<Map<String, Object>>) metrics.metrics(90).get("rules");
        assertEquals(4, rules.get(1).get("incidents"));
    }

    // ----------------------------------------------------------------- fixtures

    static UUID incident(String rule, String mode, Instant created, String status) {
        UUID id = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO pqt.incident (incident_id, rule_id, rule_version, mode, store_id, sim_run_id, incident_key, subject,
                                          severity, summary, onset_at, detected_at, status, created_at, updated_at)
                VALUES (?, ?, '1.0.0', ?, 'store-it', 'run-it', ?, 'queue:checkout-1', 'medium', 'it', ?, ?,
                        ?::pqt.incident_status, ?, ?)""",
                id, rule, mode, id.toString(), ts(created), ts(created), status, ts(created), ts(created));
        return id;
    }

    static void review(UUID id, String action, String from, String to, String reason, Instant at) {
        jdbc.update("""
                INSERT INTO pqt.incident_review (incident_id, action, from_status, to_status, reason_code, actor, acted_at)
                VALUES (?, ?, ?::pqt.incident_status, ?::pqt.incident_status, ?, 'it-reviewer', ?)""",
                id, action, from, to, reason, ts(at));
    }

    static OffsetDateTime ts(Instant i) {
        return i.atOffset(ZoneOffset.UTC);
    }
}
