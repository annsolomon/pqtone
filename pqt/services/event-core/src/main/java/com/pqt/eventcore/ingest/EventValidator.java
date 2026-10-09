package com.pqt.eventcore.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.networknt.schema.JsonSchema;
import com.pqt.eventcore.config.PqtProperties;

import java.time.Clock;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Contract enforcement: envelope schema, known type, allowed dataschema version, data schema,
 * subject format, time bounds and simulation tagging. Collects all errors.
 */
public final class EventValidator {
    private final SchemaRegistry registry;
    private final PqtProperties.Ingest cfg;
    private final Clock clock;

    public EventValidator(SchemaRegistry registry, PqtProperties.Ingest cfg, Clock clock) {
        this.registry = registry;
        this.cfg = cfg;
        this.clock = clock;
    }

    public ValidatedEvent validate(JsonNode n) {
        if (n == null || !n.isObject()) throw new InvalidEventException(List.of("event must be a JSON object"));
        List<String> errors = new ArrayList<>(registry.validateEnvelope(n));
        String type = n.path("type").asText(null);
        String dataschema = n.path("dataschema").asText(null);
        Optional<SchemaRegistry.TypeEntry> entry = type == null ? Optional.empty() : registry.type(type);
        if (type != null && entry.isEmpty()) errors.add("type: unknown event type " + type);
        entry.ifPresent(t -> {
            JsonSchema schema = t.dataSchemas().get(dataschema);
            if (schema == null) {
                errors.add("dataschema: " + dataschema + " is not registered for " + type);
            } else if (n.path("data").isObject()) {
                errors.addAll(SchemaRegistry.messages(schema.validate(n.get("data")), "data"));
            }
            String subject = n.path("subject").asText(null);
            if (subject == null || !t.subjectPattern().matcher(subject).matches()) {
                errors.add("subject: must match " + t.subjectPattern().pattern());
            }
        });

        Instant time = null;
        if (n.path("time").isTextual()) {
            try {
                time = Instant.parse(n.get("time").asText());
            } catch (DateTimeParseException e) {
                errors.add("time: not an RFC 3339 UTC timestamp");
            }
        }
        String source = n.path("source").asText("");
        String simRunId = n.path("simrunid").asText(null);
        boolean simSource = source.startsWith(cfg.simSourcePrefix());
        if (simRunId != null && !simSource) errors.add("simrunid: only simulator sources may set simrunid");
        if (simSource && simRunId == null) errors.add("simrunid: required for simulator sources");
        if (time != null) {
            Instant now = clock.instant();
            if (time.isAfter(now.plus(cfg.maxFutureSkew()))) errors.add("time: more than " + cfg.maxFutureSkew() + " in the future");
            // Simulated runs replay a fixed historical epoch for determinism; the age bound applies to real producers.
            if (!simSource && time.isBefore(now.minus(cfg.maxAge()))) errors.add("time: older than " + cfg.maxAge());
        }
        String storeId = n.path("storeid").asText(null);
        if (storeId != null && !storeId.equals(n.path("partitionkey").asText(null))) {
            errors.add("partitionkey: must equal storeid");
        }
        if (!errors.isEmpty()) throw new InvalidEventException(errors);

        return new ValidatedEvent(n.get("id").asText(), source, type, time, n.path("subject").asText(null),
                dataschema, storeId, simRunId, n.path("traceparent").asText(null), n, Canonical.eventHash(n));
    }
}
