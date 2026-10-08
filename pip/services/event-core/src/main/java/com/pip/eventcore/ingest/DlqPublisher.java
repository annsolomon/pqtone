package com.pip.eventcore.ingest;

import com.pip.eventcore.config.PipProperties;
import io.micrometer.core.instrument.MeterRegistry;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.TimeUnit;

/** Rejected raw-topic records go to the DLQ with the reason in headers; payloads are kept verbatim. */
@Component
public class DlqPublisher {
    private final KafkaTemplate<String, String> kafka;
    private final PipProperties props;
    private final MeterRegistry meters;

    public DlqPublisher(KafkaTemplate<String, String> kafka, PipProperties props, MeterRegistry meters) {
        this.kafka = kafka;
        this.props = props;
        this.meters = meters;
    }

    public void publish(ConsumerRecord<String, String> rec, String reason, List<String> errors) {
        ProducerRecord<String, String> out = new ProducerRecord<>(props.topics().dlq(), rec.key(), rec.value());
        out.headers().add("dlq-reason", reason.getBytes(StandardCharsets.UTF_8));
        String joined = String.join("; ", errors);
        if (joined.length() > 1024) joined = joined.substring(0, 1024);
        out.headers().add("dlq-errors", joined.getBytes(StandardCharsets.UTF_8));
        out.headers().add("dlq-source", (rec.topic() + "/" + rec.partition() + "@" + rec.offset()).getBytes(StandardCharsets.UTF_8));
        try {
            kafka.send(out).get(10, TimeUnit.SECONDS);
        } catch (Exception ex) {
            throw new IllegalStateException("DLQ publish failed", ex);
        }
        meters.counter("pip.dlq.events", "reason", reason).increment();
    }
}
