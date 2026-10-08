package com.pip.rules;

import com.fasterxml.jackson.databind.JsonNode;
import com.pip.rules.app.Json;
import com.pip.rules.domain.RuleEngine;
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
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TopologyTest {
    static final Topics T = new Topics("store.events.v1", "incidents.v1", "incidents.shadow.v1", "rules.heartbeat.v1");
    static int seq = 0;

    static String event(long ms, String type, String data) {
        seq++;
        return "{\"specversion\":\"1.0\",\"id\":\"id-" + seq + "\",\"source\":\"urn:pip:sim:store-sim:store-001\","
                + "\"type\":\"" + type + "\",\"time\":\"" + Instant.ofEpochMilli(ms) + "\",\"subject\":\"store\","
                + "\"dataschema\":\"https://schemas.pip.local/x\",\"datacontenttype\":\"application/json\","
                + "\"storeid\":\"store-001\",\"partitionkey\":\"store-001\",\"simrunid\":\"run-abcdefabcdef\","
                + "\"sequence\":\"" + String.format("%010d", seq) + "\",\"data\":" + data + "}";
    }

    @Test
    void routesEnforceAndShadowIncidentsAndEmitsHeartbeats() throws Exception {
        Properties p = new Properties();
        p.put(StreamsConfig.APPLICATION_ID_CONFIG, "rules-engine-test");
        p.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "dummy:9092");
        RuleEngine engine = new RuleEngine(RuleEngineTest.cfg("shadow"));
        RulesMetrics metrics = new RulesMetrics(new SimpleMeterRegistry());
        try (TopologyTestDriver driver = new TopologyTestDriver(
                RulesTopology.build(engine, T, metrics, "test", Duration.ofHours(6)), p)) {
            TestInputTopic<String, String> in = driver.createInputTopic(T.validated(), new StringSerializer(), new StringSerializer());
            TestOutputTopic<String, String> enforce = driver.createOutputTopic(T.incidents(), new StringDeserializer(), new StringDeserializer());
            TestOutputTopic<String, String> shadow = driver.createOutputTopic(T.shadow(), new StringDeserializer(), new StringDeserializer());
            TestOutputTopic<String, String> hb = driver.createOutputTopic(T.heartbeat(), new StringDeserializer(), new StringDeserializer());

            long base = Instant.parse("2026-01-01T09:00:00Z").toEpochMilli();
            in.pipeInput("store-001", event(base, "com.pip.store.queue.length", "{\"queueId\":\"checkout-1\",\"length\":9,\"openRegisters\":1}"));
            in.pipeInput("store-001", "not json at all");
            for (long t = 10_000; t <= 500_000; t += 10_000) {
                in.pipeInput("store-001", event(base + t, "com.pip.store.clock.tick", "{\"seq\":" + t + "}"));
            }

            List<String> e = enforce.readValuesToList();
            List<String> s = shadow.readValuesToList();
            assertEquals(1, e.size());
            JsonNode q = Json.MAPPER.readTree(e.get(0));
            assertEquals("R-QUEUE-001", q.path("ruleId").asText());
            assertEquals("OPENED", q.path("kind").asText());
            assertEquals("run-abcdefabcdef", q.path("simRunId").asText());
            assertEquals(1, q.path("schemaVersion").asInt());
            assertEquals(1, s.size());
            assertEquals("R-ABS-001", Json.MAPPER.readTree(s.get(0)).path("ruleId").asText());

            driver.advanceWallClockTime(Duration.ofSeconds(6));
            List<String> beats = hb.readValuesToList();
            assertFalse(beats.isEmpty());
            JsonNode beat = Json.MAPPER.readTree(beats.get(beats.size() - 1));
            JsonNode key = beat.path("keys").get(0);
            assertEquals("store-001|run-abcdefabcdef", key.path("key").asText());
            assertTrue(key.path("watermarkMs").asLong() >= base + 470_000);
        }
    }
}
