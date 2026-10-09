package com.pip.rules.kafka;

import com.pip.rules.app.EventParser;
import com.pip.rules.app.IncidentJson;
import com.pip.rules.app.InvalidEventException;
import com.pip.rules.app.Json;
import com.pip.rules.domain.Event;
import com.pip.rules.domain.FootfallConfig;
import com.pip.rules.domain.FootfallHistory;
import com.pip.rules.domain.FootfallSpikeDetector;
import com.pip.rules.domain.Incident;
import com.pip.rules.domain.WindowCounts;
import org.apache.kafka.common.errors.SerializationException;
import org.apache.kafka.common.header.Headers;
import org.apache.kafka.common.header.internals.RecordHeaders;
import org.apache.kafka.common.serialization.Serde;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.utils.Bytes;
import org.apache.kafka.streams.KeyValue;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.kstream.Grouped;
import org.apache.kafka.streams.kstream.KStream;
import org.apache.kafka.streams.kstream.Materialized;
import org.apache.kafka.streams.kstream.Named;
import org.apache.kafka.streams.kstream.Suppressed;
import org.apache.kafka.streams.kstream.TimeWindows;
import org.apache.kafka.streams.kstream.Windowed;
import org.apache.kafka.streams.processor.PunctuationType;
import org.apache.kafka.streams.processor.api.Processor;
import org.apache.kafka.streams.processor.api.ProcessorContext;
import org.apache.kafka.streams.processor.api.ProcessorSupplier;
import org.apache.kafka.streams.processor.api.Record;
import org.apache.kafka.streams.state.KeyValueIterator;
import org.apache.kafka.streams.state.KeyValueStore;
import org.apache.kafka.streams.state.Stores;
import org.apache.kafka.streams.state.WindowStore;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * R-FOOT-001 with native Kafka Streams windows:
 *
 * <pre>
 * store.events.v1 (key = storeId)
 *   -> parse; every event of the store, ticks included, so stream time keeps moving
 *   -> groupByKey (no repartition: the key is already the store)
 *   -> windowedBy(TimeWindows.ofSizeAndGrace(window, grace)).aggregate(WindowCounts)
 *   -> suppress(untilWindowCloses): each window is emitted once, when stream time >= end + grace
 *   -> FootfallSpikeProcessor: per (store, run) history -> incidents.v1 | incidents.shadow.v1
 * </pre>
 *
 * Window closing follows Kafka Streams stream time, which is tracked per partition, not per
 * store or run (see docs/adr/011-windowing.md for what that means for replays).
 */
public final class FootfallTopology {
    public static final String COUNTS_STORE = "footfall-window-counts";
    public static final String HISTORY_STORE = "footfall-history";
    private static final String LIVE = "live";

    private FootfallTopology() {
    }

    /** One parsed store event, reduced to what the window needs. */
    public static final class FootfallEvent {
        public String run;
        public String countedZone;

        public FootfallEvent() {
        }
    }

    public static <T> Serde<T> json(Class<T> type) {
        return Serdes.serdeFrom(
                (topic, data) -> {
                    if (data == null) return null;
                    try {
                        return Json.MAPPER.writeValueAsBytes(data);
                    } catch (Exception ex) {
                        throw new SerializationException("cannot serialise " + type.getSimpleName(), ex);
                    }
                },
                (topic, bytes) -> {
                    if (bytes == null) return null;
                    try {
                        return Json.MAPPER.readValue(bytes, type);
                    } catch (Exception ex) {
                        throw new SerializationException("cannot deserialise " + type.getSimpleName(), ex);
                    }
                });
    }

    /** Adds the footfall branch to a topology reading {@code events} (store events keyed by store id). */
    public static void addTo(StreamsBuilder builder, KStream<String, String> events, FootfallConfig cfg, Topics topics,
                             RulesMetrics metrics, Duration idleTtl) {
        FootfallSpikeDetector detector = new FootfallSpikeDetector(cfg);
        addTo(builder, events, cfg, () -> detector, topics, metrics, idleTtl);
    }

