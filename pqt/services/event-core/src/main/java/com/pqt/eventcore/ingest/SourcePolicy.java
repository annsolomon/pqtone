package com.pqt.eventcore.ingest;

import com.pqt.eventcore.config.PqtProperties;

import java.util.List;

/** Which producer may claim which CloudEvents source. Prevents one client impersonating another. */
public final class SourcePolicy {
    private final PqtProperties.Ingest cfg;

    public SourcePolicy(PqtProperties.Ingest cfg) {
        this.cfg = cfg;
    }

    public boolean clientMayPublish(String clientId, String source) {
        List<String> prefixes = clientId == null ? null : cfg.clientSources().get(clientId);
        return prefixes != null && prefixes.stream().anyMatch(source::startsWith);
    }

    public boolean rawTopicAllows(String source) {
        return cfg.rawTopicSources().stream().anyMatch(source::startsWith);
    }

    static final String EDGE_SOURCE_PREFIX = "urn:pqt:edge:";

    /**
     * An edge source names its store ({@code urn:pqt:edge:<store>:<device>}) and each client is bound to its sources,
     * so an edge event must be for the store its source names: one store's edge box cannot write another store's
     * events (X1, ADR-036; the store-scoped billing tables rely on it). Other families (simulator runs) are unaffected.
     */
    public static boolean storeMatchesSource(String source, String storeId) {
        if (source == null || !source.startsWith(EDGE_SOURCE_PREFIX)) return true;
        String rest = source.substring(EDGE_SOURCE_PREFIX.length());
        int colon = rest.indexOf(':');
        return (colon < 0 ? rest : rest.substring(0, colon)).equals(storeId);
    }
}
