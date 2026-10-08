package com.pip.rules.domain;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Evaluates R-FOOT-001 on final (closed) windows, in order, for one (store, run). Pure and
 * framework-free; the Kafka Streams topology feeds it the output of suppress(untilWindowCloses).
 * The Python reference (sim/store_sim/reference.py FootfallRef) implements the same rules:
 *
 *  - baseline: the previous historyWindows windows of the run, 0 where a zone had no entry;
 *  - spike: a full history exists, count >= minCount and count * H > factor * sum(history);
 *  - a spike opens an incident (onset = window start, detected = window end) unless one is open
 *    for the zone; the first window that is not a spike resolves it at its end.
 */
public final class FootfallSpikeDetector {
    private final FootfallConfig cfg;

    public FootfallSpikeDetector(FootfallConfig cfg) {
        this.cfg = cfg;
    }

    public FootfallConfig config() {
        return cfg;
    }

    /** Applies one closed window. Windows at or before the last applied one are ignored. */
    public List<Incident> onWindow(FootfallHistory h, String storeId, String simRunId, long startMs, long endMs,
                                   Map<String, Long> zoneCounts, long wallMs) {
        List<Incident> out = new ArrayList<>();
        h.lastWallMs = wallMs;
        if (h.storeId == null) {
            h.storeId = storeId;
            h.simRunId = simRunId;
        }
        if (startMs <= h.lastWindowStartMs) return out;
        h.lastWindowStartMs = startMs;
        int n = cfg.historyWindows();
        for (String zone : cfg.zones()) {
            long count = zoneCounts == null ? 0L : zoneCounts.getOrDefault(zone, 0L);
            ArrayList<Long> past = h.counts.computeIfAbsent(zone, z -> new ArrayList<>());
            long sum = 0;
            for (long c : past) sum += c;
            boolean spike = past.size() == n && count >= cfg.minCount()
                    && (double) count * n > cfg.factor() * (double) sum;
            double baseline = past.isEmpty() ? 0.0 : (double) sum / past.size();
            if (spike && !h.openOnsetMs.containsKey(zone)) {
                Incident inc = incident(h, "OPENED", zone, startMs, endMs);
                inc.summary = "Footfall into " + zone + " was " + count + " in " + minutes(cfg.windowMs())
                        + ", more than " + trim(cfg.factor()) + "x the average of the previous "
                        + minutes(cfg.windowMs() * n) + " (" + String.format(java.util.Locale.ROOT, "%.1f", baseline) + ")";
                inc.attrs.put("zoneId", zone);
                inc.attrs.put("count", count);
                inc.attrs.put("baseline", Math.round(baseline * 100.0) / 100.0);
                inc.attrs.put("factor", cfg.factor());
                inc.attrs.put("windowStart", java.time.Instant.ofEpochMilli(startMs).toString());
                inc.attrs.put("windowEnd", java.time.Instant.ofEpochMilli(endMs).toString());
                h.openOnsetMs.put(zone, startMs);
                h.openIncidentId.put(zone, inc.incidentId);
                emit(inc, out);
            } else if (!spike && h.openOnsetMs.containsKey(zone)) {
                long onset = h.openOnsetMs.remove(zone);
                Incident inc = incident(h, "RESOLVED", zone, onset, endMs);
                inc.incidentId = h.openIncidentId.remove(zone);
                inc.summary = "Footfall into " + zone + " back to normal (" + count + " in " + minutes(cfg.windowMs()) + ")";
                emit(inc, out);
            }
            past.add(count);
            while (past.size() > n) past.remove(0);
        }
        return out;
    }

    private void emit(Incident inc, List<Incident> out) {
        if (cfg.meta().enabled()) out.add(inc);
    }

    private Incident incident(FootfallHistory h, String kind, String zone, long onsetMs, long detectedMs) {
        RuleConfig.RuleMeta meta = cfg.meta();
        Incident inc = new Incident();
        inc.ruleId = meta.id();
        inc.ruleVersion = meta.version();
        inc.mode = meta.mode();
        inc.severity = meta.severity();
        inc.kind = kind;
        inc.storeId = h.storeId;
        inc.simRunId = h.simRunId;
        inc.key = zone;
        inc.subject = "zone:" + zone;
        inc.onsetMs = onsetMs;
        inc.detectedMs = detectedMs;
        String major = meta.version() == null ? "0" : meta.version().split("\\.")[0];
        String name = meta.id() + "|" + major + "|" + h.storeId + "|" + h.simRunId + "|" + zone + "|" + onsetMs;
        inc.incidentId = UUID.nameUUIDFromBytes(name.getBytes(StandardCharsets.UTF_8)).toString();
        return inc;
    }

    private static String minutes(long ms) {
        return (ms / 60_000) + " min";
    }

    private static String trim(double d) {
        return d == Math.rint(d) ? String.valueOf((long) d) : String.valueOf(d);
    }
}
