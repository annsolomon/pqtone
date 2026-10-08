package com.pip.rules.kafka;

import com.pip.rules.domain.RuleEngine;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.Topology;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.kstream.Produced;
import org.apache.kafka.streams.processor.TopicNameExtractor;
import org.apache.kafka.streams.processor.api.ProcessorSupplier;
import org.apache.kafka.streams.state.Stores;

import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * store.events.v1 -> per-(store, run) rule state -> incidents.v1 | incidents.shadow.v1 | rules.heartbeat.v1
 */
public final class RulesTopology {
    private RulesTopology() {
    }

    public static Topology build(RuleEngine engine, Topics topics, RulesMetrics metrics, String instanceId,
                                 Duration idleTtl) {
        StreamsBuilder builder = new StreamsBuilder();
        builder.addStateStore(Stores.keyValueStoreBuilder(
                Stores.persistentKeyValueStore(StoreRulesProcessor.STORE), Serdes.String(), StoreStateSerde.create()));

        ProcessorSupplier<String, String, String, String> supplier =
                () -> new StoreRulesProcessor(engine, topics, metrics, instanceId, idleTtl);
        TopicNameExtractor<String, String> router = (key, value, recordContext) -> {
            Header h = recordContext.headers().lastHeader(StoreRulesProcessor.ROUTE_HEADER);
            if (h == null) throw new IllegalStateException("record without route header");
            return new String(h.value(), StandardCharsets.UTF_8);
        };

        builder.stream(topics.validated(), Consumed.with(Serdes.String(), Serdes.String())
                        .withTimestampExtractor(new CloudEventTimestampExtractor()))
                .process(supplier, StoreRulesProcessor.STORE)
                .to(router, Produced.with(Serdes.String(), Serdes.String()));
        return builder.build();
    }
}
