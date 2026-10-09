package com.pip.rules.app;

import com.fasterxml.jackson.databind.JsonNode;
import com.pip.rules.domain.FootfallConfig;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Milestone R4: when may a new rules document replace the running one?
 *
 * <ul>
 *   <li>Every rule whose definition (mode, severity, params) changed must carry a higher version.
 *       Incidents record the rule version, so an unbumped change would make two different
 *       behaviours indistinguishable afterwards.</li>
 *   <li>Structural settings cannot change while running: the global grace (it defines the
 *       watermark of every open state) and R-FOOT-001's presence, window, history and zones (the
 *       Kafka Streams window topology and the stored history depend on them). Those need a
 *       restart with the new file.</li>
 * </ul>
 */
public final class VersionCheck {
    private VersionCheck() {
    }

    /** MAJOR.MINOR.PATCH as three numbers, or null when it isn't one. */
    public static int[] parse(String v) {
        if (v == null) return null;
        String[] parts = v.split("\\.");
        if (parts.length != 3) return null;
        int[] out = new int[3];
        try {
            for (int i = 0; i < 3; i++) {
                out[i] = Integer.parseInt(parts[i]);
                if (out[i] < 0) return null;
            }
        } catch (NumberFormatException e) {
            return null;
        }
        return out;
    }

    public static int compare(String a, String b) {
        int[] x = parse(a);
        int[] y = parse(b);
        if (x == null || y == null) throw new IllegalArgumentException("not a version: " + (x == null ? a : b));
        for (int i = 0; i < 3; i++) {
            int c = Integer.compare(x[i], y[i]);
            if (c != 0) return c;
        }
        return 0;
    }

    /** Changes that need a restart rather than a hot reload. */
    public static List<String> structural(RuleSet running, RuleSet proposed) {
        List<String> out = new ArrayList<>();
        if (running.core().graceMs != proposed.core().graceMs) {
            out.add("grace changed from " + running.core().graceMs + " ms to " + proposed.core().graceMs
                    + " ms: needs a restart with the new file");
        }
        Optional<FootfallConfig> a = running.footfall();
        Optional<FootfallConfig> b = proposed.footfall();
        if (a.isPresent() != b.isPresent()) {
            out.add("R-FOOT-001 added or removed: needs a restart with the new file");
        } else if (a.isPresent()) {
            FootfallConfig x = a.get();
            FootfallConfig y = b.get();
            if (x.windowMs() != y.windowMs() || x.historyWindows() != y.historyWindows() || !x.zones().equals(y.zones())) {
                out.add("R-FOOT-001 window, history or zones changed: needs a restart with the new file");
            }
        }
        return out;
    }

    /** Every reason the proposed document may not replace the running one; empty means it may. */
    public static List<String> problems(RuleSet running, RuleSet proposed) {
        List<String> out = new ArrayList<>(structural(running, proposed));
        for (var e : proposed.definitions().entrySet()) {
            String id = e.getKey();
            JsonNode before = running.definitions().get(id);
            if (before == null) continue;   // a rule new to this document: nothing to compare with
            String vBefore = running.versions().get(id);
            String vAfter = proposed.versions().get(id);
            if (!before.equals(e.getValue()) && compare(vAfter, vBefore) <= 0) {
                out.add(id + " changed but its version " + vAfter + " is not higher than the running " + vBefore);
            }
        }
        return out;
    }
}
