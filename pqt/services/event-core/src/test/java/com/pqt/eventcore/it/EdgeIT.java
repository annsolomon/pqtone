package com.pqt.eventcore.it;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.eventcore.config.PqtProperties;
import com.pqt.eventcore.edge.EdgeService;
import com.pqt.eventcore.ingest.EventValidator;
import com.pqt.eventcore.ingest.IngestService;
import com.pqt.eventcore.ingest.SchemaRegistry;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * X1 milestone B5f: the console's edge health reads the latest heartbeat per agent of one store, through the real
 * contract (edge.heartbeat 1.0.0 and 1.1.0 with billing connectors) and the real write path. Synthetic data only.
 */
class EdgeIT {
    static final Instant NOW = Instant.parse("2026-06-01T12:00:00Z");
    static Db db;
    static IngestService ingest;
    static EventValidator validator;
    static EdgeService edge;
    static int seq;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        ingest = Fixtures.ingest(db.app());
        edge = new EdgeService(new JdbcTemplate(db.app()), Fixtures.MAPPER);
        SchemaRegistry reg = new SchemaRegistry(Path.of(System.getProperty("pqt.schemas", "../../schemas")), Fixtures.MAPPER);
        validator = new EventValidator(reg, new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536,
                1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(), Map.of()), Clock.fixed(NOW, ZoneOffset.UTC));

        beat("store-it", "edge-a", "1.0.0", "2026-06-01T11:50:00Z", null);
        beat("store-it", "edge-a", "1.1.0", "2026-06-01T11:59:30Z", List.of(
                Map.of("id", "shop-db", "type", "sql", "ok", true, "stale", false, "failures", 0, "lastSuccessAgeSeconds", 20,
                        "events", 42, "rowErrors", 0),
                Map.of("id", "push", "type", "push", "ok", false, "stale", false, "failures", 4, "events", 0, "rowErrors", 0)));
        beat("store-it", "edge-b", "1.0.0", "2026-06-01T11:40:00Z", null);
        beat("store-other", "edge-c", "1.0.0", "2026-06-01T11:59:50Z", null);
    }

    @AfterAll
    static void stop() {
        db.close();
    }

    static void beat(String store, String agent, String version, String time, List<Map<String, Object>> connectors) {
        ObjectNode n = Fixtures.MAPPER.createObjectNode();
        n.put("specversion", "1.0");
        n.put("id", "it-edge-" + (++seq));
        n.put("source", "urn:pqt:edge:" + store + ":" + agent);
        n.put("type", "com.pqt.edge.heartbeat");
        n.put("time", time);
        n.put("subject", "edge:" + agent);
        n.put("dataschema", "https://schemas.pqt.local/edge/heartbeat/" + version);
        n.put("datacontenttype", "application/json");
        n.put("storeid", store);
        n.put("partitionkey", store);
        ObjectNode d = n.putObject("data");
        d.put("agentId", agent);
        d.put("agentVersion", "0.1.0");
        d.put("uptimeSeconds", 600);
        d.putArray("cameras");
        ObjectNode ob = d.putObject("outbox");
        ob.put("pending", 3).put("rejected", 0).put("bytes", 4096).put("quotaBytes", 1 << 29).put("oldestPendingAgeMs", 1200);
        d.putArray("alarms");
        if (connectors != null) {
            d.set("connectors", Fixtures.MAPPER.valueToTree(connectors));
            d.withArray("alarms").add("connector_failing");
        }
        assertEquals(IngestService.Status.ACCEPTED, ingest.ingest(validator.validate(n), "http", "it").status());
    }

    @Test
    void theLatestHeartbeatOfEachAgentOfTheStoreWithItsConnectors() {
        List<Map<String, Object>> agents = edge.agents("store-it", NOW);
        assertEquals(List.of("edge-a", "edge-b"), agents.stream().map(a -> a.get("agentId")).toList());
        Map<String, Object> a = agents.get(0);
        assertEquals("2026-06-01T11:59:30Z", a.get("at"));
        assertFalse((Boolean) a.get("stale"));
        JsonNode conns = (JsonNode) a.get("connectors");
        assertEquals(2, conns.size());
        assertEquals("shop-db", conns.get(0).path("id").asText());
        assertEquals(42, conns.get(0).path("events").asInt());
        assertFalse(conns.get(1).path("ok").asBoolean());
        assertEquals("connector_failing", ((JsonNode) a.get("alarms")).get(0).asText());
    }

    @Test
    void anAgentSilentForMoreThanTwoMinutesIsStaleAndOldHeartbeatsHaveNoConnectors() {
        Map<String, Object> b = edge.agents("store-it", NOW).get(1);
        assertTrue((Boolean) b.get("stale"));
        assertEquals(0, ((JsonNode) b.get("connectors")).size());
    }

    @Test
    void anotherStoresAgentsAreNotShownAndNothingOlderThanAWeek() {
        assertEquals(List.of("edge-c"), edge.agents("store-other", NOW).stream().map(a -> a.get("agentId")).toList());
        assertTrue(edge.agents("store-it", NOW.plus(Duration.ofDays(8))).isEmpty());
        assertTrue(edge.agents("store-none", NOW).isEmpty());
    }
}
