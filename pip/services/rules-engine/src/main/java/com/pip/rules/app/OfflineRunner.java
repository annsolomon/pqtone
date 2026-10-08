package com.pip.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pip.rules.domain.Event;
import com.pip.rules.domain.Incident;
import com.pip.rules.domain.RuleConfig;
import com.pip.rules.domain.RuleEngine;
import com.pip.rules.domain.StoreState;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Runs the production rule engine over a recorded event file in emission order, mimicking
 * the event-core gate (drop invalid, dedup on (source, id)). Used by CI for fast scoring
 * without Kafka:  java -cp app.jar com.pip.rules.app.OfflineRunner --rules R --matrix DIR
 */
public final class OfflineRunner {

    public record Stats(long events, long invalid, long duplicates, long late, long incidents) {
    }

    public static Stats run(Path events, Path out, RuleEngine engine) throws IOException {
        Set<String> seen = new HashSet<>();
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
            if (!seen.add(e.source + "\u0000" + e.id)) {
                dups++;
                continue;
            }
            StoreState s = states.computeIfAbsent(Event.stateKey(e), k -> new StoreState());
            RuleEngine.Outcome o = engine.onEvent(s, e, 0L);
            if (o.late) late++;
            incidents.addAll(o.incidents);
        }
        try (BufferedWriter w = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
            for (Incident inc : incidents) {
                w.write(IncidentJson.toJson(inc));
                w.write('\n');
            }
        }
        return new Stats(n, invalid, dups, late, incidents.size());
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> a = new HashMap<>();
        for (int i = 0; i + 1 < args.length; i += 2) a.put(args[i], args[i + 1]);
        Path rules = Path.of(a.getOrDefault("--rules", "config/rules.yaml"));
        RuleConfig cfg = RulesConfigLoader.load(rules);
        RuleEngine engine = new RuleEngine(cfg);
        if (a.containsKey("--matrix")) {
            Path dir = Path.of(a.get("--matrix"));
            JsonNode cases = Json.MAPPER.readTree(Files.readAllBytes(dir.resolve("cases.json")));
            for (JsonNode c : cases) {
                Path caseDir = Path.of(c.path("dir").asText());
                writeStats(caseDir, run(caseDir.resolve("events.jsonl"), caseDir.resolve("incidents.jsonl"), engine));
            }
            System.out.println("{\"cases\":" + cases.size() + "}");
        } else {
            Path events = Path.of(required(a, "--events"));
            Path out = Path.of(required(a, "--out"));
            Stats s = run(events, out, engine);
            writeStats(out.getParent() == null ? Path.of(".") : out.getParent(), s);
        }
    }

    private static void writeStats(Path dir, Stats s) throws IOException {
        ObjectNode n = Json.MAPPER.createObjectNode();
        n.put("events", s.events()).put("invalid", s.invalid()).put("duplicates", s.duplicates())
                .put("lateDropped", s.late()).put("incidents", s.incidents());
        Files.writeString(dir.resolve("offline_stats.json"), n.toString() + "\n");
        System.out.println(dir + " " + n);
    }

    private static String required(Map<String, String> a, String key) {
        String v = a.get(key);
        if (v == null) throw new IllegalArgumentException("missing " + key);
        return v;
    }
}
