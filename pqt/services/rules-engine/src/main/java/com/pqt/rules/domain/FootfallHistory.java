package com.pqt.rules.domain;

import java.util.ArrayList;
import java.util.TreeMap;

/** Per (store, run) state of R-FOOT-001: the trailing window counts and any open incident per zone. */
public final class FootfallHistory {
    public String storeId;
    public String simRunId;
    /** Start of the last window applied, so a replayed or restored window is never applied twice. */
    public long lastWindowStartMs = Long.MIN_VALUE;
    /** zone -> counts of the previous windows, oldest first, at most historyWindows long. */
    public TreeMap<String, ArrayList<Long>> counts = new TreeMap<>();
    /** zone -> onset (window start) of the open incident. */
    public TreeMap<String, Long> openOnsetMs = new TreeMap<>();
    /** zone -> id of the open incident. */
    public TreeMap<String, String> openIncidentId = new TreeMap<>();
    public long lastWallMs;

    public FootfallHistory() {
    }
}
