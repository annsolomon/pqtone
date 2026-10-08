package com.pip.eventcore.ingest;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.List;
import java.util.Set;

/**
 * Canonical JSON (recursively sorted keys, no insignificant whitespace) and SHA-256.
 * The contract restricts numbers to integers, so this matches RFC 8785 for every valid event.
 */
public final class Canonical {
    /** Transport-level attributes that may legitimately differ between retries of the same event. */
    public static final Set<String> EXCLUDED_TOP_LEVEL = Set.of("traceparent", "tracestate");
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private Canonical() {
    }

    public static String json(JsonNode node) {
        StringBuilder sb = new StringBuilder();
        write(node, sb, Set.of());
        return sb.toString();
    }

    public static String eventHash(JsonNode event) {
        StringBuilder sb = new StringBuilder();
        write(event, sb, EXCLUDED_TOP_LEVEL);
        return sha256Hex(sb.toString());
    }

    public static String sha256Hex(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void write(JsonNode n, StringBuilder sb, Set<String> skip) {
        try {
            if (n.isObject()) {
                List<String> names = new ArrayList<>();
                Iterator<String> it = n.fieldNames();
                while (it.hasNext()) names.add(it.next());
                names.sort(null);
                sb.append('{');
                boolean first = true;
                for (String name : names) {
                    if (skip.contains(name)) continue;
                    if (!first) sb.append(',');
                    first = false;
                    sb.append(MAPPER.writeValueAsString(name)).append(':');
                    write(n.get(name), sb, Set.of());
                }
                sb.append('}');
            } else if (n.isArray()) {
                sb.append('[');
                for (int i = 0; i < n.size(); i++) {
                    if (i > 0) sb.append(',');
                    write(n.get(i), sb, Set.of());
                }
                sb.append(']');
            } else {
                sb.append(MAPPER.writeValueAsString(n));
            }
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }
}