    /**
     * R4: the window topology is built from {@code cfg} (window, grace and zones are structural and
     * cannot change while running); the detector, which holds mode, factor and minCount, is looked
     * up for every closed window, so a published rules document changes it without a restart.
     */
    public static void addTo(StreamsBuilder builder, KStream<String, String> events, FootfallConfig cfg,
                             java.util.function.Supplier<FootfallSpikeDetector> detector, Topics topics,
                             RulesMetrics metrics, Duration idleTtl) {
        builder.addStateStore(Stores.keyValueStoreBuilder(
                Stores.persistentKeyValueStore(HISTORY_STORE), Serdes.String(), json(FootfallHistory.class)));
        ProcessorSupplier<Windowed<String>, WindowCounts, String, String> spikes =
                () -> new FootfallSpikeProcessor(detector, topics, metrics, idleTtl);

        events
                .mapValues((key, json) -> toFootfallEvent(json, cfg), Named.as("footfall-parse"))
                .filter((key, ev) -> key != null && ev != null, Named.as("footfall-valid"))
                .groupByKey(Grouped.with("footfall-by-store", Serdes.String(), json(FootfallEvent.class)))
                .windowedBy(TimeWindows.ofSizeAndGrace(Duration.ofMillis(cfg.windowMs()), Duration.ofMillis(cfg.graceMs())))
                .aggregate(WindowCounts::new, (store, ev, agg) -> agg.add(ev.run, ev.countedZone),
                        Named.as("footfall-count"),
                        Materialized.<String, WindowCounts, WindowStore<Bytes, byte[]>>as(COUNTS_STORE)
                                .withKeySerde(Serdes.String())
                                .withValueSerde(json(WindowCounts.class))
                                .withCachingDisabled())
                .suppress(Suppressed.untilWindowCloses(Suppressed.BufferConfig.unbounded()).withName("footfall-final"))
                .toStream(Named.as("footfall-windows"))
                .process(spikes, Named.as("footfall-spikes"), HISTORY_STORE)
                .to(RulesTopology.router(), org.apache.kafka.streams.kstream.Produced.with(Serdes.String(), Serdes.String()));
    }

    static FootfallEvent toFootfallEvent(String json, FootfallConfig cfg) {
        Event e;
        try {
            e = EventParser.parse(json);
        } catch (InvalidEventException ex) {
            return null;
        }
        FootfallEvent ev = new FootfallEvent();
        ev.run = e.simRunId == null ? LIVE : e.simRunId;
        if (Event.ZONE_ENTERED.equals(e.type) && cfg.zones().contains(e.zoneId)) ev.countedZone = e.zoneId;
        return ev;
    }

    /** Applies each final window to the per-(store, run) history and routes the resulting incidents. */
    static final class FootfallSpikeProcessor implements Processor<Windowed<String>, WindowCounts, String, String> {
        private final java.util.function.Supplier<FootfallSpikeDetector> detector;
        private final Topics topics;
        private final RulesMetrics metrics;
        private final Duration idleTtl;
        private ProcessorContext<String, String> ctx;
        private KeyValueStore<String, FootfallHistory> store;

        FootfallSpikeProcessor(java.util.function.Supplier<FootfallSpikeDetector> detector, Topics topics,
                               RulesMetrics metrics, Duration idleTtl) {
            this.detector = detector;
            this.topics = topics;
            this.metrics = metrics;
            this.idleTtl = idleTtl;
        }

        @Override
        public void init(ProcessorContext<String, String> context) {
            this.ctx = context;
            this.store = context.getStateStore(HISTORY_STORE);
            // Wall clock only evicts idle simulation state; every rule decision is in event time.
            context.schedule(Duration.ofMinutes(1), PunctuationType.WALL_CLOCK_TIME, this::evictIdle);
        }

        @Override
        public void process(Record<Windowed<String>, WindowCounts> rec) {
            if (rec.key() == null || rec.value() == null) return;
            String storeId = rec.key().key();
            long start = rec.key().window().start();
            long end = rec.key().window().end();
            long wall = System.currentTimeMillis();
            FootfallSpikeDetector detector = this.detector.get();
            for (Map.Entry<String, java.util.TreeMap<String, Long>> run : rec.value().runs.entrySet()) {
                String runId = LIVE.equals(run.getKey()) ? null : run.getKey();
                String key = storeId + "|" + run.getKey();
                FootfallHistory h = store.get(key);
                if (h == null) h = new FootfallHistory();
                List<Incident> incidents = detector.onWindow(h, storeId, runId, start, end, run.getValue(), wall);
                store.put(key, h);
                for (Incident inc : incidents) {
                    Headers headers = new RecordHeaders();
                    headers.add(StoreRulesProcessor.ROUTE_HEADER,
                            ("shadow".equals(inc.mode) ? topics.shadow() : topics.incidents()).getBytes(StandardCharsets.UTF_8));
                    headers.add("pip-incident-kind", inc.kind.getBytes(StandardCharsets.UTF_8));
                    ctx.forward(new Record<>(inc.storeId, IncidentJson.toJson(inc), inc.detectedMs, headers));
                    metrics.incident(inc);
                }
            }
        }

        private void evictIdle(long wallMs) {
            List<String> idle = new ArrayList<>();
            try (KeyValueIterator<String, FootfallHistory> it = store.all()) {
                while (it.hasNext()) {
                    KeyValue<String, FootfallHistory> kv = it.next();
                    if (kv.value.simRunId != null && kv.value.lastWallMs < wallMs - idleTtl.toMillis()) idle.add(kv.key);
                }
            }
            idle.forEach(store::delete);
        }
    }
}
