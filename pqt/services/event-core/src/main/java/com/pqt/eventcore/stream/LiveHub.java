package com.pqt.eventcore.stream;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Server-sent events fan-out for the console. Store events are coalesced into batches every
 * 250 ms (at most 50 per batch, the rest counted as dropped) so a replay at full speed cannot
 * flood browsers; incident changes are pushed immediately.
 */
@Component
public class LiveHub {
    private static final int MAX_PENDING = 2_000;
    private static final int MAX_PER_BATCH = 50;

    private final Set<SseEmitter> emitters = ConcurrentHashMap.newKeySet();
    private final ConcurrentLinkedQueue<JsonNode> pending = new ConcurrentLinkedQueue<>();
    private final AtomicInteger pendingSize = new AtomicInteger();
    private final AtomicInteger dropped = new AtomicInteger();
    private final ObjectMapper mapper;

    public LiveHub(ObjectMapper mapper, MeterRegistry meters) {
        this.mapper = mapper;
        meters.gauge("pqt.sse.clients", emitters, Set::size);
    }

    public SseEmitter subscribe() {
        SseEmitter em = new SseEmitter(Duration.ofMinutes(30).toMillis());
        emitters.add(em);
        em.onCompletion(() -> emitters.remove(em));
        em.onTimeout(() -> emitters.remove(em));
        em.onError(e -> emitters.remove(em));
        try {
            em.send(SseEmitter.event().name("hello").data(Map.of("ok", true), MediaType.APPLICATION_JSON));
        } catch (IOException e) {
            emitters.remove(em);
        }
        return em;
    }

    /** Called for every validated store event; keeps only what the console needs. */
    public void storeEvent(JsonNode ce) {
        if (emitters.isEmpty()) return;
        if (pendingSize.incrementAndGet() > MAX_PENDING) {
            pendingSize.decrementAndGet();
            dropped.incrementAndGet();
            return;
        }
        ObjectNode n = mapper.createObjectNode();
        n.put("id", ce.path("id").asText());
        n.put("type", ce.path("type").asText().replace("com.pqt.store.", ""));
        n.put("time", ce.path("time").asText());
        n.put("storeId", ce.path("storeid").asText());
        n.put("subject", ce.path("subject").asText());
        n.put("simRunId", ce.path("simrunid").asText(null));
        n.set("data", ce.path("data"));
        pending.add(n);
    }

    public void incident(Object view, String change) {
        broadcast("incident", Map.of("change", change, "incident", view));
    }

    @Scheduled(fixedRate = 250L)
    public void flush() {
        if (pending.isEmpty()) return;
        List<JsonNode> batch = new ArrayList<>(MAX_PER_BATCH);
        JsonNode n;
        int drained = 0;
        while ((n = pending.poll()) != null) {
            pendingSize.decrementAndGet();
            drained++;
            if (batch.size() < MAX_PER_BATCH) batch.add(n);
        }
        int skipped = drained - batch.size() + dropped.getAndSet(0);
        broadcast("events", Map.of("items", batch, "skipped", skipped));
    }

    @Scheduled(fixedRate = 15_000L)
    public void keepAlive() {
        for (SseEmitter em : emitters) {
            try {
                em.send(SseEmitter.event().comment("keep-alive"));
            } catch (Exception e) {
                emitters.remove(em);
            }
        }
    }

    private void broadcast(String name, Object payload) {
        for (SseEmitter em : emitters) {
            try {
                em.send(SseEmitter.event().name(name).data(payload, MediaType.APPLICATION_JSON));
            } catch (Exception e) {
                emitters.remove(em);
            }
        }
    }
}
