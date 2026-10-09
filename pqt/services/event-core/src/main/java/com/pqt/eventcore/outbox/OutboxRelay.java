package com.pqt.eventcore.outbox;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.config.PqtProperties;
import io.micrometer.core.instrument.MeterRegistry;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Transactional outbox relay. Rows are claimed with FOR UPDATE SKIP LOCKED (safe with many
 * instances), published with an idempotent producer, and marked only after every send in the
 * batch is acknowledged. A crash between send and mark republishes: at-least-once, and every
 * consumer is idempotent on (source, id) or incident_id.
 */
@Component
public class OutboxRelay {
    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);
    private static final int BATCH = 500;

    private record Row(long id, String topic, String key, String payload, String headers) {
    }

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final KafkaTemplate<String, String> kafka;
    private final ObjectMapper mapper;
    private final PqtProperties props;
    private final AtomicLong backlog = new AtomicLong();

    public OutboxRelay(JdbcTemplate jdbc, TransactionTemplate tx, KafkaTemplate<String, String> kafka,
                       ObjectMapper mapper, PqtProperties props, MeterRegistry meters) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.kafka = kafka;
        this.mapper = mapper;
        this.props = props;
        meters.gauge("pqt.outbox.unpublished.rows", backlog);
    }

    @Scheduled(fixedDelayString = "${pqt.outbox.poll-ms:200}")
    public void relay() {
        try {
            Integer n;
            do {
                n = tx.execute(status -> relayBatch());
            } while (n != null && n == BATCH);
        } catch (Exception ex) {
            log.warn("outbox relay pass failed; will retry: {}", ex.toString());
        }
    }

    private int relayBatch() {
        List<Row> rows = jdbc.query("""
                SELECT outbox_id, topic, msg_key, payload, headers::text
                  FROM pqt.outbox
                 WHERE published_at IS NULL
                 ORDER BY outbox_id
                 LIMIT ?
                   FOR UPDATE SKIP LOCKED""",
                (rs, i) -> new Row(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5)),
                BATCH);
        if (rows.isEmpty()) return 0;
        List<CompletableFuture<SendResult<String, String>>> futures = new ArrayList<>(rows.size());
        for (Row r : rows) {
            ProducerRecord<String, String> rec = new ProducerRecord<>(r.topic(), r.key(), r.payload());
            try {
                JsonNode h = mapper.readTree(r.headers());
                Iterator<Map.Entry<String, JsonNode>> it = h.fields();
                while (it.hasNext()) {
                    Map.Entry<String, JsonNode> en = it.next();
                    rec.headers().add(en.getKey(), en.getValue().asText().getBytes(StandardCharsets.UTF_8));
                }
            } catch (Exception ex) {
                log.warn("outbox row {} has unreadable headers; publishing without them", r.id());
            }
            futures.add(kafka.send(rec));
        }
        try {
            CompletableFuture.allOf(futures.toArray(new CompletableFuture[0])).get(30, TimeUnit.SECONDS);
        } catch (Exception ex) {
            throw new IllegalStateException("outbox publish failed; batch will be retried", ex);
        }
        jdbc.batchUpdate("UPDATE pqt.outbox SET published_at = now() WHERE outbox_id = ?",
                rows.stream().map(r -> new Object[]{r.id()}).toList());
        return rows.size();
    }

    @Scheduled(fixedDelay = 15_000L)
    public void measureBacklog() {
        Long n = jdbc.queryForObject("SELECT count(*) FROM pqt.outbox WHERE published_at IS NULL", Long.class);
        backlog.set(n == null ? 0 : n);
    }

    @Scheduled(fixedDelay = 600_000L, initialDelay = 60_000L)
    public void purgePublished() {
        int n = jdbc.update("DELETE FROM pqt.outbox WHERE published_at < now() - make_interval(hours => ?)",
                props.retention().outboxHours());
        if (n > 0) log.info("purged {} published outbox rows", n);
    }
}
