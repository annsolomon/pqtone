package com.pip.eventcore.incident;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pip.eventcore.audit.AuditService;
import com.pip.eventcore.ingest.Canonical;
import com.pip.eventcore.stream.LiveHub;
import com.pip.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
public class IncidentService {
    private static final String COLUMNS = """
            incident_id, rule_id, rule_version, mode, store_id, sim_run_id, incident_key, subject, severity,
            summary, onset_at, detected_at, resolved_at, status::text, evidence::text, attrs::text, updated_at, version""";

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final AuditService audit;
    private final LiveHub hub;
    private final RowMapper<IncidentView> rowMapper;

    public IncidentService(JdbcTemplate jdbc, ObjectMapper mapper, AuditService audit, LiveHub hub) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.audit = audit;
        this.hub = hub;
        this.rowMapper = this::map;
    }

    // ---------------------------------------------------------------- rule output

    /** Idempotent application of a rules-engine record (OPENED inserts, RESOLVED auto-resolves). */
    @Transactional
    public void applyRuleOutput(JsonNode n) {
        UUID id = UUID.fromString(n.path("incidentId").asText());
        String kind = n.path("kind").asText();
        if ("OPENED".equals(kind)) {
            int inserted = jdbc.update("""
                    INSERT INTO pip.incident (incident_id, rule_id, rule_version, mode, store_id, sim_run_id, incident_key,
                                              subject, severity, summary, onset_at, detected_at, evidence, attrs)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
                    ON CONFLICT (incident_id) DO NOTHING""",
                    id, n.path("ruleId").asText(), n.path("ruleVersion").asText(), n.path("mode").asText(),
                    n.path("storeId").asText(), textOrNull(n, "simRunId"), n.path("key").asText(),
                    textOrNull(n, "subject"), n.path("severity").asText("medium"), n.path("summary").asText(""),
                    ts(n.path("onsetMs").asLong()), ts(n.path("detectedMs").asLong()),
                    Canonical.json(n.path("evidence").isArray() ? n.get("evidence") : mapper.createArrayNode()),
                    Canonical.json(n.path("attrs").isObject() ? n.get("attrs") : mapper.createObjectNode()));
            if (inserted == 1) find(id).ifPresent(v -> hub.incident(v, "opened"));
        } else if ("RESOLVED".equals(kind)) {
            int updated = jdbc.update("""
                    UPDATE pip.incident
                       SET resolved_at = ?,
                           status = CASE WHEN status IN ('OPEN', 'ACKNOWLEDGED') THEN 'AUTO_RESOLVED'::pip.incident_status
                                         ELSE status END,
                           version = version + 1,
                           updated_at = now()
                     WHERE incident_id = ? AND resolved_at IS NULL""",
                    ts(n.path("detectedMs").asLong()), id);
            if (updated == 1) find(id).ifPresent(v -> hub.incident(v, "resolved"));
        }
    }

    // ---------------------------------------------------------------- queries

    public Optional<IncidentView> find(UUID id) {
        List<IncidentView> l = jdbc.query("SELECT " + COLUMNS + " FROM pip.incident WHERE incident_id = ?", rowMapper, id);
        return l.stream().findFirst();
    }

    public List<IncidentView> list(String mode, Set<String> statuses, String storeId, String ruleId, int limit) {
        StringBuilder sql = new StringBuilder("SELECT " + COLUMNS + " FROM pip.incident WHERE mode = ?");
        List<Object> args = new ArrayList<>(List.of(mode));
        if (statuses != null && !statuses.isEmpty()) {
            sql.append(" AND status::text IN (").append(String.join(",", java.util.Collections.nCopies(statuses.size(), "?"))).append(")");
            args.addAll(statuses.stream().sorted().toList());
        }
        if (storeId != null) {
            sql.append(" AND store_id = ?");
            args.add(storeId);
        }
        if (ruleId != null) {
            sql.append(" AND rule_id = ?");
            args.add(ruleId);
        }
        sql.append(" ORDER BY detected_at DESC, incident_id LIMIT ?");
        args.add(Math.min(Math.max(limit, 1), 500));
        return jdbc.query(sql.toString(), rowMapper, args.toArray());
    }

    public List<Map<String, Object>> reviews(UUID id) {
        return jdbc.query("""
                SELECT action, from_status::text, to_status::text, reason_code, note, actor, acted_at
                  FROM pip.incident_review WHERE incident_id = ? ORDER BY acted_at, review_id""",
                (rs, i) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("action", rs.getString(1));
                    m.put("from", rs.getString(2));
                    m.put("to", rs.getString(3));
                    m.put("reasonCode", rs.getString(4));
                    m.put("note", rs.getString(5));
                    m.put("actor", rs.getString(6));
                    m.put("at", rs.getObject(7, OffsetDateTime.class).toInstant());
                    return m;
                }, id);
    }

    // ---------------------------------------------------------------- review

    @Transactional
    public IncidentView act(UUID id, String action, String reasonCode, String note, int expectedVersion,
                            String actor, Set<String> actorRoles) {
        if (!Transitions.isAction(action)) throw new ApiException(HttpStatus.BAD_REQUEST, "unknown-action", "unknown action " + action);
        if (!actorRoles.contains(Transitions.requiredRole(action))) {
            throw new ApiException(HttpStatus.FORBIDDEN, "insufficient-role", action + " requires role " + Transitions.requiredRole(action));
        }
        IncidentView current = find(id).orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "not-found", "incident not found"));
        if ("shadow".equals(current.mode())) {
            throw new ApiException(HttpStatus.CONFLICT, "shadow-read-only", "shadow-mode incidents are for measurement only");
        }
        if (current.version() != expectedVersion) {
            throw new ApiException(HttpStatus.PRECONDITION_FAILED, "stale-version",
                    "incident changed since you loaded it", Map.of("currentVersion", current.version()));
        }
        String next = Transitions.next(current.status(), action).orElseThrow(() -> new ApiException(HttpStatus.CONFLICT,
                "invalid-transition", "cannot " + action + " an incident in status " + current.status()));
        if ("dismiss".equals(action)) {
            if (reasonCode == null || !Transitions.DISMISS_REASONS.contains(reasonCode)) {
                throw new ApiException(HttpStatus.BAD_REQUEST, "reason-required",
                        "dismiss requires a reason code", Map.of("allowed", Transitions.DISMISS_REASONS));
            }
            if ("OTHER".equals(reasonCode) && (note == null || note.isBlank())) {
                throw new ApiException(HttpStatus.BAD_REQUEST, "note-required", "reason OTHER requires a note");
            }
        }
        if (note != null && note.length() > 500) throw new ApiException(HttpStatus.BAD_REQUEST, "note-too-long", "note exceeds 500 characters");

        int updated = jdbc.update("""
                UPDATE pip.incident SET status = ?::pip.incident_status, version = version + 1, updated_at = now()
                 WHERE incident_id = ? AND version = ?""", next, id, expectedVersion);
        if (updated == 0) {
            throw new ApiException(HttpStatus.PRECONDITION_FAILED, "stale-version", "incident changed since you loaded it");
        }
        jdbc.update("""
                INSERT INTO pip.incident_review (incident_id, action, from_status, to_status, reason_code, note, actor)
                VALUES (?, ?, ?::pip.incident_status, ?::pip.incident_status, ?, ?, ?)""",
                id, action, current.status(), next, reasonCode, note, actor);
        Map<String, Object> details = new LinkedHashMap<>();
        details.put("from", current.status());
        details.put("to", next);
        if (reasonCode != null) details.put("reasonCode", reasonCode);
        audit.record(actor, "incident." + action, id.toString(), details);
        IncidentView after = find(id).orElseThrow();
        hub.incident(after, "reviewed");
        return after;
    }

    // ---------------------------------------------------------------- mapping

    private IncidentView map(ResultSet rs, int i) throws SQLException {
        try {
            return new IncidentView(
                    rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
                    rs.getString(6), rs.getString(7), rs.getString(8), rs.getString(9), rs.getString(10),
                    instant(rs, 11), instant(rs, 12), instant(rs, 13), rs.getString(14),
                    mapper.readTree(rs.getString(15)), mapper.readTree(rs.getString(16)), instant(rs, 17), rs.getInt(18));
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new SQLException("bad json in incident row", e);
        }
    }

    private static Instant instant(ResultSet rs, int col) throws SQLException {
        OffsetDateTime t = rs.getObject(col, OffsetDateTime.class);
        return t == null ? null : t.toInstant();
    }

    private static OffsetDateTime ts(long ms) {
        return Instant.ofEpochMilli(ms).atOffset(ZoneOffset.UTC);
    }

    private static String textOrNull(JsonNode n, String f) {
        JsonNode v = n.get(f);
        return v == null || v.isNull() ? null : v.asText();
    }
}
