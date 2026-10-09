package com.pip.eventcore.it;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pip.eventcore.audit.AuditService;
import com.pip.eventcore.config.PipProperties;
import com.pip.eventcore.ingest.Canonical;
import com.pip.eventcore.ingest.IngestService;
import com.pip.eventcore.ingest.ValidatedEvent;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/** Builds event-core's write path by hand, without a Spring context, against a given DataSource. */
final class Fixtures {
    static final ObjectMapper MAPPER = new ObjectMapper();
    static final String VALIDATED_TOPIC = "store.events.v1";

    private Fixtures() {
    }

    static PipProperties props() {
        return new PipProperties(
                new PipProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500, 65536, 1048576, 100, 200, 1,
                        "urn:pip:sim:", Map.of(), List.of()),
                new PipProperties.Topics("store.raw.v1", VALIDATED_TOPIC, "store.dlq.v1", "incidents.v1",
                        "incidents.shadow.v1", "rules.heartbeat.v1"),
                null, null, new PipProperties.Retention(30, 24), null);
    }

    static TransactionTemplate tx(DataSource ds) {
        return new TransactionTemplate(new DataSourceTransactionManager(ds));
    }

    static IngestService ingest(DataSource ds) {
        JdbcTemplate jdbc = new JdbcTemplate(ds);
        return new IngestService(jdbc, tx(ds), new AuditService(jdbc, MAPPER, Clock.systemUTC()), props(), MAPPER,
                new SimpleMeterRegistry());
    }

    /** A queue.length event as the validator would hand it over. Synthetic ids only. */
    static ValidatedEvent event(String id, int length) {
        try {
            ObjectNode n = (ObjectNode) MAPPER.readTree("""
                    {"specversion":"1.0","id":"%s","source":"urn:pip:edge:store-it:gw-1",
                     "type":"com.pip.store.queue.length","time":"2026-01-01T09:00:00.000Z","subject":"queue:checkout-1",
                     "dataschema":"https://schemas.pip.local/store/queue.length/1.0.0","datacontenttype":"application/json",
                     "storeid":"store-it","partitionkey":"store-it",
                     "data":{"queueId":"checkout-1","length":%d,"openRegisters":1}}""".formatted(id, length));
            return new ValidatedEvent(id, n.get("source").asText(), n.get("type").asText(),
                    Instant.parse(n.get("time").asText()), n.get("subject").asText(), n.get("dataschema").asText(),
                    "store-it", null, null, n, Canonical.eventHash(n));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
