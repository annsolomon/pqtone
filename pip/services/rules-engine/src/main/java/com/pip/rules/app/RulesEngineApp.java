package com.pip.rules.app;

import com.pip.rules.domain.FootfallConfig;
import com.pip.rules.domain.RuleConfig;
import com.pip.rules.kafka.RulesHolder;
import com.pip.rules.kafka.RulesMetrics;
import com.pip.rules.kafka.RulesTopology;
import com.pip.rules.kafka.Topics;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.binder.jvm.JvmGcMetrics;
import io.micrometer.core.instrument.binder.jvm.JvmMemoryMetrics;
import io.micrometer.core.instrument.binder.jvm.JvmThreadMetrics;
import io.micrometer.core.instrument.binder.kafka.KafkaStreamsMetrics;
import io.micrometer.core.instrument.binder.system.ProcessorMetrics;
import io.micrometer.prometheusmetrics.PrometheusConfig;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import org.apache.kafka.clients.CommonClientConfigs;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.config.SaslConfigs;
import org.apache.kafka.streams.KafkaStreams;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.errors.LogAndContinueExceptionHandler;
import org.apache.kafka.streams.errors.StreamsUncaughtExceptionHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Optional;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;

/** Entry point: Kafka Streams application plus a small /metrics and /health HTTP server. */
public final class RulesEngineApp {
    private static final Logger log = LoggerFactory.getLogger(RulesEngineApp.class);

    private RulesEngineApp() {
    }

    static String env(String name, String def) {
        String v = System.getenv(name);
        if (v == null || v.isBlank()) {
            if (def == null) throw new IllegalStateException("missing required environment variable " + name);
            return def;
        }
        return v;
    }

