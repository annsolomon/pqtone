package com.pip.eventcore.it;

import com.pip.eventcore.review.RuleScoreService;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone R6: rule scores against a real PostgreSQL 16 at the latest migration, as pip_app.
 * Accumulates the current version only, ignores rows older than the window, keeps the table
 * append-only and makes re-recording a run a no-op.
 */
class RuleScoreIT {
    static Db db;
    static JdbcTemplate jdbc;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        jdbc = new JdbcTemplate(db.app());
        // R-ABS-001 v1.0.0: 25 runs with one true positive each -> 25 samples, perfect.
        for (int i = 0; i < 25; i++) insert("run-" + i, "R-ABS-001", "1.0.0", "shadow", 1, 0, 0, "now() - interval '1 day'");
        // An older version of the same rule must not count once a new version is measured...
        insert("run-old", "R-DWELL-001", "1.0.0", "shadow", 0, 9, 9, "now() - interval '2 days'");
        insert("run-new", "R-DWELL-001", "1.1.0", "shadow", 2, 0, 0, "now() - interval '1 hour'");
        // ...and rows outside the 30-day window never count.
        insert("run-ancient", "R-QUEUE-001", "1.0.0", "enforce", 0, 50, 50, "now() - interval '40 days'");
        insert("run-q", "R-QUEUE-001", "1.0.0", "enforce", 3, 0, 0, "now() - interval '1 hour'");
    }

    @AfterAll
    static void stop() {
        if (db != null) db.close();
    }

    static void insert(String run, String rule, String version, String mode, int tp, int fp, int fn, String at) {
        // `at` is one of the fixed SQL interval literals above, never input.
        jdbc.update("INSERT INTO pip.rule_score (measured_at, source, sim_run_id, scenario, rule_id, rule_version, mode, tp, fp, fn, "
                + "precision_min, recall_min, min_n, min_lower_bound) VALUES (" + at + ", 'e2e', ?, 'register_delay', ?, ?, ?, ?, ?, ?, "
                + "0.90, 0.95, 20, 0.80) ON CONFLICT (sim_run_id, rule_id) DO NOTHING", run, rule, version, mode, tp, fp, fn);
    }

    @Test
    @SuppressWarnings("unchecked")
    void scoresAccumulateTheCurrentVersionInsideTheWindow() {
        Map<String, Map<String, Object>> by = new java.util.HashMap<>();
        for (Map<String, Object> r : (List<Map<String, Object>>) new RuleScoreService(jdbc).scores().get("rules")) {
            by.put((String) r.get("ruleId"), r);
        }
        Map<String, Object> abs = by.get("R-ABS-001");
        assertEquals(25, abs.get("runs"));
        assertEquals(25, abs.get("tp"));
        assertEquals(true, abs.get("ready"), String.valueOf(abs.get("reasons")));

        Map<String, Object> dwell = by.get("R-DWELL-001");
        assertEquals("1.1.0", dwell.get("ruleVersion"));
        assertEquals(1, dwell.get("runs"));
        assertEquals(0, dwell.get("fp"), "the 1.0.0 rows are not mixed in");
        assertEquals(false, dwell.get("ready"));

        Map<String, Object> q = by.get("R-QUEUE-001");
        assertEquals(1, q.get("runs"), "the 40-day-old row is outside the window");
        assertEquals(List.of("already enforce", "only 3 samples; need 20"), q.get("reasons"));
    }

    @Test
    void reRecordingARunInsertsNothingAndRowsAreAppendOnly() {
        insert("run-q", "R-QUEUE-001", "1.0.0", "enforce", 99, 99, 99, "now()");
        assertEquals(1, jdbc.queryForObject("SELECT count(*) FROM pip.rule_score WHERE sim_run_id = 'run-q'", Integer.class));
        assertThrows(DataAccessException.class, () -> jdbc.update("UPDATE pip.rule_score SET tp = 0"));
        assertThrows(DataAccessException.class, () -> jdbc.update("DELETE FROM pip.rule_score"));
        assertTrue(jdbc.queryForObject("SELECT count(*) FROM pip.rule_score", Integer.class) > 0);
        assertFalse(jdbc.queryForList("SELECT tp FROM pip.rule_score WHERE sim_run_id = 'run-q'", Integer.class).contains(99));
    }
}
