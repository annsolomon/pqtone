package com.pqt.eventcore;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.eventcore.config.PqtProperties;
import com.pqt.eventcore.ingest.EventValidator;
import com.pqt.eventcore.ingest.InvalidEventException;
import com.pqt.eventcore.ingest.SchemaRegistry;
import com.pqt.eventcore.ingest.ValidatedEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Runs against the real repository schemas (system property pqt.schemas set by surefire). */
class EventValidatorTest {
    final ObjectMapper m = new ObjectMapper();
    EventValidator validator;

    @BeforeEach
    void setUp() throws Exception {
        SchemaRegistry reg = new SchemaRegistry(Path.of(System.getProperty("pqt.schemas", "../../schemas")), m);
        PqtProperties.Ingest cfg = new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536,
                1048576, 100, 200, 1, "urn:pqt:sim:", Map.of("store-sim", List.of("urn:pqt:sim:")), List.of("urn:pqt:sim:"));
        validator = new EventValidator(reg, cfg, Clock.fixed(Instant.parse("2026-06-01T00:00:00Z"), ZoneOffset.UTC));
    }

    ObjectNode valid() throws Exception {
        return (ObjectNode) m.readTree("""
                {"specversion":"1.0","id":"e-1","source":"urn:pqt:sim:store-sim:store-001",
                 "type":"com.pqt.store.queue.length","time":"2026-01-01T09:00:00.000Z","subject":"queue:checkout-1",
                 "dataschema":"https://schemas.pqt.local/store/queue.length/1.0.0","datacontenttype":"application/json",
                 "storeid":"store-001","partitionkey":"store-001","simrunid":"run-0123456789ab","sequence":"0000000001",
                 "data":{"queueId":"checkout-1","length":7,"openRegisters":2}}""");
    }

    @Test
    void acceptsAValidEvent() throws Exception {
        ValidatedEvent e = validator.validate(valid());
        assertEquals("store-001", e.storeId());
        assertEquals(64, e.payloadHash().length());
    }

    @Test
    void rejectsSchemaViolationsWithAllErrors() throws Exception {
        ObjectNode n = valid();
        n.remove("time");
        ((ObjectNode) n.get("data")).put("length", "seven");
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> validator.validate(n));
        assertTrue(ex.errors().size() >= 2, ex.errors().toString());
    }

    @Test
    void rejectsUnknownTypeAndBadSubject() throws Exception {
        ObjectNode n = valid();
        n.put("type", "com.pqt.store.teleport");
        assertThrows(InvalidEventException.class, () -> validator.validate(n));
        ObjectNode s = valid();
        s.put("subject", "track:trk-1");
        assertThrows(InvalidEventException.class, () -> validator.validate(s));
    }

    @Test
    void enforcesTimeBoundsAndSimTagging() throws Exception {
        ObjectNode future = valid();
        future.put("time", "2026-06-01T00:10:00.000Z");
        assertThrows(InvalidEventException.class, () -> validator.validate(future));

        ObjectNode realOld = valid();
        realOld.put("source", "urn:pqt:edge:store-001");
        realOld.remove("simrunid");
        assertThrows(InvalidEventException.class, () -> validator.validate(realOld), "real producers cannot replay old data");

        ObjectNode untaggedSim = valid();
        untaggedSim.remove("simrunid");
        assertThrows(InvalidEventException.class, () -> validator.validate(untaggedSim));

        ObjectNode mismatchedKey = valid();
        mismatchedKey.put("partitionkey", "store-002");
        assertThrows(InvalidEventException.class, () -> validator.validate(mismatchedKey));
    }

    // Milestone E2: producers may send queue.length 1.0.0 or 1.1.0; both are registered.

    ObjectNode queueLengthV110() throws Exception {
        ObjectNode n = valid();
        n.put("dataschema", "https://schemas.pqt.local/store/queue.length/1.1.0");
        ((ObjectNode) n.get("data")).put("estimatedWaitSeconds", 240);
        return n;
    }

    @Test
    void acceptsQueueLengthVersion100() throws Exception {
        ValidatedEvent e = validator.validate(valid());
        assertEquals("https://schemas.pqt.local/store/queue.length/1.0.0", e.dataschema());
    }

    @Test
    void acceptsQueueLengthVersion110WithAndWithoutTheOptionalField() throws Exception {
        assertEquals("https://schemas.pqt.local/store/queue.length/1.1.0", validator.validate(queueLengthV110()).dataschema());
        ObjectNode without = queueLengthV110();
        ((ObjectNode) without.get("data")).remove("estimatedWaitSeconds");
        assertEquals("https://schemas.pqt.local/store/queue.length/1.1.0", validator.validate(without).dataschema());
    }

    @Test
    void version110StillValidatesTheNewField() throws Exception {
        ObjectNode negative = queueLengthV110();
        ((ObjectNode) negative.get("data")).put("estimatedWaitSeconds", -1);
        assertThrows(InvalidEventException.class, () -> validator.validate(negative));
    }

    @Test
    void version100DoesNotAcceptTheNewField() throws Exception {
        ObjectNode n = valid();
        ((ObjectNode) n.get("data")).put("estimatedWaitSeconds", 240);
        assertThrows(InvalidEventException.class, () -> validator.validate(n),
                "1.0.0 is closed (additionalProperties: false); the field needs the 1.1.0 dataschema");
    }

    @Test
    void anUnregisteredVersionIsRejected() throws Exception {
        ObjectNode n = valid();
        n.put("dataschema", "https://schemas.pqt.local/store/queue.length/1.2.0");
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> validator.validate(n));
        assertTrue(ex.errors().toString().contains("not registered"), ex.errors().toString());
    }
}
