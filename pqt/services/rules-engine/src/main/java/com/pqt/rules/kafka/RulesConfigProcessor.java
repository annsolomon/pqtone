package com.pqt.rules.kafka;

import org.apache.kafka.streams.processor.api.Processor;
import org.apache.kafka.streams.processor.api.ProcessorContext;
import org.apache.kafka.streams.processor.api.Record;
import org.apache.kafka.streams.state.KeyValueStore;

import java.util.List;

/**
 * Global-store processor for rules.config.v1 (milestone R4). Every instance sees every record of
 * the compacted topic, so all stream threads switch rules together.
 *
 * <p>Kafka Streams restores a global store from its topic without calling the processor, so
 * {@link #init} applies whatever the restored store already holds: a restart comes back with the
 * last published rules, not the file.
 */
public final class RulesConfigProcessor implements Processor<String, String, Void, Void> {
    public static final String STORE = "rules-config";
    public static final String KEY = "rules";

    private final RulesHolder holder;
    private final RulesMetrics metrics;
    private KeyValueStore<String, String> store;

    public RulesConfigProcessor(RulesHolder holder, RulesMetrics metrics) {
        this.holder = holder;
        this.metrics = metrics;
    }

    @Override
    public void init(ProcessorContext<Void, Void> context) {
        store = context.getStateStore(STORE);
        String restored = store.get(KEY);
        if (restored != null) apply(restored);
    }

    @Override
    public void process(Record<String, String> rec) {
        if (!KEY.equals(rec.key())) return;
        if (rec.value() == null) {
            store.delete(KEY);
            holder.reset();
            metrics.rulesReloaded("reset");
            return;
        }
        store.put(KEY, rec.value());
        apply(rec.value());
    }

    private void apply(String text) {
        List<String> problems = holder.publish(text);
        metrics.rulesReloaded(problems.isEmpty() ? "applied" : "refused");
    }
}
