package com.pip.eventcore.ingest;

import com.pip.eventcore.config.PipProperties;

import java.util.List;

/** Which producer may claim which CloudEvents source. Prevents one client impersonating another. */
public final class SourcePolicy {
    private final PipProperties.Ingest cfg;

    public SourcePolicy(PipProperties.Ingest cfg) {
        this.cfg = cfg;
    }

    public boolean clientMayPublish(String clientId, String source) {
        List<String> prefixes = clientId == null ? null : cfg.clientSources().get(clientId);
        return prefixes != null && prefixes.stream().anyMatch(source::startsWith);
    }

    public boolean rawTopicAllows(String source) {
        return cfg.rawTopicSources().stream().anyMatch(source::startsWith);
    }
}
