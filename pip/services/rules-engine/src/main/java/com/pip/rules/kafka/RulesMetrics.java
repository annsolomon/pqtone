package com.pip.rules.kafka;

import com.pip.rules.domain.Incident;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;

import java.util.concurrent.atomic.AtomicLong;

/** Application metrics (Kafka Streams client metrics are bound separately). */
public final class RulesMetrics {
    private final MeterRegistry registry;
    private final Counter processed;
    private final Counter invalid;
    private final AtomicLong lastHeartbeatEpochSeconds = new AtomicLong();

    public RulesMetrics(MeterRegistry registry) {
        this.registry = registry;
        this.processed = Counter.builder("pip.rules.events.received").description("Events received").register(registry);
        this.invalid = Counter.builder("pip.rules.events.invalid").description("Events rejected by the parser").register(registry);
        registry.gauge("pip.rules.heartbeat.last.seconds", lastHeartbeatEpochSeconds);
    }

    public void received() {
        processed.increment();
    }

    public void invalid() {
        invalid.increment();
    }

    public void late(String storeId) {
        Counter.builder("pip.rules.events.late").description("Events dropped because they arrived after the watermark")
                .tag("store", storeId).register(registry).increment();
    }

    public void incident(Incident inc) {
        Counter.builder("pip.rules.incidents").description("Incident records emitted")
                .tag("rule", inc.ruleId).tag("mode", inc.mode).tag("kind", inc.kind)
                .register(registry).increment();
    }

    /** Milestone R4: a rules document from rules.config.v1 was applied, refused, or reset to the file. */
    public void rulesReloaded(String outcome) {
        Counter.builder("pip.rules.config.reloads").description("Rules documents received from rules.config.v1")
                .tag("outcome", outcome).register(registry).increment();
    }

    public void heartbeat(long epochMillis) {
        lastHeartbeatEpochSeconds.set(epochMillis / 1000);
    }
}
