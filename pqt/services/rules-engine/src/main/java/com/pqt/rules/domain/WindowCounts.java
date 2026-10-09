package com.pqt.rules.domain;

import java.util.TreeMap;

/**
 * Aggregate of one store's tumbling window: per simulation run (or "live"), the number of
 * zone.entered events per counted zone. Every event of the store, ticks included, records its
 * run, so a run is present in every window it was active in even when nobody entered a zone.
 */
public final class WindowCounts {
    public TreeMap<String, TreeMap<String, Long>> runs = new TreeMap<>();

    public WindowCounts() {
    }

    /** Records the run's presence and, when zone is not null, one more entry into that zone. */
    public WindowCounts add(String run, String zone) {
        TreeMap<String, Long> zones = runs.computeIfAbsent(run, r -> new TreeMap<>());
        if (zone != null) zones.merge(zone, 1L, Long::sum);
        return this;
    }
}
