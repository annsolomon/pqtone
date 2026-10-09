package com.pqt.rules.domain;

import java.util.List;

/**
 * R-FOOT-001: footfall spike, the first windowed rule. Counts zone.entered per zone in tumbling
 * windows and flags a window whose count exceeds factor x the mean of the previous windows.
 *
 * @param meta           rule id, version, mode and severity
 * @param zones          zones whose entries are counted, in a fixed order
 * @param windowMs       tumbling window size (aligned to the epoch)
 * @param historyWindows number of previous windows the baseline is averaged over
 * @param factor         a spike needs count x historyWindows > factor x sum(history)...
 * @param minCount       ...and count >= minCount
 * @param graceMs        how long after its end a window still accepts out-of-order records
 */
public record FootfallConfig(RuleConfig.RuleMeta meta, List<String> zones, long windowMs, int historyWindows,
                             double factor, int minCount, long graceMs) {
    public FootfallConfig {
        zones = List.copyOf(zones);
        if (zones.isEmpty()) throw new IllegalArgumentException("R-FOOT-001 needs at least one zone");
        if (windowMs <= 0) throw new IllegalArgumentException("R-FOOT-001 window must be positive");
        if (historyWindows < 1) throw new IllegalArgumentException("R-FOOT-001 history must cover at least one window");
        if (!(factor > 0)) throw new IllegalArgumentException("R-FOOT-001 factor must be positive");
        if (minCount < 0) throw new IllegalArgumentException("R-FOOT-001 minCount must be >= 0");
        if (graceMs < 0) throw new IllegalArgumentException("grace must be >= 0");
    }
}
