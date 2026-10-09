package com.pqt.eventcore.incident;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/** Consumes rules-engine output (read_committed) and liveness heartbeats. */
@Component
public class IncidentConsumer {
    private static final Logger log = LoggerFactory.getLogger(IncidentConsumer.class);
    private final ObjectMapper mapper;
    private final IncidentService incidents;
    private final JdbcTemplate jdbc;
    private final MeterRegistry meters;

    public IncidentConsumer(ObjectMapper mapper, IncidentService incidents, JdbcTemplate jdbc, MeterRegistry meters) {
        this.mapper = mapper;
        this.incidents = incidents;
        this.jdbc = jdbc;
        this.meters = meters;
    }

    @KafkaListener(id = "incidents", topics = {"${pqt.topics.incidents}", "${pqt.topics.shadow}"}, groupId = "event-core-incidents")
    public void onIncident(ConsumerRecord<String, String> rec, Acknowledgment ack) {
        JsonNode n;
        try {
            n = mapper.readTree(rec.value());
        } catch (Exception ex) {
            log.error("skipping unreadable incident record {}/{}@{}", rec.topic(), rec.partition(), rec.offset());
            meters.counter("pqt.incidents.unreadable").increment();
            ack.acknowledge();
            return;
        }
        if (n.path("schemaVersion").asInt() != 1) {
            log.error("unsupported incident schemaVersion {}", n.path("schemaVersion"));
            meters.counter("pqt.incidents.unreadable").increment();
            ack.acknowledge();
            return;
        }
        incidents.applyRuleOutput(n);
        meters.counter("pqt.incidents.applied", "kind", n.path("kind").asText(), "mode", n.path("mode").asText()).increment();
        ack.acknowledge();
    }

    @KafkaListener(id = "heartbeat", topics = "${pqt.topics.heartbeat}", groupId = "event-core-heartbeat")
    public void onHeartbeat(ConsumerRecord<String, String> rec, Acknowledgment ack) {
        try {
            JsonNode n = mapper.readTree(rec.value());
            jdbc.update("""
                    INSERT INTO pqt.pipeline_heartbeat (task_id, received_at, payload) VALUES (?, now(), ?::jsonb)
                    ON CONFLICT (task_id) DO UPDATE SET received_at = excluded.received_at, payload = excluded.payload""",
                    n.path("taskId").asText(rec.key()), rec.value());
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            log.warn("skipping unreadable heartbeat");
        }
        ack.acknowledge();
    }
}
