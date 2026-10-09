package com.pip.rules;

import com.fasterxml.jackson.databind.JsonNode;
import com.pip.rules.app.Json;
import com.pip.rules.domain.FootfallConfig;
import com.pip.rules.domain.RuleConfig;
import com.pip.rules.kafka.CloudEventTimestampExtractor;
import com.pip.rules.kafka.FootfallTopology;
import com.pip.rules.kafka.RulesMetrics;
import com.pip.rules.kafka.Topics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.TopologyTestDriver;
import org.apache.kafka.streams.kstream.Consumed;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * R-FOOT-001 through the real Kafka Streams windows: tumbling-window boundaries, records
 * within and beyond grace, and suppress emitting each window once, when it closes.
 *
 * History is 12 windows of 4 entries (sum 48), so window 12 is a spike at 10 or more
 * entries (10 x 12 = 120 > 2 x 48) and not at 9 (below minCount 10). One entry decides it.
 */
class FootfallTopologyTest {
    static final Topics T = new Topics("store.events.v1", "incidents.v1", "incidents.shadow.v1", "rules.heartbeat.v1");
    static final long W = 300_000;
    static final long G = 30_000;
    static final long BASE = Instant.parse("2026-01-01T09:00:00Z").toEpochMilli(); // a multiple of 5 min

    @TempDir
    Path stateDir;
    TopologyTestDriver driver;
    TestInputTopic<String, String> in;
    TestOutputTopic<String, String> enforce;
    TestOutputTopic<String, String> shadow;
    int seq;

    @BeforeEach
    void setUp() {
        open("enforce");
    }

