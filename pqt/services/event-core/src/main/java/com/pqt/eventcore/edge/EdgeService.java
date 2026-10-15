package com.pqt.eventcore.edge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;

import java.io.IOException;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Edge health for the console (X1 B5f): the latest heartbeat of each edge agent of a store in the last 7 days,
 * with its cameras, outbox, alarms and (edge.heartbeat 1.1.0) billing connector health. Operational data only.
 */
public class EdgeService {
    /** Heartbeats come every 30 s; an agent silent for longer than this is shown as stale. */
    public static final Duration STALE_AFTER = Duration.ofMinutes(2);
    static final Duration LOOK_BACK = Duration.ofDays(7);
    static final int MAX_AGENTS = 50;

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public EdgeService(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    public List<Map<String, Object>> agents(String storeId, Instant now) {
        List<Map<String, Object>> out = new ArrayList<>();
        jdbc.query("""
                SELECT DISTINCT ON (source) source, event_time, data::text
                  FROM pqt.event
                 WHERE store_id = ? AND type = 'com.pqt.edge.heartbeat' AND event_time > ?
                 ORDER BY source, event_time DESC, event_seq DESC
                 LIMIT ?""",
                rs -> {
                    Instant at = rs.getObject(2, OffsetDateTime.class).toInstant();
                    JsonNode d;
                    try {
                        d = mapper.readTree(rs.getString(3));
                    } catch (IOException e) {
                        return;
                    }
                    Map<String, Object> a = new LinkedHashMap<>();
                    a.put("agentId", d.path("agentId").asText());
                    a.put("source", rs.getString(1));
                    a.put("at", at.toString());
                    a.put("stale", Duration.between(at, now).compareTo(STALE_AFTER) > 0);
                    a.put("agentVersion", d.path("agentVersion").asText());
                    a.put("uptimeSeconds", d.path("uptimeSeconds").asLong());
                    a.put("alarms", d.path("alarms").isArray() ? d.get("alarms") : mapper.createArrayNode());
                    a.put("cameras", d.path("cameras").isArray() ? d.get("cameras") : mapper.createArrayNode());
                    a.put("outbox", d.path("outbox").isObject() ? d.get("outbox") : mapper.createObjectNode());
                    a.put("connectors", d.path("connectors").isArray() ? d.get("connectors") : mapper.createArrayNode());
                    a.put("clockOffsetMs", d.has("clockOffsetMs") ? d.get("clockOffsetMs").asLong() : null);
                    out.add(a);
                }, storeId, Timestamp.from(now.minus(LOOK_BACK)), MAX_AGENTS);
        out.sort((x, y) -> String.valueOf(x.get("agentId")).compareTo(String.valueOf(y.get("agentId"))));
        return out;
    }
}
