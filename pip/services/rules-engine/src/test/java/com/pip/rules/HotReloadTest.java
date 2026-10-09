package com.pip.rules;

import com.fasterxml.jackson.databind.JsonNode;
import com.pip.rules.app.Json;
import com.pip.rules.app.RuleSet;
import com.pip.rules.kafka.RulesHolder;
import com.pip.rules.kafka.RulesMetrics;
import com.pip.rules.kafka.RulesTopology;
import com.pip.rules.kafka.Topics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.TopologyTestDriver;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone R4: a rules document published to rules.config.v1 changes behaviour without a
 * restart; incidents carry the version that produced them; a refused document changes nothing;
 * a tombstone goes back to the deployment file.
 */
class HotReloadTest {
    static final Topics T = new Topics("store.events.v1", "incidents.v1", "incidents.shadow.v1", "rules.heartbeat.v1");
    static final String CONFIG = "rules.config.v1";
    static final long BASE = Instant.parse("2026-01-01T09:00:00Z").toEpochMilli();
    static final String Q_HEAD = "  - id: R-QUEUE-001\n    version: 1.0.0\n";

    @TempDir
    Path stateDir;
    TopologyTestDriver driver;
    TestInputTopic<String, String> events;
    TestInputTopic<String, String> config;
    TestOutputTopic<String, String> incidents;
    TestOutputTopic<String, String> heartbeats;
    RulesHolder holder;
    int seq;

    static String rules(String threshold, String version) {
        String file = VersionCheckTest.FILE;
        return file.replace("      threshold: 6 ", "      threshold: " + threshold + " ")
                .replace(Q_HEAD, "  - id: R-QUEUE-001\n    version: " + version + "\n");
    }

    @BeforeEach
    void start() {
        holder = new RulesHolder(RuleSet.parse(VersionCheckTest.FILE));
        Properties p = new Properties();
        p.put(StreamsConfig.APPLICATION_ID_CONFIG, "hot-reload-test");
        p.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "dummy:9092");
        p.put(StreamsConfig.STATE_DIR_CONFIG, stateDir.toString());
        driver = new TopologyTestDriver(RulesTopology.build(holder, Optional.of(CONFIG), T,
                new RulesMetrics(new SimpleMeterRegistry()), "test", Duration.ofHours(6)), p, Instant.EPOCH);
        events = driver.createInputTopic(T.validated(), new StringSerializer(), new StringSerializer());
        config = driver.createInputTopic(CONFIG, new StringSerializer(), new StringSerializer());
        incidents = driver.createOutputTopic(T.incidents(), new StringDeserializer(), new StringDeserializer());
        heartbeats = driver.createOutputTopic(T.heartbeat(), new StringDeserializer(), new StringDeserializer());
    }

    @AfterEach
    void stop() {
        driver.close();
    }

    String event(String run, long offsetMs, String type, String subject, String data) {
        seq++;
        return "{\"specversion\":\"1.0\",\"id\":\"hr-" + seq + "\",\"source\":\"urn:pip:sim:store-sim:store-001\","
                + "\"type\":\"" + type + "\",\"time\":\"" + Instant.ofEpochMilli(BASE + offsetMs) + "\",\"subject\":\"" + subject + "\","
                + "\"dataschema\":\"https://schemas.pip.local/x\",\"datacontenttype\":\"application/json\","
                + "\"storeid\":\"store-001\",\"partitionkey\":\"store-001\",\"simrunid\":\"" + run + "\","
                + "\"sequence\":\"" + String.format("%010d", seq) + "\",\"data\":" + data + "}";
    }

    /** A queue of 9 held for 5 minutes in its own run, ticks every 10 s. Returns the R-QUEUE-001 incidents opened. */
    List<JsonNode> queueOfNine(String run) throws Exception {
        events.pipeInput("store-001", event(run, 0, "com.pip.store.queue.length", "queue:checkout-1",
                "{\"queueId\":\"checkout-1\",\"length\":9,\"openRegisters\":1}"));
        for (long t = 10_000; t <= 300_000; t += 10_000) {
            events.pipeInput("store-001", event(run, t, "com.pip.store.clock.tick", "store", "{\"seq\":" + t + "}"));
        }
        List<JsonNode> out = new ArrayList<>();
        for (String v : incidents.readValuesToList()) {
            JsonNode n = Json.MAPPER.readTree(v);
            if ("R-QUEUE-001".equals(n.path("ruleId").asText()) && "OPENED".equals(n.path("kind").asText())) out.add(n);
        }
        return out;
    }

    JsonNode lastHeartbeat() throws Exception {
        driver.advanceWallClockTime(Duration.ofSeconds(6));
        List<String> all = heartbeats.readValuesToList();
        return Json.MAPPER.readTree(all.get(all.size() - 1));
    }

    @Test
    void thresholdChangeAppliesWithoutARestartAndIncidentsKeepTheirVersion() throws Exception {
        List<JsonNode> before = queueOfNine("run-aaaaaaaaaaa1");
        assertEquals(1, before.size(), "threshold 6: a queue of 9 opens an incident");
        assertEquals("1.0.0", before.get(0).path("ruleVersion").asText());
        assertEquals("file", lastHeartbeat().path("rules").path("source").asText());

        config.pipeInput("rules", rules("20", "1.1.0"));
        assertEquals("1.1.0", holder.current().rules().versions().get("R-QUEUE-001"));
        JsonNode hb = lastHeartbeat();
        assertEquals("topic", hb.path("rules").path("source").asText());
        assertEquals("1.1.0", hb.path("rules").path("versions").path("R-QUEUE-001").asText());
        assertTrue(queueOfNine("run-aaaaaaaaaaa2").isEmpty(), "threshold 20: the same queue no longer alerts");

        config.pipeInput("rules", rules("4", "1.1.0"));   // changed again without a bump: refused
        assertEquals("1.1.0", holder.current().rules().versions().get("R-QUEUE-001"));
        assertTrue(queueOfNine("run-aaaaaaaaaaa3").isEmpty(), "the refused document changed nothing");

        config.pipeInput("rules", rules("8", "1.2.0"));
        List<JsonNode> lowered = queueOfNine("run-aaaaaaaaaaa4");
        assertEquals(1, lowered.size(), "threshold 8: a queue of 9 alerts again");
        assertEquals("1.2.0", lowered.get(0).path("ruleVersion").asText(), "new incidents carry the new version");

        config.pipeInput("rules", (String) null);         // tombstone: back to the deployment file
        assertEquals("file", holder.current().source());
        List<JsonNode> reset = queueOfNine("run-aaaaaaaaaaa5");
        assertEquals(1, reset.size());
        assertEquals("1.0.0", reset.get(0).path("ruleVersion").asText());
    }

    @Test
    void structuralChangesAreRefusedWhileRunning() {
        config.pipeInput("rules", VersionCheckTest.FILE.replaceFirst("(?m)^grace: PT30S", "grace: PT45S"));
        assertEquals("file", holder.current().source());
        config.pipeInput("rules", "not: [valid");
        assertEquals("file", holder.current().source());
    }
}
