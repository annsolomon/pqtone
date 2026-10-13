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

    // X1 milestone B1: the billing contracts are registered; every sample is accepted, a float amount is not.

    static final List<String> BILLING = List.of("pos.sale.line", "pos.payment", "pos.till.event", "stock.movement",
            "stock.level", "pos.day.total");

    ObjectNode billingSample(String shortType) throws Exception {
        Path dir = Path.of(System.getProperty("pqt.schemas", "../../schemas")).resolve("samples");
        ObjectNode n = (ObjectNode) m.readTree(java.nio.file.Files.readString(dir.resolve(shortType + ".json")));
        n.put("time", "2026-05-31T23:30:00.000Z");  // inside the real-producer age window of the fixed clock
        return n;
    }

    @Test
    void acceptsEveryBillingSample() throws Exception {
        for (String t : BILLING) {
            ValidatedEvent e = validator.validate(billingSample(t));
            assertEquals("com.pqt." + t, e.type(), t);
            assertEquals("store-001", e.storeId(), t);
        }
    }

    @Test
    void rejectsFractionalMoneyAndRealBillNumbers() throws Exception {
        ObjectNode fractional = billingSample("pos.payment");
        ((ObjectNode) fractional.get("data")).put("amountMinor", 490.5);
        assertThrows(InvalidEventException.class, () -> validator.validate(fractional));
        ObjectNode realNumber = billingSample("pos.sale.line");
        ((ObjectNode) realNumber.get("data")).put("billRef", "INV-2026-000123");
        assertThrows(InvalidEventException.class, () -> validator.validate(realNumber));
        ObjectNode customer = billingSample("pos.sale.line");
        ((ObjectNode) customer.get("data")).put("customerName", "A Person");
        assertThrows(InvalidEventException.class, () -> validator.validate(customer));
    }

    // X1 milestone B3 (ADR-036): billing exports arrive after the day closes, so billing types get 35 days;
    // every other type keeps the 24-hour bound. Same configuration as application.yml.

    static final Map<String, Duration> BILLING_AGES = Map.of("com.pqt.pos.", Duration.ofDays(35),
            "com.pqt.stock.", Duration.ofDays(35));
    static final Instant NOW = Instant.parse("2026-06-01T00:00:00Z");

    EventValidator withBillingAges() throws Exception {
        SchemaRegistry reg = new SchemaRegistry(Path.of(System.getProperty("pqt.schemas", "../../schemas")), m);
        PqtProperties.Ingest cfg = new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536,
                1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(), BILLING_AGES);
        return new EventValidator(reg, cfg, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    ObjectNode realCameraEvent(Instant time) throws Exception {
        ObjectNode n = valid();
        n.put("source", "urn:pqt:edge:store-001:cam-1");
        n.remove("simrunid");
        n.put("time", time.toString());
        return n;
    }

    @Test
    void aThreeDayOldBillIsAcceptedAndAThreeDayOldCameraEventIsStillRefused() throws Exception {
        EventValidator v = withBillingAges();
        Instant threeDaysAgo = NOW.minus(Duration.ofDays(3));
        for (String t : BILLING) {
            ObjectNode bill = billingSample(t);
            bill.put("time", threeDaysAgo.toString());
            assertEquals("com.pqt." + t, v.validate(bill).type(), t);
        }
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> v.validate(realCameraEvent(threeDaysAgo)));
        assertTrue(ex.errors().contains("time: older than PT24H"), ex.errors().toString());
        assertEquals("store-001", v.validate(realCameraEvent(NOW.minus(Duration.ofHours(23)))).storeId(),
                "a camera event inside 24 h is still accepted");
    }

    @Test
    void billingTypesStillHaveAnAgeLimit() throws Exception {
        ObjectNode old = billingSample("pos.sale.line");
        old.put("time", NOW.minus(Duration.ofDays(35)).minusSeconds(1).toString());
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> withBillingAges().validate(old));
        assertTrue(ex.errors().contains("time: older than PT840H"), ex.errors().toString());
    }

    @Test
    void withoutPerTypeAgesBillingKeepsTheDefaultBound() throws Exception {
        ObjectNode bill = billingSample("pos.payment");
        bill.put("time", NOW.minus(Duration.ofDays(3)).toString());
        assertThrows(InvalidEventException.class, () -> validator.validate(bill), "the 11-argument configuration has no per-type ages");
    }

    @Test
    void theLongestMatchingPrefixDecidesTheAge() {
        PqtProperties.Ingest cfg = new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536,
                1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(),
                Map.of("com.pqt.", Duration.ofDays(2), "com.pqt.pos.", Duration.ofDays(35)));
        assertEquals(Duration.ofDays(35), cfg.maxAgeFor("com.pqt.pos.sale.line"));
        assertEquals(Duration.ofDays(2), cfg.maxAgeFor("com.pqt.store.queue.length"));
        assertEquals(Duration.ofHours(24), cfg.maxAgeFor("org.example.other"));
        assertEquals(Duration.ofHours(24), cfg.maxAgeFor(null));
    }

    @Test
    void aZeroOrNegativePerTypeAgeIsAConfigurationError() {
        assertThrows(IllegalArgumentException.class, () -> new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24),
                500, 65536, 1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(), Map.of("com.pqt.pos.", Duration.ZERO)));
        assertThrows(IllegalArgumentException.class, () -> new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24),
                500, 65536, 1048576, 100, 200, 1, "urn:pqt:sim:", Map.of(), List.of(), Map.of(" ", Duration.ofDays(1))));
    }

    @Test
    void aBillingPayloadForAnotherStoreIsRefused() throws Exception {
        ObjectNode n = billingSample("pos.sale.line");
        ((ObjectNode) n.get("data")).put("storeId", "store-002");
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> validator.validate(n));
        assertTrue(ex.errors().contains("data.storeId: must equal storeid"), ex.errors().toString());
    }

    @Test
    void anUnregisteredVersionIsRejected() throws Exception {
        ObjectNode n = valid();
        n.put("dataschema", "https://schemas.pqt.local/store/queue.length/1.2.0");
        InvalidEventException ex = assertThrows(InvalidEventException.class, () -> validator.validate(n));
        assertTrue(ex.errors().toString().contains("not registered"), ex.errors().toString());
    }
}
