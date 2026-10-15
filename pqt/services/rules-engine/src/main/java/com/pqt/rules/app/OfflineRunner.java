package com.pqt.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.rules.domain.Event;
import com.pqt.rules.domain.FootfallConfig;
import com.pqt.rules.domain.Incident;
import com.pqt.rules.domain.RuleConfig;
import com.pqt.rules.domain.RuleEngine;
import com.pqt.rules.domain.StoreState;
import com.pqt.rules.kafka.CloudEventTimestampExtractor;
import com.pqt.rules.kafka.FootfallTopology;
import com.pqt.rules.kafka.RulesMetrics;
import com.pqt.rules.kafka.Topics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.TopologyConfig;
import org.apache.kafka.streams.TopologyTestDriver;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.state.BuiltInDslStoreSuppliers;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Properties;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/**
 * Runs the production rule engine over a recorded event file in emission order, mimicking
 * the event-core gate (drop invalid, dedup on (source, id)). Used by CI for fast scoring
 * without Kafka:  java -cp app.jar com.pqt.rules.app.OfflineRunner --rules R --matrix DIR
 *
 * R-FOOT-001 is a Kafka Streams window, so the offline run feeds the same deduplicated
 * events, in the same order, through the production footfall topology in a
 * TopologyTestDriver: windowing, grace, suppress and lateness are Kafka Streams' own code.
 */
public final class OfflineRunner {

    public record Stats(long events, long invalid, long duplicates, long late, long incidents) {
    }

    public static Stats run(Path events, Path out, RuleEngine engine) throws IOException {
        return run(events, out, engine, Optional.empty());
    }

    public static Stats run(Path events, Path out, RuleEngine engine, Optional<FootfallConfig> footfall) throws IOException {
        Set<String> seen = new HashSet<>();
        List<String[]> accepted = new ArrayList<>();
        Map<String, StoreState> states = new HashMap<>();
        List<Incident> incidents = new ArrayList<>();
        long n = 0, invalid = 0, dups = 0, late = 0;
        for (String line : Files.readAllLines(events, StandardCharsets.UTF_8)) {
            if (line.isBlank()) continue;
            n++;
            Event e;
            try {
                e = EventParser.parse(line);
            } catch (InvalidEventException ex) {
                invalid++;
                continue;
            }
            if (e == null) continue;  // not rule input
            if (!seen.add(e.source + "\u0000" + e.id)) {
                dups++;
                continue;
            }
            accepted.add(new String[]{e.storeId, line});
            StoreState s = states.computeIfAbsent(Event.stateKey(e), k -> new StoreState());
            RuleEngine.Outcome o = engine.onEvent(s, e, 0L);
            if (o.late) late++;
            incidents.addAll(o.incidents);
        }
        List<String> windowed = footfall.isPresent() ? footfall(accepted, footfall.get()) : List.of();
        try (BufferedWriter w = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
            for (Incident inc : incidents) {
                w.write(IncidentJson.toJson(inc));
                w.write('\n');
            }
            for (String json : windowed) {
                w.write(json);
                w.write('\n');
            }
        }
        return new Stats(n, invalid, dups, late, incidents.size() + windowed.size());
    }

