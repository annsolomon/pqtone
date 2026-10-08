package com.pip.eventcore.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;
import java.util.List;
import java.util.Map;

@ConfigurationProperties(prefix = "pip")
public record PipProperties(Ingest ingest, Topics topics, Oidc oidc, Paths paths, Retention retention, Pipeline pipeline) {

    public record Ingest(Duration maxFutureSkew, Duration maxAge, int maxBatchSize, int maxEventBytes,
                         int maxBatchBytes, double rateLimitPerSecond, int rateLimitBurst, int rawConcurrency,
                         String simSourcePrefix, Map<String, List<String>> clientSources,
                         List<String> rawTopicSources) {
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
