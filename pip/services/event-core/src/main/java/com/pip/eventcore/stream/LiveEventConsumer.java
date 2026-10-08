package com.pip.eventcore.stream;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/**
 * Every event-core instance tails the validated topic with its own throw-away consumer group
 * (latest offset), so each instance can serve the live view regardless of which one ingested.
 */
@Component
public class LiveEventConsumer {
    private final ObjectMapper mapper;
    private final LiveHub hub;

    public LiveEventConsumer(ObjectMapper mapper, LiveHub hub) {
        this.mapper = mapper;
        this.hub = hub;
    }

    @KafkaListener(id = "live-view", topics = "${pip.topics.validated}",
            groupId = "event-core-live-#{T(java.util.UUID).randomUUID().toString()}",
            properties = {"auto.offset.reset=latest"})
    public void onEvent(ConsumerRecord<String, String> rec, Acknowledgment ack) {
        try {
            hub.storeEvent(mapper.readTree(rec.value()));
        } catch (Exception ignored) {
            // validated topic only carries contract-checked events; nothing to do on a bad record
        }
        ack.acknowledge();
    }
}
