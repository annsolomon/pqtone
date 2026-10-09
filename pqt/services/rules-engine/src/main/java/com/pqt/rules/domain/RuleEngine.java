package com.pqt.rules.domain;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Event-time rule evaluation for one (store, run) state.
 *
 * <p>Kafka Streams has no Flink-style watermarks, so the engine keeps its own: stream time is
 * the highest event time seen for this store; the watermark is stream time minus grace.
 * Arriving events go into a reorder buffer and are released in (time, sequence, id) order
 * once they fall at or below the watermark. Events at or below an already-reached watermark
 * are late: counted and dropped. Timers (sustain, clear, absence deadline, dwell limit) are
 * evaluated in event time, so replaying the same input always yields the same incidents.
 *
 * <p>This class has no framework dependencies; it is the unit under test and is shared by
 * the Kafka Streams processor and the offline runner.
 */
public final class RuleEngine {

    public static final class Outcome {
        public final List<Incident> incidents = new ArrayList<>();
        public boolean late;
    }

    private static final int Q_RESOLVE = 0;
    private static final int Q_OPEN = 1;
    private static final int A_OPEN = 2;
    private static final int D_OPEN = 3;
    private static final int D_EXPIRE = 4;

    private record Timer(long deadline, int kind, String key) implements Comparable<Timer> {
        @Override
        public int compareTo(Timer o) {
            int c = Long.compare(deadline, o.deadline);
            if (c != 0) return c;
            c = Integer.compare(kind, o.kind);
            if (c != 0) return c;
            return key.compareTo(o.key);
        }
    }

    private final RuleConfig cfg;

    public RuleEngine(RuleConfig cfg) {
        this.cfg = cfg;
    }

    public RuleConfig config() {
        return cfg;
    }

    /** Accept one event in arrival order; returns incidents released by the watermark advance. */
    public Outcome onEvent(StoreState s, Event e, long wallMs) {
        Outcome out = new Outcome();
        s.lastWallMs = wallMs;
        if (s.storeId == null) {
            s.storeId = e.storeId;
            s.simRunId = e.simRunId;
        }
        if (s.watermarkMs != Long.MIN_VALUE && e.timeMs <= s.watermarkMs) {
            s.lateDropped++;
            out.late = true;
            return out;
        }
        insertSorted(s.buffer, e);
        s.streamTimeMs = Math.max(s.streamTimeMs, e.timeMs);
        long newWatermark = s.streamTimeMs - cfg.graceMs;
        if (newWatermark > s.watermarkMs) {
            advance(s, newWatermark, out.incidents);
        }
        return out;
    }

    /** Force the watermark forward (used by the offline runner at end of input). */
    public List<Incident> flushTo(StoreState s, long watermarkMs) {
        List<Incident> out = new ArrayList<>();
        if (watermarkMs > s.watermarkMs) {
            advance(s, watermarkMs, out);
        }
        return out;
    }

    private void advance(StoreState s, long watermark, List<Incident> out) {
        Iterator<Event> it = s.buffer.iterator();
        List<Event> released = new ArrayList<>();
        while (it.hasNext()) {
            Event next = it.next();
            if (next.timeMs > watermark) break;
            released.add(next);
            it.remove();
        }
        for (Event ev : released) {
            fireUntil(s, ev.timeMs, false, out);
            apply(s, ev, out);
            s.processed++;
        }
        fireUntil(s, watermark, true, out);
        s.watermarkMs = watermark;
    }

