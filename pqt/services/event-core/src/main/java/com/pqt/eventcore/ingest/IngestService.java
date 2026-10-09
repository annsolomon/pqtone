package com.pqt.eventcore.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.audit.AuditService;
import com.pqt.eventcore.config.PqtProperties;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The single write path. One transaction: dedup ledger -> event row -> outbox row.
 * Same (source, id) and same canonical payload => duplicate (idempotent success);
 * same (source, id) with a different payload => conflict (rejected and audited).
 */
@Service
public class IngestService {
    public enum Status { ACCEPTED, DUPLICATE, CONFLICT }

    public record Result(Status status, String source, String id) {
    }

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final AuditService audit;
    private final PqtProperties props;
    private final ObjectMapper mapper;
    private final MeterRegistry meters;
    private final Timer timer;

    public IngestService(JdbcTemplate jdbc, TransactionTemplate tx, AuditService audit, PqtProperties props,
                         ObjectMapper mapper, MeterRegistry meters) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.audit = audit;
        this.props = props;
        this.mapper = mapper;
        this.meters = meters;
        this.timer = Timer.builder("pqt.ingest.duration").description("Ingest transaction time")
                .publishPercentileHistogram().register(meters);
    }

    public Result ingest(ValidatedEvent e, String channel, String producer) {
        Result r = timer.record(() -> tx.execute(status -> write(e, channel, producer)));
        meters.counter("pqt.ingest.events", "result", r.status().name().toLowerCase(), "channel", channel).increment();
        return r;
    }

    private Result write(ValidatedEvent e, String channel, String producer) {
        int inserted = jdbc.update(
                "INSERT INTO pqt.event_dedup (source, id, payload_sha256) VALUES (?, ?, ?) ON CONFLICT (source, id) DO NOTHING",
                e.source(), e.id(), e.payloadHash());
        if (inserted == 0) {
            String existing = jdbc.queryForObject(
                    "SELECT payload_sha256 FROM pqt.event_dedup WHERE source = ? AND id = ?", String.class, e.source(), e.id());
            if (e.payloadHash().equals(existing)) return new Result(Status.DUPLICATE, e.source(), e.id());
            Map<String, Object> details = new LinkedHashMap<>();
            details.put("channel", channel);
            details.put("producer", producer);
            details.put("storedSha256", existing);
            details.put("receivedSha256", e.payloadHash());
            audit.record("system:" + producer, "ingest.conflicting-duplicate", e.source() + "#" + e.id(), details);
            return new Result(Status.CONFLICT, e.source(), e.id());
        }
        String json = Canonical.json(e.node());
        jdbc.update("""
                INSERT INTO pqt.event (source, id, type, event_time, subject, store_id, sim_run_id, dataschema,
                                       data, traceparent, channel, producer)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?)""",
                e.source(), e.id(), e.type(), e.time().atOffset(ZoneOffset.UTC), e.subject(), e.storeId(),
                e.simRunId(), e.dataschema(), Canonical.json(e.node().get("data")), e.traceparent(), channel, producer);
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("ce_id", e.id());
        headers.put("ce_source", e.source());
        headers.put("ce_type", e.type());
        if (e.traceparent() != null) headers.put("traceparent", e.traceparent());
        try {
            jdbc.update("INSERT INTO pqt.outbox (topic, msg_key, payload, headers) VALUES (?, ?, ?, ?::jsonb)",
                    props.topics().validated(), e.storeId(), json, mapper.writeValueAsString(headers));
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException(ex);
        }
        return new Result(Status.ACCEPTED, e.source(), e.id());
    }
}
