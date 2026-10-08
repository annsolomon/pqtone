package com.pip.rules.domain;

import java.util.Set;

/** Immutable, typed rule configuration. Built from config/rules.yaml by the loader. */
public final class RuleConfig {
    public record RuleMeta(String id, String version, String mode, String severity) {
        public boolean enabled() {
            return !"off".equals(mode);
        }
    }

    public final long graceMs;
    public final RuleMeta queue;
    public final int queueThreshold;
    public final long queueSustainMs;
    public final int queueHysteresis;
    public final long queueClearMs;
    public final RuleMeta dwell;
    public final Set<String> dwellZones;
    public final long dwellLimitMs;
    public final long dwellTtlMs;
    public final RuleMeta absence;
    public final long absenceWithinMs;
    public final String absenceExpectType;

    public RuleConfig(long graceMs,
                      RuleMeta queue, int queueThreshold, long queueSustainMs, int queueHysteresis, long queueClearMs,
                      RuleMeta dwell, Set<String> dwellZones, long dwellLimitMs, long dwellTtlMs,
                      RuleMeta absence, long absenceWithinMs, String absenceExpectType) {
        if (graceMs < 0) throw new IllegalArgumentException("grace must be >= 0");
        if (queueThreshold - queueHysteresis < 0) throw new IllegalArgumentException("hysteresis exceeds threshold");
        this.graceMs = graceMs;
        this.queue = queue;
        this.queueThreshold = queueThreshold;
        this.queueSustainMs = queueSustainMs;
        this.queueHysteresis = queueHysteresis;
        this.queueClearMs = queueClearMs;
        this.dwell = dwell;
        this.dwellZones = Set.copyOf(dwellZones);
        this.dwellLimitMs = dwellLimitMs;
        this.dwellTtlMs = dwellTtlMs;
        this.absence = absence;
        this.absenceWithinMs = absenceWithinMs;
        this.absenceExpectType = absenceExpectType;
    }
}