    static Properties streamsProperties() {
        Properties p = new Properties();
        p.put(StreamsConfig.APPLICATION_ID_CONFIG, env("PIP_APP_ID", "rules-engine"));
        p.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, env("PIP_KAFKA_BOOTSTRAP", null));
        p.put(StreamsConfig.PROCESSING_GUARANTEE_CONFIG, StreamsConfig.EXACTLY_ONCE_V2);
        p.put(StreamsConfig.STATE_DIR_CONFIG, env("PIP_STREAMS_STATE_DIR", "/var/lib/rules-engine/state"));
        p.put(StreamsConfig.REPLICATION_FACTOR_CONFIG, Integer.parseInt(env("PIP_REPLICATION_FACTOR", "1")));
        p.put(StreamsConfig.NUM_STANDBY_REPLICAS_CONFIG, Integer.parseInt(env("PIP_STANDBY_REPLICAS", "0")));
        p.put(StreamsConfig.NUM_STREAM_THREADS_CONFIG, Integer.parseInt(env("PIP_STREAM_THREADS", "2")));
        p.put(StreamsConfig.COMMIT_INTERVAL_MS_CONFIG, 100);
        p.put(StreamsConfig.DEFAULT_DESERIALIZATION_EXCEPTION_HANDLER_CLASS_CONFIG, LogAndContinueExceptionHandler.class);
        p.put(StreamsConfig.producerPrefix(ProducerConfig.COMPRESSION_TYPE_CONFIG), "zstd");
        String protocol = env("PIP_KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT");
        p.put(CommonClientConfigs.SECURITY_PROTOCOL_CONFIG, protocol);
        if (protocol.startsWith("SASL")) {
            String user = env("PIP_KAFKA_USER", null);
            String pass = env("PIP_KAFKA_PASSWORD", null);
            if (user.contains("\"") || pass.contains("\"")) throw new IllegalStateException("quotes not allowed in SASL credentials");
            p.put(SaslConfigs.SASL_MECHANISM, "SCRAM-SHA-256");
            p.put(SaslConfigs.SASL_JAAS_CONFIG, "org.apache.kafka.common.security.scram.ScramLoginModule required "
                    + "username=\"" + user + "\" password=\"" + pass + "\";");
        }
        return p;
    }

    public static void main(String[] args) throws Exception {
        Path rulesFile = Path.of(env("PIP_RULES_FILE", "/config/rules.yaml"));
        RuleSet fileRules = RuleSet.parse(java.nio.file.Files.readString(rulesFile));
        RuleConfig cfg = fileRules.core();
        Optional<FootfallConfig> footfall = fileRules.footfall();
        RulesHolder rules = new RulesHolder(fileRules);
        // Milestone R4: hot reload from the compacted config topic; "off" (or empty) turns it off.
        Optional<String> configTopic = Optional.of(System.getenv().getOrDefault("PIP_TOPIC_RULES_CONFIG", "rules.config.v1"))
                .map(String::trim).filter(t -> !t.isEmpty() && !t.equals("off"));
        Topics topics = new Topics(
                env("PIP_TOPIC_VALIDATED", "store.events.v1"),
                env("PIP_TOPIC_INCIDENTS", "incidents.v1"),
                env("PIP_TOPIC_SHADOW", "incidents.shadow.v1"),
                env("PIP_TOPIC_HEARTBEAT", "rules.heartbeat.v1"));
        String instanceId = env("HOSTNAME", "rules-engine") + "-" + UUID.randomUUID().toString().substring(0, 8);

        PrometheusMeterRegistry registry = new PrometheusMeterRegistry(PrometheusConfig.DEFAULT);
        registry.config().commonTags("service", "rules-engine");
        new JvmMemoryMetrics().bindTo(registry);
        new JvmGcMetrics().bindTo(registry);
        new JvmThreadMetrics().bindTo(registry);
        new ProcessorMetrics().bindTo(registry);
        RulesMetrics metrics = new RulesMetrics(registry);

        KafkaStreams streams = new KafkaStreams(
                RulesTopology.build(rules, configTopic, topics, metrics, instanceId,
                        Duration.parse(env("PIP_IDLE_STATE_TTL", "PT6H"))),
                streamsProperties());
        new KafkaStreamsMetrics(streams).bindTo(registry);
        streams.setUncaughtExceptionHandler(ex -> {
            log.error("stream thread failed; replacing it", ex);
            return StreamsUncaughtExceptionHandler.StreamThreadExceptionResponse.REPLACE_THREAD;
        });
        streams.setStateListener((now, before) -> log.info("streams state {} -> {}", before, now));

        HttpServer http = HttpServer.create(new InetSocketAddress(Integer.parseInt(env("PIP_METRICS_PORT", "8082"))), 0);
        http.createContext("/metrics", ex -> respond(ex, 200, registry.scrape(), "text/plain; version=0.0.4"));
        http.createContext("/health/live", ex -> {
            KafkaStreams.State s = streams.state();
            boolean ok = s != KafkaStreams.State.ERROR && s != KafkaStreams.State.NOT_RUNNING;
            respond(ex, ok ? 200 : 503, "{\"status\":\"" + s + "\"}", "application/json");
        });
        http.createContext("/health/ready", ex -> {
            KafkaStreams.State s = streams.state();
            respond(ex, s == KafkaStreams.State.RUNNING ? 200 : 503, "{\"status\":\"" + s + "\"}", "application/json");
        });
        http.start();

        CountDownLatch done = new CountDownLatch(1);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            log.info("shutting down");
            streams.close(Duration.ofSeconds(30));
            http.stop(0);
            done.countDown();
        }, "shutdown"));
        log.info("starting rules-engine {} with grace {} ms; R-FOOT-001 {}; rules {} from {}; reload topic {}", instanceId,
                cfg.graceMs, footfall.map(f -> f.meta().mode()).orElse("not configured"), fileRules.sha256(), rulesFile,
                configTopic.orElse("off"));
        streams.start();
        done.await();
    }

    private static void respond(HttpExchange ex, int status, String body, String contentType) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", contentType);
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }
}
