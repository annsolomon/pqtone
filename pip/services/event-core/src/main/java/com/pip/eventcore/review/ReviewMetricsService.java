package com.pip.eventcore.review;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Milestone C5: how people review what the rules raise, per rule, for admins.
 *
 * <ul>
 *   <li>Time to first action: from the incident reaching the console ({@code created_at}, wall clock) to the
 *       first review action of any kind. Wall clock on purpose: this measures people, not event time.</li>
 *   <li>Confirm rate: confirmed / (confirmed + dismissed), using each incident's latest decision, with a
 *       95% Wilson interval. Its lower bound is the conservative precision estimate for a real site.</li>
 *   <li>Dismiss reasons, and how many incidents nobody has decided yet.</li>
 * </ul>
 * Enforce incidents only (shadow incidents are never reviewed). Reviewer names are not returned.
 */
@Service
public class ReviewMetricsService {
    public static final int MIN_DAYS = 1;
    public static final int MAX_DAYS = 90;

    private final JdbcTemplate jdbc;

    public ReviewMetricsService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> metrics(int days) {
        if (days < MIN_DAYS || days > MAX_DAYS) throw new IllegalArgumentException("days must be 1..90");

        Map<String, Map<String, Integer>> reasons = new TreeMap<>();
        jdbc.query("""
                WITH inc AS (
                    SELECT incident_id, rule_id FROM pip.incident
                     WHERE mode = 'enforce' AND created_at >= now() - make_interval(days => ?)
                ), decision AS (
                    SELECT DISTINCT ON (r.incident_id) r.incident_id, r.action, r.reason_code
                      FROM pip.incident_review r JOIN inc ON inc.incident_id = r.incident_id
                     WHERE r.action IN ('confirm', 'dismiss')
                     ORDER BY r.incident_id, r.acted_at DESC, r.review_id DESC
                )
                SELECT inc.rule_id, coalesce(d.reason_code, 'UNSPECIFIED'), count(*)::int
                  FROM inc JOIN decision d ON d.incident_id = inc.incident_id
                 WHERE d.action = 'dismiss'
                 GROUP BY 1, 2
                 ORDER BY 1, 3 DESC, 2""",
                rs -> {
                    reasons.computeIfAbsent(rs.getString(1), k -> new LinkedHashMap<>()).put(rs.getString(2), rs.getInt(3));
                }, days);

        List<Map<String, Object>> rules = new ArrayList<>();
        jdbc.query("""
                WITH inc AS (
                    SELECT incident_id, rule_id, created_at FROM pip.incident
                     WHERE mode = 'enforce' AND created_at >= now() - make_interval(days => ?)
                ), first_touch AS (
                    SELECT r.incident_id, min(r.acted_at) AS first_at
                      FROM pip.incident_review r JOIN inc ON inc.incident_id = r.incident_id
                     GROUP BY r.incident_id
                ), decision AS (
                    SELECT DISTINCT ON (r.incident_id) r.incident_id, r.action
                      FROM pip.incident_review r JOIN inc ON inc.incident_id = r.incident_id
                     WHERE r.action IN ('confirm', 'dismiss')
                     ORDER BY r.incident_id, r.acted_at DESC, r.review_id DESC
                )
                SELECT inc.rule_id,
                       count(*)::int,
                       count(ft.first_at)::int,
                       count(d.incident_id)::int,
                       (count(*) FILTER (WHERE d.action = 'confirm'))::int,
                       (count(*) FILTER (WHERE d.action = 'dismiss'))::int,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM ft.first_at - inc.created_at)::float8),
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM ft.first_at - inc.created_at)::float8)
                  FROM inc
                  LEFT JOIN first_touch ft ON ft.incident_id = inc.incident_id
                  LEFT JOIN decision d ON d.incident_id = inc.incident_id
                 GROUP BY inc.rule_id
                 ORDER BY inc.rule_id""",
                rs -> {
                    String rule = rs.getString(1);
                    int confirmed = rs.getInt(5);
                    int dismissed = rs.getInt(6);
                    rules.add(row(rule, rs.getInt(2), rs.getInt(3), rs.getInt(4), confirmed, dismissed,
                            seconds(rs.getObject(7)), seconds(rs.getObject(8)), reasons.getOrDefault(rule, Map.of())));
                }, days);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("days", days);
        out.put("generatedAt", Instant.now().toString());
        out.put("rules", rules);
        return out;
    }

    static Map<String, Object> row(String rule, int incidents, int acted, int decided, int confirmed, int dismissed,
                                   Double p50, Double p90, Map<String, Integer> dismissReasons) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("ruleId", rule);
        m.put("incidents", incidents);
        m.put("acted", acted);
        m.put("decided", decided);
        m.put("confirmed", confirmed);
        m.put("dismissed", dismissed);
        m.put("undecided", incidents - decided);
        Wilson.Interval ci = Wilson.interval(confirmed, decided, Wilson.Z95);
        m.put("confirmRate", decided == 0 ? null : round4((double) confirmed / decided));
        m.put("confirmRateLow", decided == 0 ? null : round4(ci.low()));
        m.put("confirmRateHigh", decided == 0 ? null : round4(ci.high()));
        m.put("timeToActionP50Seconds", p50);
        m.put("timeToActionP90Seconds", p90);
        m.put("dismissReasons", dismissReasons);
        return m;
    }

    private static Double seconds(Object v) {
        if (v == null) return null;
        return Math.round(((Number) v).doubleValue() * 10.0) / 10.0;
    }

    private static double round4(double v) {
        return Math.round(v * 10_000.0) / 10_000.0;
    }
}
