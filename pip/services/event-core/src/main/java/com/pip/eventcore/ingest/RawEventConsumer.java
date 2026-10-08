package com.pip.eventcore.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Raw-topic ingestion path. Offsets are committed only after the database transaction (or the
 * DLQ write) succeeds; infrastructure failures are retried by the container error handler.
 */
@Component
public class RawEventConsumer {
    private final ObjectMapper mapper;
    private final EventValidator validator;
    private final SourcePolicy policy;
    private final IngestService ingest;
    private final DlqPublisher dlq;

    public RawEventConsumer(ObjectMapper mapper, EventValidator validator, SourcePolicy policy, IngestService ingest,
                            DlqPublisher dlq) {
        this.mapper = mapper;
        this.validator = validator;
        this.policy = policy;
        this.ingest = ingest;
        this.dlq = dlq;
    }

    @KafkaListener(id = "raw-ingest", topics = "${pip.topics.raw}", groupId = "event-core-ingest",
            concurrency = "${pip.ingest.raw-concurrency}")
    public void onMessage(ConsumerRecord<String, String> rec, Acknowledgment ack) {
        JsonNode node;
        try {
            node = rec.value() == null ? null : mapper.readTree(rec.value());
        } catch (Exception ex) {
            node = null;
        }
        if (node == null || !node.isObject()) {
            dlq.publish(rec, "malformed-json", List.of("payload is not a JSON object"));
            ack.acknowledge();
            return;
        }
        try {
            ValidatedEvent e = validator.validate(node);
            if (!policy.rawTopicAllows(e.source())) {
                dlq.publish(rec, "forbidden-source", List.of("source " + e.source() + " may not use the raw topic"));
            } else if (ingest.ingest(e, "kafka", "kafka:" + rec.topic()).status() == IngestService.Status.CONFLICT) {
                dlq.publish(rec, "conflicting-duplicate", List.of("same (source, id) with a different payload"));
            }
        } catch (InvalidEventException ex) {
            dlq.publish(rec, "invalid-event", ex.errors());
        }
        ack.acknowledge();
    }
}
