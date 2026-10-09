package com.pqt.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.pqt.rules.domain.Event;

import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;

/**
 * Parses a CloudEvent (structured JSON) into the rules' Event. In the live pipeline only
 * events already validated by event-core arrive here; the checks below are defensive and
 * also let the offline runner reject the same malformed inputs event-core would.
 */
public final class EventParser {
    static final Set<String> KNOWN_TYPES = Set.of(
            "com.pqt.store.zone.entered", "com.pqt.store.zone.exited",
            "com.pqt.store.queue.joined", "com.pqt.store.queue.left", "com.pqt.store.queue.length",
            "com.pqt.store.register.opened", "com.pqt.store.register.closed", "com.pqt.store.clock.tick");

    private EventParser() {
    }

    public static Event parse(String json) throws InvalidEventException {
        JsonNode n;
        try {
            n = Json.MAPPER.readTree(json);
        } catch (Exception ex) {
            throw new InvalidEventException("not JSON");
        }
        if (n == null || !n.isObject()) throw new InvalidEventException("not an object");
        Event e = new Event();
        e.id = text(n, "id", true);
        e.source = text(n, "source", true);
        e.type = text(n, "type", true);
        if (!KNOWN_TYPES.contains(e.type)) throw new InvalidEventException("unknown type " + e.type);
        String time = text(n, "time", true);
        try {
            e.timeMs = Instant.parse(time).toEpochMilli();
        } catch (DateTimeParseException ex) {
            throw new InvalidEventException("bad time");
        }
        e.storeId = text(n, "storeid", true);
        e.simRunId = text(n, "simrunid", false);
        e.sequence = text(n, "sequence", false);
        e.subject = text(n, "subject", false);
        JsonNode data = n.get("data");
        if (data == null || !data.isObject()) throw new InvalidEventException("data must be an object");
        switch (e.type) {
            case "com.pqt.store.queue.length" -> {
                e.queueId = text(data, "queueId", true);
                JsonNode len = data.get("length");
                if (len == null || !len.isIntegralNumber() || len.asInt() < 0) throw new InvalidEventException("bad length");
                e.length = len.asInt();
                if (!data.path("openRegisters").isIntegralNumber()) throw new InvalidEventException("bad openRegisters");
            }
            case "com.pqt.store.zone.entered", "com.pqt.store.zone.exited" -> {
                e.zoneId = text(data, "zoneId", true);
                e.trackId = text(data, "trackId", true);
            }
            case "com.pqt.store.queue.joined", "com.pqt.store.queue.left" -> {
                e.queueId = text(data, "queueId", true);
                e.trackId = text(data, "trackId", true);
            }
            case "com.pqt.store.register.opened", "com.pqt.store.register.closed" ->
                    e.registerId = text(data, "registerId", true);
            case "com.pqt.store.clock.tick" -> {
                if (!data.path("seq").isIntegralNumber()) throw new InvalidEventException("bad seq");
            }
            default -> throw new InvalidEventException("unhandled type");
        }
        return e;
    }

    private static String text(JsonNode n, String field, boolean required) throws InvalidEventException {
        JsonNode v = n.get(field);
        if (v == null || v.isNull()) {
            if (required) throw new InvalidEventException("missing " + field);
            return null;
        }
        if (!v.isTextual() || v.asText().isEmpty()) throw new InvalidEventException("bad " + field);
        return v.asText();
    }
}
