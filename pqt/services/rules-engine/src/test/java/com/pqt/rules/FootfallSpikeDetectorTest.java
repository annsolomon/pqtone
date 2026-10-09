package com.pqt.rules;

import com.pqt.rules.domain.FootfallConfig;
import com.pqt.rules.domain.FootfallHistory;
import com.pqt.rules.domain.FootfallSpikeDetector;
import com.pqt.rules.domain.Incident;
import com.pqt.rules.domain.RuleConfig;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Same cases as the Python reference tests (sim/tests/test_sim.py, R-FOOT-001). */
class FootfallSpikeDetectorTest {
    static final long W = 300_000;

    static FootfallConfig cfg(String mode) {
        return new FootfallConfig(new RuleConfig.RuleMeta("R-FOOT-001", "1.0.0", mode, "low"),
                List.of("entrance"), W, 12, 2.0, 10, 30_000);
    }

    static List<String> run(String mode, long... counts) {
        FootfallSpikeDetector d = new FootfallSpikeDetector(cfg(mode));
        FootfallHistory h = new FootfallHistory();
        List<String> out = new ArrayList<>();
        for (int i = 0; i < counts.length; i++) {
            for (Incident inc : d.onWindow(h, "store-001", "run-a", i * W, (i + 1) * W, Map.of("entrance", counts[i]), 0)) {
                out.add(inc.kind + "@" + (inc.onsetMs / W) + "-" + (inc.detectedMs / W));
            }
        }
        return out;
    }

    static long[] history(long each, long... then) {
        long[] out = new long[12 + then.length];
        java.util.Arrays.fill(out, 0, 12, each);
        System.arraycopy(then, 0, out, 12, then.length);
        return out;
    }

    @Test
    void opensAfterAFullHourAndResolvesOnTheNextQuietWindow() {
        assertEquals(List.of("OPENED@12-13", "RESOLVED@12-14"), run("enforce", history(5, 11, 5)));
    }

    @Test
    void needsAFullHourOfHistory() {
        long[] c = new long[12];
        java.util.Arrays.fill(c, 0, 11, 5);
        c[11] = 40;
        assertTrue(run("enforce", c).isEmpty());
    }

    @Test
    void strictlyMoreThanFactorTimesTheMeanAndAtLeastMinCount() {
        assertTrue(run("enforce", history(5, 10)).isEmpty());   // exactly 2x
        assertTrue(run("enforce", history(2, 9)).isEmpty());    // below minCount
        assertEquals(List.of("OPENED@12-13"), run("enforce", history(2, 10)));
    }

    @Test
    void consecutiveSpikeWindowsAreOneIncident() {
        assertEquals(List.of("OPENED@12-13", "RESOLVED@12-15"), run("enforce", history(5, 20, 30, 5)));
    }

    @Test
    void zonesWithoutEntriesCountAsZeroAndOffEmitsNothing() {
        FootfallSpikeDetector d = new FootfallSpikeDetector(cfg("enforce"));
        FootfallHistory h = new FootfallHistory();
        for (int i = 0; i < 12; i++) assertTrue(d.onWindow(h, "s", "r", i * W, (i + 1) * W, Map.of(), 0).isEmpty());
        assertEquals(12, h.counts.get("entrance").size());
        assertEquals(0L, h.counts.get("entrance").get(0));
        List<Incident> out = d.onWindow(h, "s", "r", 12 * W, 13 * W, Map.of("entrance", 10L), 0);
        assertEquals(1, out.size());
        assertEquals("zone:entrance", out.get(0).subject);
        assertEquals(10L, out.get(0).attrs.get("count"));
        assertTrue(run("off", history(5, 40)).isEmpty());
    }

    @Test
    void replayedWindowsAreIgnoredAndIdsAreStable() {
        FootfallSpikeDetector d = new FootfallSpikeDetector(cfg("shadow"));
        FootfallHistory h = new FootfallHistory();
        for (int i = 0; i < 12; i++) d.onWindow(h, "s", "r", i * W, (i + 1) * W, Map.of("entrance", 5L), 0);
        Incident first = d.onWindow(h, "s", "r", 12 * W, 13 * W, Map.of("entrance", 30L), 0).get(0);
        assertTrue(d.onWindow(h, "s", "r", 12 * W, 13 * W, Map.of("entrance", 30L), 0).isEmpty());
        assertEquals("shadow", first.mode);
        FootfallHistory h2 = new FootfallHistory();
        for (int i = 0; i < 12; i++) d.onWindow(h2, "s", "r", i * W, (i + 1) * W, Map.of("entrance", 5L), 0);
        assertEquals(first.incidentId, d.onWindow(h2, "s", "r", 12 * W, 13 * W, Map.of("entrance", 30L), 0).get(0).incidentId);
    }
}
