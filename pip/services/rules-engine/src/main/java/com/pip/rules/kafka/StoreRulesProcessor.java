package com.pip.rules.kafka;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pip.rules.app.EventParser;
import com.pip.rules.app.IncidentJson;
import com.pip.rules.app.InvalidEventException;
import com.pip.rules.app.Json;
import com.pip.rules.domain.Event;
import com.pip.rules.domain.Incident;
import com.pip.rules.domain.RuleEngine;
import com.pip.rules.domain.StoreState;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.header.Headers;
import org.apache.kafka.common.header.internals.RecordHeaders;
import org.apache.kafka.streams.KeyValue;
import org.apache.kafka.streams.processor.PunctuationType;
import org.apache.kafka.streams.processor.api.Processor;
import org.apache.kafka.streams.processor.api.ProcessorContext;
import org.apache.kafka.streams.processor.api.Record;
import org.apache.kafka.streams.state.KeyValueIterator;
import org.apache.kafka.streams.state.KeyValueStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/**
 * Applies the rule engine per (store, run) and routes output via the pip-route header.
 * Wall-clock punctuation is used only for the liveness heartbeat and idle-state eviction;
 * every rule decision is made in event time.
 */
public final class StoreRulesProcessor implements Processor<String, String, String, String> {
    public static final String STORE = "store-rules-state";
    public static final String ROUTE_HEADER = "pip-route";
    private static final Logger log = LoggerFactory.getLogger(StoreRulesProcessor.class);
    private static final int HEARTBEAT_MAX_KEYS = 500;

    private final RuleEngine engine;
    private final Topics topics;
    private final RulesMetrics metrics;
    private final String instanceId;
    private final Duration idleTtl;
    private ProcessorContext<String, String> ctx;
    private KeyValueStore<String, StoreState> store;

    public StoreRulesProcessor(RuleEngine engine, Topics topics, RulesMetrics metrics, String instanceId, Duration idleTtl) {
        this.engine = engine;
        this.topics = topics;
        this.metrics = metrics;
        this.instanceId = instanceId;
        this.idleTtl = idleTtl;
    }

    @Override
    public void init(ProcessorContext<String, String> context) {
        this.ctx = context;
        this.store = context.getStateStore(STORE);
        context.schedule(Duration.ofSeconds(5), PunctuationType.WALL_CLOCK_TIME, this::heartbeat);
        context.schedule(Duration.ofMinutes(1), PunctuationType.WALL_CLOCK_TIME, this::evictIdle);
    }

    @Override
    public void process(Record<String, String> rec) {
        metrics.received();
        Event e;
        try {
            e = EventParser.parse(rec.value());
        } catch (InvalidEventException ex) {
            metrics.invalid();
            log.warn("dropping unparseable event: {}", ex.getMessage());
            return;
        }
        String key = Event.stateKey(e);
        StoreState s = store.get(key);
        if (s == null) s = new StoreState();
        RuleEngine.Outcome outcome = engine.onEvent(s, e, System.currentTimeMillis());
        store.put(key, s);
        if (outcome.late) metrics.late(e.storeId);
        Header traceparent = rec.headers().lastHeader("traceparent");
        for (Incident inc : outcome.incidents) {
            Headers h = new RecordHeaders();
            if (traceparent != null) h.add(traceparent);
            h.add(ROUTE_HEADER, ("shadow".equals(inc.mode) ? topics.shadow() : topics.incidents()).getBytes(StandardCharsets.UTF_8));
            h.add("pip-incident-kind", inc.kind.getBytes(StandardCharsets.UTF_8));
            ctx.forward(new Record<>(inc.storeId, IncidentJson.toJson(inc), inc.detectedMs, h));
            metrics.incident(inc);
        }
    }

    private void heartbeat(long wallMs) {
        ObjectNode hb = Json.MAPPER.createObjectNode();
        String taskId = ctx.taskId().toString();
        hb.put("taskId", taskId);
        hb.put("instance", instanceId);
        hb.put("at", Instant.ofEpochMilli(wallMs).toString());
        hb.put("atMs", wallMs);
        ArrayNode keys = hb.putArray("keys");
        try (KeyValueIterator<String, StoreState> it = store.all()) {
            int n = 0;
            while (it.hasNext() && n++ < HEARTBEAT_MAX_KEYS) {
                KeyValue<String, StoreState> kv = it.next();
                ObjectNode k = keys.addObject();
                k.put("key", kv.key);
                k.put("watermarkMs", kv.value.watermarkMs);
                k.put("streamTimeMs", kv.value.streamTimeMs);
                k.put("buffered", kv.value.buffer.size());
                k.put("lateDropped", kv.value.lateDropped);
                k.put("processed", kv.value.processed);
            }
        }
        Headers h = new RecordHeaders();
        h.add(ROUTE_HEADER, topics.heartbeat().getBytes(StandardCharsets.UTF_8));
        ctx.forward(new Record<>(taskId, hb.toString(), wallMs, h));
        metrics.heartbeat(wallMs);
    }

    private void evictIdle(long wallMs) {
        List<String> idle = new ArrayList<>();
        try (KeyValueIterator<String, StoreState> it = store.all()) {
            while (it.hasNext()) {
                KeyValue<String, StoreState> kv = it.next();
                if (kv.value.simRunId != null && kv.value.lastWallMs < wallMs - idleTtl.toMillis()) idle.add(kv.key);
            }
        }
        for (String k : idle) {
            store.delete(k);
            log.info("evicted idle simulation state {}", k);
        }
    }
}
