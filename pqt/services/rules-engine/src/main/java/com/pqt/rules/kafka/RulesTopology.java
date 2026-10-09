package com.pqt.rules.kafka;

import com.pqt.rules.domain.FootfallConfig;
import com.pqt.rules.domain.RuleEngine;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.Topology;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.kstream.KStream;
import org.apache.kafka.streams.kstream.Produced;
import org.apache.kafka.streams.processor.TopicNameExtractor;
import org.apache.kafka.streams.processor.api.ProcessorSupplier;
import org.apache.kafka.streams.state.Stores;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Optional;

/**
 * store.events.v1 -> per-(store, run) rule state -> incidents.v1 | incidents.shadow.v1 | rules.heartbeat.v1
 *                 \-> R-FOOT-001 tumbling windows (FootfallTopology) -> incidents.v1 | incidents.shadow.v1
 */
public final class RulesTopology {
    private RulesTopology() {
    }

    public static Topology build(RuleEngine engine, Topics topics, RulesMetrics metrics, String instanceId,
                                 Duration idleTtl) {
        return build(engine, Optional.empty(), topics, metrics, instanceId, idleTtl);
    }

    public static Topology build(RuleEngine engine, Optional<FootfallConfig> footfall, Topics topics, RulesMetrics metrics,
                                 String instanceId, Duration idleTtl) {
        StreamsBuilder builder = new StreamsBuilder();
        KStream<String, String> events = coreRules(builder, RulesHolder.fixed(engine), topics, metrics, instanceId, idleTtl);
        footfall.ifPresent(cfg -> FootfallTopology.addTo(builder, events, cfg, topics, metrics, idleTtl));
        return builder.build();
    }

    /**
     * Milestone R4: the rules come from {@code rules} and, when {@code configTopic} is set, a global
     * store on that compacted topic swaps them without a restart (see RulesConfigProcessor).
     */
    public static Topology build(RulesHolder rules, Optional<String> configTopic, Topics topics, RulesMetrics metrics,
                                 String instanceId, Duration idleTtl) {
        StreamsBuilder builder = new StreamsBuilder();
        configTopic.ifPresent(topic -> builder.addGlobalStore(
                Stores.keyValueStoreBuilder(Stores.inMemoryKeyValueStore(RulesConfigProcessor.STORE),
                        Serdes.String(), Serdes.String()).withLoggingDisabled(),
                topic, Consumed.with(Serdes.String(), Serdes.String()),
                () -> new RulesConfigProcessor(rules, metrics)));
        KStream<String, String> events = coreRules(builder, rules, topics, metrics, instanceId, idleTtl);
        rules.current().rules().footfall().ifPresent(cfg -> FootfallTopology.addTo(builder, events, cfg,
                () -> rules.current().footfall().orElseThrow(), topics, metrics, idleTtl));
        return builder.build();
    }

    private static KStream<String, String> coreRules(StreamsBuilder builder, RulesHolder rules, Topics topics,
                                                     RulesMetrics metrics, String instanceId, Duration idleTtl) {
        builder.addStateStore(Stores.keyValueStoreBuilder(
                Stores.persistentKeyValueStore(StoreRulesProcessor.STORE), Serdes.String(), StoreStateSerde.create()));
        ProcessorSupplier<String, String, String, String> supplier =
                () -> new StoreRulesProcessor(rules, topics, metrics, instanceId, idleTtl);
        KStream<String, String> events = builder.stream(topics.validated(), Consumed.with(Serdes.String(), Serdes.String())
                .withTimestampExtractor(new CloudEventTimestampExtractor()));
        events.process(supplier, StoreRulesProcessor.STORE)
                .to(router(), Produced.with(Serdes.String(), Serdes.String()));
        return events;
    }

    /** Routes each output record to the topic named in its pqt-route header. */
    static TopicNameExtractor<String, String> router() {
        return (key, value, recordContext) -> {
            Header h = recordContext.headers().lastHeader(StoreRulesProcessor.ROUTE_HEADER);
            if (h == null) throw new IllegalStateException("record without route header");
            return new String(h.value(), StandardCharsets.UTF_8);
        };
    }
}
