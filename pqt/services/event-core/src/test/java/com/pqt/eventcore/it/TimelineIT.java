package com.pqt.eventcore.it;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.eventcore.incident.IncidentView;
import com.pqt.eventcore.incident.TimelineService;
import com.pqt.eventcore.ingest.Canonical;
import com.pqt.eventcore.ingest.IngestService;
import com.pqt.eventcore.ingest.ValidatedEvent;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone C4: the timeline queries against a real PostgreSQL 16, as pqt_app.
 * Proves the store, run, queue/zone and event-time filters, the carried-in starting value, and
 * that no track id leaves the database. All ids are synthetic.
 */
class TimelineIT {
    static final String STORE = "store-tl";
    static final String RUN = "run-tl";
    static final Instant ONSET = Instant.parse("2026-01-01T09:00:00Z");

    static Db db;
    static IngestService ingest;
    static TimelineService timelines;
    static int seq;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        ingest = Fixtures.ingest(db.app());
        timelines = new TimelineService(new JdbcTemplate(db.app()));

        // Before the window (onset - 10 min = 08:50): the carried-in starting value.
        queue(RUN, "checkout-1", "2026-01-01T08:45:00Z", 2, 1);
        // Inside the window.
        queue(RUN, "checkout-1", "2026-01-01T08:58:00Z", 5, 1);
        queue(RUN, "checkout-1", "2026-01-01T09:00:00Z", 7, 1);
        queue(RUN, "checkout-1", "2026-01-01T09:04:00Z", 3, 2);
        // Noise that must be filtered out: another queue, another run, live data, after the window.
        queue(RUN, "checkout-2", "2026-01-01T09:01:00Z", 9, 1);
        queue("run-other", "checkout-1", "2026-01-01T09:01:00Z", 9, 1);
        queue(null, "checkout-1", "2026-01-01T09:01:00Z", 9, 1);
        queue(RUN, "checkout-1", "2026-01-01T10:00:00Z", 9, 1);
        register(RUN, "com.pqt.store.register.opened", "2026-01-01T09:03:00Z", "reg-2");
        register("run-other", "com.pqt.store.register.opened", "2026-01-01T09:03:00Z", "reg-9");
        zoneEntered(RUN, "entrance", "2026-01-01T08:55:10Z");
        zoneEntered(RUN, "entrance", "2026-01-01T08:55:50Z");
        zoneEntered(RUN, "entrance", "2026-01-01T08:57:00Z");
        zoneEntered(RUN, "fitting-rooms", "2026-01-01T08:57:00Z");
    }

    @AfterAll
    static void stop() {
        if (db != null) db.close();
    }

    static IncidentView incident(String subject, Instant resolved, String attrs) throws Exception {
        return new IncidentView(UUID.randomUUID(), "R-QUEUE-001", "1.0.0", "enforce", STORE, RUN, "k", subject,
                "medium", "test", ONSET, ONSET.plusSeconds(60), resolved, "OPEN",
                Fixtures.MAPPER.readTree("[]"), Fixtures.MAPPER.readTree(attrs), ONSET, 1);
    }

    @Test
    @SuppressWarnings("unchecked")
    void queueTimelineHasOnlyThisQueueAndRunInsideTheWindow() throws Exception {
        Map<String, Object> t = timelines.timeline(incident("queue:checkout-1", ONSET.plusSeconds(300), "{\"threshold\":6}"));
        assertEquals("queue", t.get("kind"));
        assertEquals("checkout-1", t.get("target"));
        assertEquals(6, t.get("threshold"));
        assertEquals("2026-01-01T08:50:00Z", t.get("from"));
        assertEquals("2026-01-01T09:10:00Z", t.get("to"));
        List<Map<String, Object>> s = (List<Map<String, Object>>) t.get("series");
        assertEquals(List.of("2026-01-01T08:50:00Z", "2026-01-01T08:58:00Z", "2026-01-01T09:00:00Z", "2026-01-01T09:04:00Z"),
                s.stream().map(p -> p.get("t")).toList());
        assertEquals(List.of(2, 5, 7, 3), s.stream().map(p -> p.get("length")).toList());
        assertEquals(2, s.get(3).get("openRegisters"));
        List<Map<String, Object>> m = (List<Map<String, Object>>) t.get("markers");
        assertEquals(1, m.size());
        assertEquals("register.opened", m.get(0).get("kind"));
        assertEquals("reg-2", m.get(0).get("registerId"));
        assertFalse((Boolean) t.get("truncated"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void zoneTimelineCountsEntriesPerMinuteWithoutTrackIds() throws Exception {
        Map<String, Object> t = timelines.timeline(incident("zone:entrance", ONSET.plusSeconds(60), "{}"));
        assertEquals("zone", t.get("kind"));
        assertNull(t.get("threshold"));
        List<Map<String, Object>> s = (List<Map<String, Object>>) t.get("series");
        assertEquals(16, s.size(), "08:50 .. 09:05 in one-minute buckets");
        Map<String, Integer> byMinute = new java.util.HashMap<>();
        s.forEach(p -> byMinute.put((String) p.get("t"), (Integer) p.get("entries")));
        assertEquals(2, byMinute.get("2026-01-01T08:55:00Z"));
        assertEquals(1, byMinute.get("2026-01-01T08:57:00Z"), "fitting-rooms entries are not counted for entrance");
        assertEquals(0, byMinute.get("2026-01-01T08:56:00Z"));
        String json = Fixtures.MAPPER.writeValueAsString(t);
        assertFalse(json.contains("trk-"), "no track ids in a timeline");
    }

    @Test
    @SuppressWarnings("unchecked")
    void unknownSubjectGivesAnEmptyTimeline() throws Exception {
        Map<String, Object> t = timelines.timeline(incident(null, null, "{}"));
        assertEquals("none", t.get("kind"));
        assertTrue(((List<Object>) t.get("series")).isEmpty());
    }

    // ----------------------------------------------------------------- fixtures

    static void queue(String run, String queueId, String time, int length, int open) throws Exception {
        put("com.pqt.store.queue.length", "queue:" + queueId, "https://schemas.pqt.local/store/queue.length/1.0.0", run, time,
                "{\"queueId\":\"" + queueId + "\",\"length\":" + length + ",\"openRegisters\":" + open + "}");
    }

    static void register(String run, String type, String time, String registerId) throws Exception {
        String name = type.substring(type.lastIndexOf("register."));
        put(type, "register:" + registerId, "https://schemas.pqt.local/store/" + name + "/1.0.0", run, time,
                "{\"registerId\":\"" + registerId + "\"}");
    }

    static void zoneEntered(String run, String zoneId, String time) throws Exception {
        String track = "trk-" + (++seq);
        put("com.pqt.store.zone.entered", "track:" + track, "https://schemas.pqt.local/store/zone.entered/1.0.0", run, time,
                "{\"zoneId\":\"" + zoneId + "\",\"trackId\":\"" + track + "\"}");
    }

    static void put(String type, String subject, String dataschema, String run, String time, String data) throws Exception {
        String id = "tl-" + (++seq);
        ObjectNode n = Fixtures.MAPPER.createObjectNode();
        n.put("specversion", "1.0");
        n.put("id", id);
        n.put("source", "urn:pqt:sim:" + STORE);
        n.put("type", type);
        n.put("time", time);
        n.put("subject", subject);
        n.put("dataschema", dataschema);
        n.put("datacontenttype", "application/json");
        n.put("storeid", STORE);
        n.put("partitionkey", STORE);
        if (run != null) n.put("simrunid", run);
        JsonNode d = Fixtures.MAPPER.readTree(data);
        n.set("data", d);
        ingest.ingest(new ValidatedEvent(id, n.get("source").asText(), type, Instant.parse(time), subject, dataschema,
                STORE, run, null, n, Canonical.eventHash(n)), "http", "it-tl");
    }
}
