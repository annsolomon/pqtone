package com.pqt.eventcore.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;

import java.time.Duration;
import java.util.List;
import java.util.Map;

@ConfigurationProperties(prefix = "pqt")
public record PqtProperties(Ingest ingest, Topics topics, Oidc oidc, Paths paths, Retention retention, Pipeline pipeline) {

    /**
     * @param maxAge       how old a real (non-simulated) event may be when it arrives
     * @param maxAgeByType per event-type prefix, an age that replaces {@code maxAge} (the longest matching prefix
     *                     wins). Billing exports arrive a day or more after the sale (X1, ADR-036); camera events
     *                     keep {@code maxAge}.
     */
    public record Ingest(Duration maxFutureSkew, Duration maxAge, int maxBatchSize, int maxEventBytes,
                         int maxBatchBytes, double rateLimitPerSecond, int rateLimitBurst, int rawConcurrency,
                         String simSourcePrefix, Map<String, List<String>> clientSources,
                         List<String> rawTopicSources, Map<String, Duration> maxAgeByType) {
        @ConstructorBinding
        public Ingest {
            maxAgeByType = maxAgeByType == null ? Map.of() : Map.copyOf(maxAgeByType);
            for (Map.Entry<String, Duration> e : maxAgeByType.entrySet()) {
                if (e.getKey().isBlank() || e.getValue() == null || e.getValue().isNegative() || e.getValue().isZero()) {
                    throw new IllegalArgumentException("pqt.ingest.max-age-by-type: invalid entry " + e.getKey());
                }
            }
        }

        /** Without per-type ages: every type gets {@code maxAge}. */
        public Ingest(Duration maxFutureSkew, Duration maxAge, int maxBatchSize, int maxEventBytes,
                      int maxBatchBytes, double rateLimitPerSecond, int rateLimitBurst, int rawConcurrency,
                      String simSourcePrefix, Map<String, List<String>> clientSources, List<String> rawTopicSources) {
            this(maxFutureSkew, maxAge, maxBatchSize, maxEventBytes, maxBatchBytes, rateLimitPerSecond, rateLimitBurst,
                    rawConcurrency, simSourcePrefix, clientSources, rawTopicSources, Map.of());
        }

        /** The maximum age for an event of this type: the longest matching prefix in maxAgeByType, else maxAge. */
        public Duration maxAgeFor(String type) {
            if (type == null) return maxAge;
            String best = null;
            for (String prefix : maxAgeByType.keySet()) {
                if (type.startsWith(prefix) && (best == null || prefix.length() > best.length())) best = prefix;
            }
            return best == null ? maxAge : maxAgeByType.get(best);
        }
    }

    public record Topics(String raw, String validated, String dlq, String incidents, String shadow, String heartbeat) {
    }

    public record Oidc(String publicIssuer, String endSessionUri, String postLogoutRedirectUri, String clientId) {
    }

    public record Paths(String schemas, String layouts) {
    }

    public record Retention(int eventDays, int outboxHours) {
    }

    public record Pipeline(Duration heartbeatStaleAfter) {
    }
}