    /**
     * The production footfall topology over the accepted events, in arrival order. The driver commits after
     * every record, and a RocksDB window store flushes on every commit, which made the offline gate spend
     * minutes per matrix in disk flushes (and the noisy matrices, five to seven times larger, most of an
     * hour). The DSL stores are therefore in memory here: the same window, grace and suppress code, without
     * the disk. Production keeps RocksDB.
     */
    static List<String> footfall(List<String[]> accepted, FootfallConfig cfg) throws IOException {
        Topics topics = new Topics("store.events.v1", "incidents.v1", "incidents.shadow.v1", "rules.heartbeat.v1");
        Path stateDir = Files.createTempDirectory("offline-footfall");
        Properties p = new Properties();
        p.put(StreamsConfig.APPLICATION_ID_CONFIG, "rules-engine-offline");
        p.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "offline:9092");
        p.put(StreamsConfig.STATE_DIR_CONFIG, stateDir.toString());
        p.put(StreamsConfig.DSL_STORE_SUPPLIERS_CLASS_CONFIG, BuiltInDslStoreSuppliers.InMemoryDslStoreSuppliers.class);
        StreamsBuilder builder = new StreamsBuilder(new TopologyConfig(new StreamsConfig(p)));
        FootfallTopology.addTo(builder, builder.stream(topics.validated(), Consumed.with(Serdes.String(), Serdes.String())
                .withTimestampExtractor(new CloudEventTimestampExtractor())), cfg, topics,
                new RulesMetrics(new SimpleMeterRegistry()), Duration.ofDays(365));
        List<String> out = new ArrayList<>();
        try (TopologyTestDriver driver = new TopologyTestDriver(builder.build(), p, Instant.EPOCH)) {
            TestInputTopic<String, String> in =
                    driver.createInputTopic(topics.validated(), new StringSerializer(), new StringSerializer());
            for (String[] rec : accepted) in.pipeInput(rec[0], rec[1]);
            for (String topic : List.of(topics.incidents(), topics.shadow())) {
                TestOutputTopic<String, String> o =
                        driver.createOutputTopic(topic, new StringDeserializer(), new StringDeserializer());
                out.addAll(o.readValuesToList());
            }
        } finally {
            deleteRecursively(stateDir);
        }
        return out;
    }

    private static void deleteRecursively(Path dir) throws IOException {
        if (!Files.exists(dir)) return;
        try (var paths = Files.walk(dir)) {
            for (Path x : paths.sorted(java.util.Comparator.reverseOrder()).toList()) Files.deleteIfExists(x);
        }
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> a = new HashMap<>();
        for (int i = 0; i + 1 < args.length; i += 2) a.put(args[i], args[i + 1]);
        Path rules = Path.of(a.getOrDefault("--rules", "config/rules.yaml"));
        RuleConfig cfg = RulesConfigLoader.load(rules);
        Optional<FootfallConfig> footfall = RulesConfigLoader.loadFootfall(rules);
        RuleEngine engine = new RuleEngine(cfg);
        if (a.containsKey("--matrix")) {
            Path dir = Path.of(a.get("--matrix"));
            JsonNode cases = Json.MAPPER.readTree(Files.readAllBytes(dir.resolve("cases.json")));
            // Cases are independent (own state, own footfall driver, own output files) and the engine holds
            // only its configuration, so they run in parallel; results are reported in case order.
            int threads = Integer.parseInt(a.getOrDefault("--threads",
                    String.valueOf(Math.max(1, Runtime.getRuntime().availableProcessors()))));
            ExecutorService pool = Executors.newFixedThreadPool(threads);
            try {
                List<Future<String>> done = new ArrayList<>();
                for (JsonNode c : cases) {
                    Path caseDir = Path.of(c.path("dir").asText());
                    done.add(pool.submit(() -> writeStats(caseDir,
                            run(caseDir.resolve("events.jsonl"), caseDir.resolve("incidents.jsonl"), engine, footfall))));
                }
                for (Future<String> f : done) {
                    try {
                        System.out.println(f.get());
                    } catch (ExecutionException ex) {
                        throw ex.getCause() instanceof Exception e ? e : new IllegalStateException(ex.getCause());
                    }
                }
            } finally {
                pool.shutdownNow();
            }
            System.out.println("{\"cases\":" + cases.size() + ",\"threads\":" + threads + "}");
        } else {
            Path events = Path.of(required(a, "--events"));
            Path out = Path.of(required(a, "--out"));
            Stats s = run(events, out, engine, footfall);
            System.out.println(writeStats(out.getParent() == null ? Path.of(".") : out.getParent(), s));
        }
    }

    private static String writeStats(Path dir, Stats s) throws IOException {
        ObjectNode n = Json.MAPPER.createObjectNode();
        n.put("events", s.events()).put("invalid", s.invalid()).put("duplicates", s.duplicates())
                .put("lateDropped", s.late()).put("incidents", s.incidents());
        Files.writeString(dir.resolve("offline_stats.json"), n.toString() + "\n");
        return dir + " " + n;
    }

    private static String required(Map<String, String> a, String key) {
        String v = a.get(key);
        if (v == null) throw new IllegalArgumentException("missing " + key);
        return v;
    }
}
