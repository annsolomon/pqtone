package com.pip.eventcore.audit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pip.eventcore.ingest.Canonical;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Append-only, hash-chained audit log. Appends are serialised with a transaction-scoped
 * advisory lock so the chain never forks; verify() recomputes the whole chain.
 */
@Service
public class AuditService {
    public static final String GENESIS = "0".repeat(64);
    private static final long LOCK_KEY = 0x50495041554449L; // "PIPAUDI"

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final Clock clock;

    public AuditService(JdbcTemplate jdbc, ObjectMapper mapper, Clock clock) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.clock = clock;
    }

    @Transactional(propagation = Propagation.REQUIRED)
    public void record(String actor, String action, String target, Map<String, ?> details) {
        jdbc.query("SELECT pg_advisory_xact_lock(?)", rs -> null, LOCK_KEY);
        String prev = jdbc.query("SELECT row_hash FROM pip.audit_log ORDER BY seq DESC LIMIT 1",
                rs -> rs.next() ? rs.getString(1) : GENESIS);
        Instant at = clock.instant().truncatedTo(ChronoUnit.MICROS);
        JsonNode detailsNode = mapper.valueToTree(details == null ? Map.of() : details);
        String hash = hash(prev, at, actor, action, target, detailsNode);
        jdbc.update("""
                INSERT INTO pip.audit_log (at, actor, action, target, details, prev_hash, row_hash)
                VALUES (?, ?, ?, ?, ?::jsonb, ?, ?)""",
                at.atOffset(ZoneOffset.UTC), actor, action, target, Canonical.json(detailsNode), prev, hash);
    }

    String hash(String prev, Instant at, String actor, String action, String target, JsonNode details) {
        ObjectNode n = mapper.createObjectNode();
        n.put("prev", prev);
        n.put("at", at.getEpochSecond() * 1_000_000L + at.getNano() / 1_000L);
        n.put("actor", actor);
        n.put("action", action);
        n.put("target", target);
        n.set("details", details);
        return Canonical.sha256Hex(Canonical.json(n));
    }

    public Map<String, Object> verify() {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT seq, at, actor, action, target, details::text, prev_hash, row_hash FROM pip.audit_log ORDER BY seq",
                (rs, i) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("seq", rs.getLong(1));
                    m.put("at", rs.getObject(2, OffsetDateTime.class).toInstant());
                    m.put("actor", rs.getString(3));
                    m.put("action", rs.getString(4));
                    m.put("target", rs.getString(5));
                    m.put("details", rs.getString(6));
                    m.put("prev", rs.getString(7));
                    m.put("hash", rs.getString(8));
                    return m;
                });
        String expectedPrev = GENESIS;
        for (Map<String, Object> r : rows) {
            try {
                String recomputed = hash((String) r.get("prev"), (Instant) r.get("at"), (String) r.get("actor"),
                        (String) r.get("action"), (String) r.get("target"), mapper.readTree((String) r.get("details")));
                if (!expectedPrev.equals(r.get("prev")) || !recomputed.equals(r.get("hash"))) {
                    return Map.of("valid", false, "rows", rows.size(), "brokenAtSeq", r.get("seq"));
                }
            } catch (Exception ex) {
                return Map.of("valid", false, "rows", rows.size(), "brokenAtSeq", r.get("seq"));
            }
            expectedPrev = (String) r.get("hash");
        }
        return Map.of("valid", true, "rows", rows.size());
    }

    public List<Map<String, Object>> recent(int limit) {
        return jdbc.queryForList("""
                SELECT seq, at, actor, action, target, details::text AS details, row_hash
                  FROM pip.audit_log ORDER BY seq DESC LIMIT ?""", Math.min(Math.max(limit, 1), 500));
    }
}
