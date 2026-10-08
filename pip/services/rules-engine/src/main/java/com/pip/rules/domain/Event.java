package com.pip.rules.domain;

/**
 * Validated store event as seen by the rules. Public fields keep the state-store JSON
 * representation explicit and free of framework annotations.
 */
public final class Event {
    public static final String QUEUE_LENGTH = "com.pip.store.queue.length";
    public static final String ZONE_ENTERED = "com.pip.store.zone.entered";
    public static final String ZONE_EXITED = "com.pip.store.zone.exited";
    public static final String REGISTER_OPENED = "com.pip.store.register.opened";

    public String id;
    public String source;
    public String type;
    public long timeMs;
    public String sequence;
    public String storeId;
    public String simRunId;
    public String subject;
    public String queueId;
    public Integer length;
    public String zoneId;
    public String trackId;
    public String registerId;

    public Event() {
    }

    /** State is scoped per store and per simulation run so replays never collide. */
    public static String stateKey(Event e) {
        return e.storeId + "|" + (e.simRunId == null ? "live" : e.simRunId);
    }

    static int compareOrder(Event a, Event b) {
        int c = Long.compare(a.timeMs, b.timeMs);
        if (c != 0) return c;
        c = nz(a.sequence).compareTo(nz(b.sequence));
        if (c != 0) return c;
        return nz(a.id).compareTo(nz(b.id));
    }

    private static String nz(String s) {
        return s == null ? "" : s;
    }
}
