package com.pqt.eventcore.it;

import com.pqt.eventcore.ingest.IngestService;
import com.pqt.eventcore.outbox.OutboxRelay;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.testcontainers.redpanda.RedpandaContainer;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Milestone E4: the transactional outbox against real PostgreSQL and Redpanda. With the broker
 * unreachable nothing is marked published; when it returns, the backlog drains and every event
 * reaches the topic (at-least-once: duplicates are allowed, gaps are not).
 */
class OutboxRelayIT {
    /** Same image as deploy/compose/docker-compose.yml. */
    static final String REDPANDA_IMAGE = "docker.redpanda.com/redpandadata/redpanda:v24.2.7";

    static Db db;
    static RedpandaContainer kafka;
    static JdbcTemplate jdbc;
    static IngestService ingest;
    static OutboxRelay relay;
    static DefaultKafkaProducerFactory<String, String> producers;

    @BeforeAll
    static void start() throws Exception {
        db = new Db().start();
        db.migrate();
        kafka = new RedpandaContainer(REDPANDA_IMAGE);
        kafka.start();
        try (AdminClient admin = AdminClient.create(Map.of(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.getBootstrapServers()))) {
            admin.createTopics(List.of(new NewTopic(Fixtures.VALIDATED_TOPIC, 3, (short) 1))).all().get();
        }
        Map<String, Object> p = new HashMap<>();
        p.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.getBootstrapServers());
        p.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        p.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        p.put(ProducerConfig.ACKS_CONFIG, "all");
        p.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        // Short timeouts so an outage shows up in seconds instead of the production 30 s.
        p.put(ProducerConfig.REQUEST_TIMEOUT_MS_CONFIG, 3000);
        p.put(ProducerConfig.DELIVERY_TIMEOUT_MS_CONFIG, 6000);
        p.put(ProducerConfig.MAX_BLOCK_MS_CONFIG, 4000);
        p.put(ProducerConfig.LINGER_MS_CONFIG, 0);
        producers = new DefaultKafkaProducerFactory<>(p);
        jdbc = new JdbcTemplate(db.app());
        ingest = Fixtures.ingest(db.app());
        relay = new OutboxRelay(jdbc, Fixtures.tx(db.app()), new KafkaTemplate<>(producers), Fixtures.MAPPER,
                Fixtures.props(), new SimpleMeterRegistry());
    }

    @AfterAll
    static void stop() {
        if (producers != null) producers.destroy();
        if (kafka != null) kafka.stop();
        if (db != null) db.close();
    }

    long unpublished() {
        Long n = jdbc.queryForObject("SELECT count(*) FROM pqt.outbox WHERE published_at IS NULL", Long.class);
        return n == null ? 0 : n;
    }

    Set<String> ingestMany(String prefix, int n) {
        Set<String> ids = new HashSet<>();
        for (int i = 0; i < n; i++) {
            String id = prefix + "-" + i;
            assertEquals(IngestService.Status.ACCEPTED, ingest.ingest(Fixtures.event(id, i % 7), "http", "it").status());
            ids.add(id);
        }
        return ids;
    }

    /** Every ce_id header seen on the topic until all expected ids are there or the deadline passes. */
    Set<String> consumeUntil(Set<String> expected, Duration timeout) {
        Map<String, Object> c = new HashMap<>();
        c.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.getBootstrapServers());
        c.put(ConsumerConfig.GROUP_ID_CONFIG, "it-" + UUID.randomUUID());
        c.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        c.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        c.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        Set<String> seen = new HashSet<>();
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(c)) {
            consumer.subscribe(List.of(Fixtures.VALIDATED_TOPIC));
            long deadline = System.nanoTime() + timeout.toNanos();
            while (!seen.containsAll(expected) && System.nanoTime() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    var h = r.headers().lastHeader("ce_id");
                    if (h != null) seen.add(new String(h.value(), StandardCharsets.UTF_8));
                    assertEquals("store-it", r.key(), "events are keyed by store");
                }
            }
        }
        return seen;
    }

    @Test
    void backlogBuiltWhileTheBrokerIsDownDrainsWhenItReturns() throws Exception {
        // 1. Broker up: the relay publishes and marks everything.
        Set<String> before = ingestMany("up", 50);
        relay.relay();
        assertEquals(0, unpublished());

        // 2. Broker unreachable: sends fail, the transaction rolls back, rows stay unpublished.
        kafka.getDockerClient().pauseContainerCmd(kafka.getContainerId()).exec();
        Set<String> during;
        try {
            during = ingestMany("down", 30);
            relay.relay();
            assertEquals(30, unpublished(), "nothing may be marked published while the broker is down");
        } finally {
            kafka.getDockerClient().unpauseContainerCmd(kafka.getContainerId()).exec();
        }

        // 3. Broker back: the backlog drains.
        long deadline = System.nanoTime() + Duration.ofSeconds(90).toNanos();
        while (unpublished() > 0 && System.nanoTime() < deadline) {
            relay.relay();
            if (unpublished() > 0) Thread.sleep(500);
        }
        assertEquals(0, unpublished(), "the backlog must drain once the broker is back");

        // 4. Every event reached the topic, none missing.
        Set<String> expected = new HashSet<>(before);
        expected.addAll(during);
        Set<String> seen = consumeUntil(expected, Duration.ofSeconds(60));
        Set<String> missing = new HashSet<>(expected);
        missing.removeAll(seen);
        assertTrue(missing.isEmpty(), "missing on the topic: " + missing);
    }
}
