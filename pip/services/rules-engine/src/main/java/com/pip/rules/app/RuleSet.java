package com.pip.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pip.rules.domain.FootfallConfig;
import com.pip.rules.domain.RuleConfig;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * One complete, validated rules document (milestone R4): the typed configuration the engine runs,
 * plus what is needed to compare two documents (each rule's version and its definition: mode,
 * severity, params) and to identify it (SHA-256 of the text).
 */
public record RuleSet(RuleConfig core, Optional<FootfallConfig> footfall, String sha256,
                      Map<String, String> versions, Map<String, JsonNode> definitions) {

    public RuleSet {
        versions = Map.copyOf(versions);
        definitions = Map.copyOf(definitions);
    }

    /** Parses and validates rules.yaml text (JSON works too). Throws IllegalArgumentException on anything odd. */
    public static RuleSet parse(String text) {
        JsonNode root;
        try {
            root = RulesConfigLoader.YAML.readTree(text);
        } catch (Exception e) {
            throw new IllegalArgumentException("rules document is not valid YAML: " + e.getMessage(), e);
        }
        if (root == null || !root.isObject()) throw new IllegalArgumentException("rules document must be a mapping");
        RuleConfig core = RulesConfigLoader.fromTree(root);
        Optional<FootfallConfig> footfall = RulesConfigLoader.footfallFromTree(root);
        Map<String, String> versions = new TreeMap<>();
        Map<String, JsonNode> definitions = new TreeMap<>();
        for (JsonNode r : root.path("rules")) {
            String id = r.path("id").asText();
            String version = r.path("version").asText("");
            if (VersionCheck.parse(version) == null) {
                throw new IllegalArgumentException("rule " + id + " needs a MAJOR.MINOR.PATCH version, got '" + version + "'");
            }
            versions.put(id, version);
            ObjectNode def = Json.MAPPER.createObjectNode();
            def.put("mode", r.path("mode").asText());
            def.put("severity", r.path("severity").asText("medium"));
            def.set("params", r.path("params"));
            definitions.put(id, def);
        }
        return new RuleSet(core, footfall, sha256(text), versions, definitions);
    }

    /** For tests and the offline path: a set built from typed configs, without a source text. */
    public static RuleSet of(RuleConfig core, Optional<FootfallConfig> footfall) {
        Map<String, String> versions = new LinkedHashMap<>();
        for (RuleConfig.RuleMeta m : new RuleConfig.RuleMeta[]{core.queue, core.dwell, core.absence}) {
            versions.put(m.id(), m.version());
        }
        footfall.ifPresent(f -> versions.put(f.meta().id(), f.meta().version()));
        return new RuleSet(core, footfall, "built-in", versions, Map.of());
    }

    static String sha256(String text) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
