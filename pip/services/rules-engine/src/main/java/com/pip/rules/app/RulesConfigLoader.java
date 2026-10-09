package com.pip.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.pip.rules.domain.FootfallConfig;
import com.pip.rules.domain.RuleConfig;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashMap;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Loads config/rules.yaml into a typed, validated RuleConfig. Fails fast on anything odd. */
public final class RulesConfigLoader {
    private static final Set<String> MODES = Set.of("enforce", "shadow", "off");

    private RulesConfigLoader() {
    }

    public static RuleConfig load(Path path) throws IOException {
        JsonNode root = new ObjectMapper(new YAMLFactory()).readTree(Files.readAllBytes(path));
        Map<String, JsonNode> rules = new HashMap<>();
        for (JsonNode r : root.path("rules")) {
            String id = r.path("id").asText();
            if (rules.put(id, r) != null) throw new IllegalArgumentException("duplicate rule " + id);
            String mode = r.path("mode").asText();
            if (!MODES.contains(mode)) throw new IllegalArgumentException("rule " + id + " has invalid mode " + mode);
        }
        JsonNode q = require(rules, "R-QUEUE-001");
        JsonNode d = require(rules, "R-DWELL-001");
        JsonNode a = require(rules, "R-ABS-001");
        JsonNode qp = q.path("params");
        JsonNode dp = d.path("params");
        JsonNode ap = a.path("params");
        Set<String> zones = new HashSet<>();
        dp.path("zones").forEach(z -> zones.add(z.asText()));
        if (!"R-QUEUE-001".equals(ap.path("trigger").asText())) {
            throw new IllegalArgumentException("R-ABS-001 trigger must be R-QUEUE-001 in Tier 1");
        }
        return new RuleConfig(
                dur(root.path("grace")),
                meta(q), positiveInt(qp, "threshold"), dur(qp.path("sustain")), positiveInt(qp, "hysteresis"),
                dur(qp.path("clearSustain")),
                meta(d), zones, dur(dp.path("limit")), dur(dp.path("sessionTtl")),
                meta(a), dur(ap.path("within")), ap.path("expect").asText());
    }

    /**
     * R-FOOT-001, the windowed footfall rule, if configured. Its window grace is the global
     * grace, so out-of-order handling is the same as for the other rules.
     */
    public static Optional<FootfallConfig> loadFootfall(Path path) throws IOException {
        JsonNode root = new ObjectMapper(new YAMLFactory()).readTree(Files.readAllBytes(path));
        for (JsonNode r : root.path("rules")) {
            if (!"R-FOOT-001".equals(r.path("id").asText())) continue;
            String mode = r.path("mode").asText();
            if (!MODES.contains(mode)) throw new IllegalArgumentException("rule R-FOOT-001 has invalid mode " + mode);
            JsonNode p = r.path("params");
            List<String> zones = new ArrayList<>();
            p.path("zones").forEach(z -> zones.add(z.asText()));
            long window = dur(p.path("window"));
            long history = dur(p.path("history"));
            if (window <= 0 || history % window != 0) {
                throw new IllegalArgumentException("R-FOOT-001: history must be a whole number of windows");
            }
            JsonNode factor = p.get("factor");
            if (factor == null || !factor.isNumber()) throw new IllegalArgumentException("bad factor");
            return Optional.of(new FootfallConfig(meta(r), zones, window, (int) (history / window),
                    factor.asDouble(), positiveInt(p, "minCount"), dur(root.path("grace"))));
        }
        return Optional.empty();
    }

    private static JsonNode require(Map<String, JsonNode> rules, String id) {
        JsonNode n = rules.get(id);
        if (n == null) throw new IllegalArgumentException("missing rule " + id);
        return n;
    }

    private static RuleConfig.RuleMeta meta(JsonNode r) {
        return new RuleConfig.RuleMeta(r.path("id").asText(), r.path("version").asText(),
                r.path("mode").asText(), r.path("severity").asText("medium"));
    }

    private static long dur(JsonNode n) {
        if (!n.isTextual()) throw new IllegalArgumentException("duration expected, got " + n);
        return Duration.parse(n.asText()).toMillis();
    }

    private static int positiveInt(JsonNode n, String field) {
        JsonNode v = n.get(field);
        if (v == null || !v.canConvertToInt() || v.asInt() < 0) throw new IllegalArgumentException("bad " + field);
        return v.asInt();
    }
}
