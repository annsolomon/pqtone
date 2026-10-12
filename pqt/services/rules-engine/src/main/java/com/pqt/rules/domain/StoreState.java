package com.pqt.rules.domain;

import java.util.ArrayList;
import java.util.TreeMap;

/** Complete per-(store, run) rule state, persisted in the Kafka Streams state store. */
public final class StoreState {
    public String storeId;
    public String simRunId;
    public long streamTimeMs = Long.MIN_VALUE;
    public long watermarkMs = Long.MIN_VALUE;
    public ArrayList<Event> buffer = new ArrayList<>();
    public TreeMap<String, QueueState> queues = new TreeMap<>();
    public TreeMap<String, DwellState> dwell = new TreeMap<>();
    /** N4: "trackId|zone" of a track that continued a stitched visit -> the visit's key. */
    public TreeMap<String, String> dwellAlias = new TreeMap<>();
    public AbsenceState absence = new AbsenceState();
    public long lateDropped;
    public long processed;
    public long lastWallMs;

    public StoreState() {
    }

    public static final class QueueState {
        public Long breachMs;
        public String breachEventId;
        public boolean open;
        public Long clearMs;
        public Long onsetMs;
        public String incidentId;

        public QueueState() {
        }
    }

    public static final class DwellState {
        public long entryMs;
        public String entryEventId;
        public boolean open;
        public String incidentId;
        /** N4: the visit's track was lost here at this time; waiting `stitch` for a track to continue it. */
        public Long lostMs;
        /** N4: when the visit was last continued by another track (its limit cannot be reported earlier). */
        public Long resumedMs;

        public DwellState() {
        }
    }

    public static final class AbsenceState {
        public Long pendingMs;
        public String triggerKey;
        public boolean open;
        public Long onsetMs;
        public String incidentId;

        public AbsenceState() {
        }
    }
}