    void open(String mode) {
        if (driver != null) driver.close();
        FootfallConfig cfg = new FootfallConfig(new RuleConfig.RuleMeta("R-FOOT-001", "1.0.0", mode, "low"),
                List.of("entrance"), W, 12, 2.0, 10, G);
        StreamsBuilder b = new StreamsBuilder();
        FootfallTopology.addTo(b, b.stream(T.validated(), Consumed.with(Serdes.String(), Serdes.String())
                .withTimestampExtractor(new CloudEventTimestampExtractor())), cfg, T,
                new RulesMetrics(new SimpleMeterRegistry()), Duration.ofHours(6));
        Properties p = new Properties();
        p.put(StreamsConfig.APPLICATION_ID_CONFIG, "footfall-test-" + mode);
        p.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "dummy:9092");
        p.put(StreamsConfig.STATE_DIR_CONFIG, stateDir.resolve(mode).toString());
        driver = new TopologyTestDriver(b.build(), p, Instant.EPOCH);
        in = driver.createInputTopic(T.validated(), new StringSerializer(), new StringSerializer());
        enforce = driver.createOutputTopic(T.incidents(), new StringDeserializer(), new StringDeserializer());
        shadow = driver.createOutputTopic(T.shadow(), new StringDeserializer(), new StringDeserializer());
    }

    @AfterEach
    void tearDown() {
        driver.close();
    }

    String event(long offsetMs, String type, String subject, String data) {
        seq++;
        return "{\"specversion\":\"1.0\",\"id\":\"ff-" + seq + "\",\"source\":\"urn:pip:sim:store-sim:store-001\","
                + "\"type\":\"" + type + "\",\"time\":\"" + Instant.ofEpochMilli(BASE + offsetMs) + "\",\"subject\":\"" + subject + "\","
                + "\"dataschema\":\"https://schemas.pip.local/x\",\"datacontenttype\":\"application/json\","
                + "\"storeid\":\"store-001\",\"partitionkey\":\"store-001\",\"simrunid\":\"run-abcdefabcdef\","
                + "\"sequence\":\"" + String.format("%010d", seq) + "\",\"data\":" + data + "}";
    }

    void tick(long offsetMs) {
        in.pipeInput("store-001", event(offsetMs, "com.pip.store.clock.tick", "store", "{\"seq\":" + seq + "}"));
    }

    void entered(long offsetMs, String zone) {
        String trk = "trk-" + seq;
        in.pipeInput("store-001", event(offsetMs, "com.pip.store.zone.entered", "track:" + trk,
                "{\"zoneId\":\"" + zone + "\",\"trackId\":\"" + trk + "\"}"));
    }

    /** 12 windows of 4 entrance entries each, ticks every 10 s. Ends with stream time at 12 W - 10 s. */
    void history() {
        for (int w = 0; w < 12; w++) {
            for (long t = w * W; t < (w + 1) * W; t += 10_000) tick(t);
            for (int k = 0; k < 4; k++) entered(w * W + 1_000 + k, "entrance");
        }
    }

    /** n entrance entries early in window 12, plus ticks up to (not including) its end. */
    void window12(int n) {
        for (int k = 0; k < n; k++) entered(12 * W + 1_000 + k, "entrance");
        for (long t = 12 * W; t < 13 * W; t += 10_000) tick(t);
    }

    List<JsonNode> opened(TestOutputTopic<String, String> topic) throws Exception {
        List<JsonNode> out = new ArrayList<>();
        for (String v : topic.readValuesToList()) {
            JsonNode n = Json.MAPPER.readTree(v);
            if ("OPENED".equals(n.path("kind").asText())) out.add(n);
        }
        return out;
    }

    @Test
    void aSpikeWindowIsEmittedOnceAndOnlyWhenItCloses() throws Exception {
        history();
        window12(10);
        tick(13 * W + G - 1);                         // window 12 still open (end + grace not reached)
        assertTrue(enforce.readValuesToList().isEmpty());
        tick(13 * W + G);                             // stream time = end + grace: closes and emits
        List<JsonNode> out = opened(enforce);
        assertEquals(1, out.size());
        JsonNode inc = out.get(0);
        assertEquals("R-FOOT-001", inc.path("ruleId").asText());
        assertEquals("entrance", inc.path("key").asText());
        assertEquals("zone:entrance", inc.path("subject").asText());
        assertEquals(BASE + 12 * W, inc.path("onsetMs").asLong());
        assertEquals(BASE + 13 * W, inc.path("detectedMs").asLong());
        assertEquals(10, inc.path("attrs").path("count").asInt());
        assertEquals("run-abcdefabcdef", inc.path("simRunId").asText());
        for (long t = 13 * W + G + 10_000; t < 15 * W; t += 10_000) tick(t);
        assertTrue(opened(enforce).isEmpty(), "suppress emits each window once");
    }

    @Test
    void nineEntriesAreNotASpike() throws Exception {
        history();
        window12(9);
        tick(13 * W + G);
        assertTrue(opened(enforce).isEmpty());
    }

    @Test
    void anEventExactlyAtTheWindowEndBelongsToTheNextWindow() throws Exception {
        history();
        window12(9);
        entered(13 * W, "entrance");                  // [start, end): this is window 13
        tick(13 * W + G);
        assertTrue(opened(enforce).isEmpty());
    }

    @Test
    void anEventJustBeforeTheWindowEndCounts() throws Exception {
        history();
        window12(9);
        entered(13 * W - 1, "entrance");
        tick(13 * W + G);
        assertEquals(1, opened(enforce).size());
    }

    @Test
    void anOutOfOrderEventWithinGraceStillCounts() throws Exception {
        history();
        window12(9);
        tick(13 * W + G - 5_000);                     // stream time past the end, inside grace
        entered(13 * W - 2_000, "entrance");          // late in arrival, but the window is still open
        tick(13 * W + G);
        assertEquals(1, opened(enforce).size());
    }

    @Test
    void anEventAfterTheWindowClosedIsDropped() throws Exception {
        history();
        window12(9);
        tick(13 * W + G);                             // window 12 closed (and evaluated: 9, no spike)
        entered(13 * W - 2_000, "entrance");          // too late: dropped by the window
        for (long t = 13 * W + G + 10_000; t < 15 * W; t += 10_000) tick(t);
        assertTrue(opened(enforce).isEmpty());
    }

    @Test
    void otherZonesAreNotCountedAndShadowModeRoutesToTheShadowTopic() throws Exception {
        open("shadow");
        history();
        window12(4);
        for (int k = 0; k < 40; k++) entered(12 * W + 2_000 + k, "produce");
        tick(13 * W + G);
        assertTrue(opened(shadow).isEmpty());
        for (int k = 0; k < 30; k++) entered(13 * W + G + 1_000 + k, "entrance");
        for (long t = 13 * W + G; t <= 14 * W + G; t += 10_000) tick(t);
        List<JsonNode> out = opened(shadow);
        assertEquals(1, out.size());
        assertEquals("shadow", out.get(0).path("mode").asText());
        assertTrue(enforce.readValuesToList().isEmpty());
    }
}
