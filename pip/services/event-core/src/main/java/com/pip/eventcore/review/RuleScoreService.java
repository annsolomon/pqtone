package com.pip.eventcore.review;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Milestone R6: live scores per rule, accumulated over the e2e runs of each rule's current version in
 * the last {@link #WINDOW_DAYS} days (pip-scorer e2e --record writes them), with a promotion verdict.
 */
@Service
public class RuleScoreService {
    public static final int WINDOW_DAYS = 30;

    private final JdbcTemplate jdbc;

    public RuleScoreService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> scores() {
        List<Map<String, Object>> rules = new ArrayList<>();
        jdbc.query("""
                WITH latest AS (
                    SELECT DISTINCT ON (rule_id) rule_id, rule_version, mode, measured_at, sim_run_id, scenario,
                           precision_min, recall_min, min_n, min_lower_bound
                      FROM pip.rule_score
                     WHERE measured_at >= now() - make_interval(days => ?)
                     ORDER BY rule_id, measured_at DESC, score_id DESC
                )
                SELECT l.rule_id, l.rule_version, l.mode, l.measured_at, l.sim_run_id, l.scenario,
                       l.precision_min, l.recall_min, l.min_n, l.min_lower_bound,
                       count(*)::int, sum(s.tp)::int, sum(s.fp)::int, sum(s.fn)::int
                  FROM latest l
                  JOIN pip.rule_score s ON s.rule_id = l.rule_id AND s.rule_version = l.rule_version AND s.mode = l.mode
                                       AND s.measured_at >= now() - make_interval(days => ?)
                 GROUP BY l.rule_id, l.rule_version, l.mode, l.measured_at, l.sim_run_id, l.scenario,
                          l.precision_min, l.recall_min, l.min_n, l.min_lower_bound
                 ORDER BY l.mode DESC, l.rule_id""",
                rs -> {
                    String mode = rs.getString(3);
                    Integer minN = (Integer) rs.getObject(9);
                    Double minLow = (Double) rs.getObject(10);
                    RuleReadiness.Totals t = new RuleReadiness.Totals(rs.getInt(12), rs.getInt(13), rs.getInt(14),
                            rs.getDouble(7), rs.getDouble(8), minN, minLow);
                    RuleReadiness.Verdict v = RuleReadiness.assess(mode, t);
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("ruleId", rs.getString(1));
                    m.put("ruleVersion", rs.getString(2));
                    m.put("mode", mode);
                    m.put("runs", rs.getInt(11));
                    m.put("lastMeasuredAt", rs.getObject(4, OffsetDateTime.class).toInstant().toString());
                    m.put("lastRunId", rs.getString(5));
                    m.put("lastScenario", rs.getString(6));
                    m.put("tp", t.tp());
                    m.put("fp", t.fp());
                    m.put("fn", t.fn());
                    m.put("precision", v.precision());
                    m.put("recall", v.recall());
                    m.put("precisionLow", v.precisionLow());
                    m.put("recallLow", v.recallLow());
                    m.put("precisionMin", t.precisionMin());
                    m.put("recallMin", t.recallMin());
                    m.put("minN", minN);
                    m.put("minLowerBound", minLow);
                    m.put("ready", v.ready());
                    m.put("reasons", v.reasons());
                    rules.add(m);
                }, WINDOW_DAYS, WINDOW_DAYS);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("windowDays", WINDOW_DAYS);
        out.put("generatedAt", Instant.now().toString());
        out.put("rules", rules);
        return out;
    }
}