    private static void insertSorted(List<Event> buffer, Event e) {
        int lo = 0;
        int hi = buffer.size();
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            int c = Event.compareOrder(buffer.get(mid), e);
            if (c == 0 && buffer.get(mid).id != null && buffer.get(mid).id.equals(e.id)) {
                return; // defensive: identical event already buffered
            }
            if (c <= 0) lo = mid + 1;
            else hi = mid;
        }
        buffer.add(lo, e);
    }

    // ------------------------------------------------------------------ timers

    private Timer earliestDue(StoreState s, long t, boolean inclusive) {
        Timer best = null;
        for (Map.Entry<String, StoreState.QueueState> en : s.queues.entrySet()) {
            StoreState.QueueState q = en.getValue();
            if (q.open && q.clearMs != null) best = min(best, new Timer(q.clearMs + cfg.queueClearMs, Q_RESOLVE, en.getKey()), t, inclusive);
            if (!q.open && q.breachMs != null) best = min(best, new Timer(q.breachMs + cfg.queueSustainMs, Q_OPEN, en.getKey()), t, inclusive);
        }
        if (s.absence.pendingMs != null) {
            best = min(best, new Timer(s.absence.pendingMs, A_OPEN, s.absence.triggerKey), t, inclusive);
        }
        for (Map.Entry<String, StoreState.DwellState> en : s.dwell.entrySet()) {
            StoreState.DwellState d = en.getValue();
            if (!d.open) best = min(best, new Timer(d.entryMs + cfg.dwellLimitMs, D_OPEN, en.getKey()), t, inclusive);
            best = min(best, new Timer(d.entryMs + cfg.dwellTtlMs, D_EXPIRE, en.getKey()), t, inclusive);
        }
        return best;
    }

    private static Timer min(Timer best, Timer candidate, long t, boolean inclusive) {
        boolean due = candidate.deadline < t || (inclusive && candidate.deadline <= t);
        if (!due) return best;
        return best == null || candidate.compareTo(best) < 0 ? candidate : best;
    }

    private void fireUntil(StoreState s, long t, boolean inclusive, List<Incident> out) {
        Timer timer;
        while ((timer = earliestDue(s, t, inclusive)) != null) {
            fire(s, timer, out);
        }
    }

    private void fire(StoreState s, Timer timer, List<Incident> out) {
        long d = timer.deadline;
        String key = timer.key;
        switch (timer.kind) {
            case Q_OPEN -> {
                StoreState.QueueState q = s.queues.get(key);
                q.open = true;
                q.clearMs = null;
                q.onsetMs = q.breachMs;
                Incident inc = incident(s, cfg.queue, "OPENED", key, "queue:" + key, q.onsetMs, d);
                inc.summary = "Queue " + key + " has been at or above " + cfg.queueThreshold + " for "
                        + seconds(cfg.queueSustainMs);
                inc.evidence.add(q.breachEventId);
                inc.attrs.put("threshold", cfg.queueThreshold);
                q.incidentId = inc.incidentId;
                emit(cfg.queue, inc, out);
                if (cfg.absence.enabled() && s.absence.pendingMs == null && !s.absence.open) {
                    s.absence.pendingMs = d + cfg.absenceWithinMs;
                    s.absence.triggerKey = key;
                }
            }
            case Q_RESOLVE -> {
                StoreState.QueueState q = s.queues.get(key);
                Incident inc = incident(s, cfg.queue, "RESOLVED", key, "queue:" + key, q.onsetMs, d);
                inc.incidentId = q.incidentId;
                inc.summary = "Queue " + key + " back below " + (cfg.queueThreshold - cfg.queueHysteresis + 1);
                emit(cfg.queue, inc, out);
                q.open = false;
                q.breachMs = null;
                q.breachEventId = null;
                q.clearMs = null;
                q.onsetMs = null;
                q.incidentId = null;
                if (key.equals(s.absence.triggerKey)) {
                    if (s.absence.open) {
                        Incident a = incident(s, cfg.absence, "RESOLVED", key, "queue:" + key, s.absence.onsetMs, d);
                        a.incidentId = s.absence.incidentId;
                        a.summary = "Queue " + key + " cleared";
                        emit(cfg.absence, a, out);
                    }
                    s.absence = new StoreState.AbsenceState();
                }
            }
            case A_OPEN -> {
                long onset = d - cfg.absenceWithinMs;
                s.absence.pendingMs = null;
                s.absence.open = true;
                s.absence.onsetMs = onset;
                Incident inc = incident(s, cfg.absence, "OPENED", key, "queue:" + key, onset, d);
                inc.summary = "No register opened within " + seconds(cfg.absenceWithinMs)
                        + " of the queue alert on " + key;
                inc.attrs.put("expected", cfg.absenceExpectType);
                s.absence.incidentId = inc.incidentId;
                emit(cfg.absence, inc, out);
            }
            case D_OPEN -> {
                StoreState.DwellState dw = s.dwell.get(key);
                dw.open = true;
                String zone = key.substring(key.indexOf('|') + 1);
                Incident inc = incident(s, cfg.dwell, "OPENED", key, "zone:" + zone, dw.entryMs, d);
                inc.summary = "A visit to " + zone + " has lasted more than " + seconds(cfg.dwellLimitMs);
                inc.evidence.add(dw.entryEventId);
                inc.attrs.put("zoneId", zone);
                dw.incidentId = inc.incidentId;
                emit(cfg.dwell, inc, out);
            }
            case D_EXPIRE -> {
                StoreState.DwellState dw = s.dwell.remove(key);
                if (dw.open) {
                    String zone = key.substring(key.indexOf('|') + 1);
                    Incident inc = incident(s, cfg.dwell, "RESOLVED", key, "zone:" + zone, dw.entryMs, d);
                    inc.incidentId = dw.incidentId;
                    inc.summary = "Visit tracking expired without an exit event";
                    emit(cfg.dwell, inc, out);
                }
            }
            default -> throw new IllegalStateException("unknown timer kind " + timer.kind);
        }
    }

    // ------------------------------------------------------------------ events

    private void apply(StoreState s, Event e, List<Incident> out) {
        String type = e.type;
        if (Event.QUEUE_LENGTH.equals(type) && cfg.queue.enabled() && e.queueId != null && e.length != null) {
            StoreState.QueueState q = s.queues.computeIfAbsent(e.queueId, k -> new StoreState.QueueState());
            int length = e.length;
            if (!q.open) {
                if (length >= cfg.queueThreshold) {
                    if (q.breachMs == null) {
                        q.breachMs = e.timeMs;
                        q.breachEventId = e.id;
                    }
                } else {
                    q.breachMs = null;
                    q.breachEventId = null;
                }
            } else if (length <= cfg.queueThreshold - cfg.queueHysteresis) {
                if (q.clearMs == null) q.clearMs = e.timeMs;
            } else {
                q.clearMs = null;
            }
        } else if (cfg.absenceExpectType.equals(type) && cfg.absence.enabled()) {
            if (s.absence.open) {
                String key = s.absence.triggerKey;
                Incident a = incident(s, cfg.absence, "RESOLVED", key, "queue:" + key, s.absence.onsetMs, e.timeMs);
                a.incidentId = s.absence.incidentId;
                a.summary = "Register opened";
                a.evidence.add(e.id);
                emit(cfg.absence, a, out);
            }
            s.absence = new StoreState.AbsenceState();
        } else if (Event.ZONE_ENTERED.equals(type) && cfg.dwell.enabled() && e.zoneId != null
                && e.trackId != null && cfg.dwellZones.contains(e.zoneId)) {
            String key = e.trackId + "|" + e.zoneId;
            StoreState.DwellState old = s.dwell.get(key);
            if (old != null && old.open) {
                Incident inc = incident(s, cfg.dwell, "RESOLVED", key, "zone:" + e.zoneId, old.entryMs, e.timeMs);
                inc.incidentId = old.incidentId;
                inc.summary = "Visit superseded by a new entry";
                emit(cfg.dwell, inc, out);
            }
            StoreState.DwellState d = new StoreState.DwellState();
            d.entryMs = e.timeMs;
            d.entryEventId = e.id;
            s.dwell.put(key, d);
        } else if (Event.ZONE_EXITED.equals(type) && cfg.dwell.enabled() && e.zoneId != null && e.trackId != null) {
            String key = e.trackId + "|" + e.zoneId;
            StoreState.DwellState d = s.dwell.remove(key);
            if (d != null && d.open) {
                Incident inc = incident(s, cfg.dwell, "RESOLVED", key, "zone:" + e.zoneId, d.entryMs, e.timeMs);
                inc.incidentId = d.incidentId;
                inc.summary = "Visit to " + e.zoneId + " ended";
                inc.evidence.add(e.id);
                emit(cfg.dwell, inc, out);
            }
        }
    }

    // ------------------------------------------------------------------ helpers

    private static void emit(RuleConfig.RuleMeta meta, Incident inc, List<Incident> out) {
        if (meta.enabled()) out.add(inc);
    }

    private static Incident incident(StoreState s, RuleConfig.RuleMeta meta, String kind, String key,
                                     String subject, long onsetMs, long detectedMs) {
        Incident inc = new Incident();
        inc.ruleId = meta.id();
        inc.ruleVersion = meta.version();
        inc.mode = meta.mode();
        inc.severity = meta.severity();
        inc.kind = kind;
        inc.storeId = s.storeId;
        inc.simRunId = s.simRunId;
        inc.key = key;
        inc.subject = subject;
        inc.onsetMs = onsetMs;
        inc.detectedMs = detectedMs;
        String major = meta.version() == null ? "0" : meta.version().split("\\.")[0];
        String name = meta.id() + "|" + major + "|" + s.storeId + "|" + s.simRunId + "|" + key + "|" + onsetMs;
        inc.incidentId = UUID.nameUUIDFromBytes(name.getBytes(StandardCharsets.UTF_8)).toString();
        return inc;
    }

    private static String seconds(long ms) {
        return (ms / 1000) + " s";
    }
}
