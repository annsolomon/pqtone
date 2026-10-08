package com.pip.rules.kafka;

import com.fasterxml.jackson.databind.JsonNode;
import com.pip.rules.app.Json;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.streams.processor.TimestampExtractor;

import java.time.Instant;

/** Uses the CloudEvent "time" attribute as the Kafka Streams record timestamp. */
public final class CloudEventTimestampExtractor implements TimestampExtractor {
    @Override
    public long extract(ConsumerRecord<Object, Object> record, long partitionTime) {
        if (record.value() instanceof String s) {
            try {
                JsonNode t = Json.MAPPER.readTree(s).get("time");
                if (t != null && t.isTextual()) return Instant.parse(t.asText()).toEpochMilli();
            } catch (Exception ignored) {
                // fall through: the processor will reject the record
            }
        }
        return record.timestamp() >= 0 ? record.timestamp() : Math.max(partitionTime, 0L);
    }
}
