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
 * <p>R-DWELL-001 1.1.0 (Tier 2 N4) stitches visits across tracking breaks: when a track is lost inside a
 * dwell zone (zone.exited with how=lost) its visit stays open for {@code stitch}; a track that appears in
 * the same zone within that time (zone.entered with how=appeared) continues the visit under the original
 * key, the most recently lost visit first. A visit nobody continues ends at the deadline. Real entries
 * and exits (crossed, or no `how`) behave exactly as in 1.0.0. Two details keep hand-overs independent of
 * delivery: within one instant losses are applied before other tracks' entries ({@link #lostBeforeAppeared}),
 * and a dwell-zone exit that arrives behind the watermark, though still counted late, ends or hands over a
 * visit that has not been reported instead of leaving it open until its TTL ({@code lateExit}).
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
    private static final int D_UNSTITCHED = 5;

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
            if (cfg.dwellStitchMs > 0) lateExit(s, e);
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
        if (cfg.dwellStitchMs > 0) released = lostBeforeAppeared(released);
        for (Event ev : released) {
            fireUntil(s, ev.timeMs, false, out);
            apply(s, ev, out);
            s.processed++;
        }
        fireUntil(s, watermark, true, out);
        s.watermarkMs = watermark;
    }

    /**
     * N4: identity switches report the old track lost and the new one appeared at the same instant, in
     * whichever order the producer sent them. Within one instant the engine applies, so that hand-overs never
     * depend on that order: first the losses of tracks that do not enter at this instant, then the rest in
     * (sequence, id) order, each entry followed at once by its own track's exits at this instant (a piece
     * seen for an instant takes the visit and passes it on). Groups without a loss keep their order, so
     * replays stay deterministic.
     */
    static List<Event> lostBeforeAppeared(List<Event> released) {
        List<Event> out = null;
        int i = 0;
        while (i < released.size()) {
            long t = released.get(i).timeMs;
            int j = i + 1;
            while (j < released.size() && released.get(j).timeMs == t) j++;
            List<Event> group = released.subList(i, j);
            List<Event> ordered = j - i > 1 ? handOverOrder(group) : null;
            if (ordered != null && out == null) out = new ArrayList<>(released.subList(0, i));
            if (out != null) out.addAll(ordered != null ? ordered : group);
            i = j;
        }
        return out == null ? released : out;
    }

    /** The order above for one instant, or null when it is the same as the input order. */
    private static List<Event> handOverOrder(List<Event> group) {
        Map<String, List<Event>> exitsOf = new java.util.HashMap<>();
        java.util.Set<String> entering = new java.util.HashSet<>();
        boolean anyLost = false;
        for (Event e : group) {
            if (e.trackId == null) continue;
            String k = e.trackId + "|" + e.zoneId;
            if (Event.ZONE_ENTERED.equals(e.type)) entering.add(k);
            if (Event.ZONE_EXITED.equals(e.type)) {
                exitsOf.computeIfAbsent(k, x -> new ArrayList<>()).add(e);
                anyLost |= Event.HOW_LOST.equals(e.how);
            }
        }
        if (!anyLost) return null;
        List<Event> out = new ArrayList<>(group.size());
        java.util.Set<Event> placed = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        for (Event e : group) {
            if (Event.ZONE_EXITED.equals(e.type) && Event.HOW_LOST.equals(e.how) && e.trackId != null
                    && !entering.contains(e.trackId + "|" + e.zoneId)) {
                out.add(e);
                placed.add(e);
            }
        }
        for (Event e : group) {
            if (!placed.add(e)) continue;
            out.add(e);
            if (Event.ZONE_ENTERED.equals(e.type) && e.trackId != null) {
                for (Event x : exitsOf.getOrDefault(e.trackId + "|" + e.zoneId, List.of())) {
                    if (placed.add(x)) out.add(x);
                }
            }
        }
        return out.equals(group) ? null : out;
    }

    /**
     * N4: a dwell-zone exit that arrives behind the watermark. It is still counted as late, and it only touches
     * visits that have not been reported (so nothing is ever dated before its report): it keeps them from
     * staying open until their TTL and makes the continuation the loss would have made on time.
     *
     * <p>A late real exit ends the visit. A late loss is applied as if on time and then re-decides the one
     * continuation in its stitch window that it would have won: a visit begun since by a track that appeared
     * (and continued nobody) is joined to the lost visit; a visit continued since from a loss older than this
     * one gives that continuation up to the lost visit (most recently lost first) and goes back to waiting.
     * The earliest such continuation is taken. Late entries stay dropped: their track may already be gone.
     */
    private void lateExit(StoreState s, Event e) {
        if (!Event.ZONE_EXITED.equals(e.type) || !cfg.dwell.enabled() || e.zoneId == null || e.trackId == null
                || !cfg.dwellZones.contains(e.zoneId)) {
            return;
        }
        String raw = e.trackId + "|" + e.zoneId;
        String key = s.dwellAlias.getOrDefault(raw, raw);
        StoreState.DwellState held = s.dwell.get(key);
        if (held == null || held.open || held.lostMs != null || e.timeMs < held.entryMs) return;
        if (!Event.HOW_LOST.equals(e.how)) {
            s.dwell.remove(key);
            removeAliases(s, key);
            return;
        }
        long t = e.timeMs;
        held.lostMs = t;
        s.dwellAlias.remove(raw);
        String suffix = "|" + e.zoneId;
        String best = null;
        boolean orphan = false;
        long bestAt = Long.MAX_VALUE;
        for (Map.Entry<String, StoreState.DwellState> en : s.dwell.entrySet()) {
            StoreState.DwellState v = en.getValue();
            if (v.open || en.getKey().equals(key) || !en.getKey().endsWith(suffix)) continue;
            Long at = null;
            boolean isOrphan = false;
            if (v.appeared && v.entryMs >= t && v.entryMs - t <= cfg.dwellStitchMs) {
                at = v.entryMs;
                isOrphan = true;
            } else if (v.resumedMs != null && v.prevLostMs != null && v.prevLostMs < t
                    && v.resumedMs >= t && v.resumedMs - t <= cfg.dwellStitchMs) {
                at = v.resumedMs;
            }
            if (at != null && at < bestAt) {
                best = en.getKey();
                bestAt = at;
                orphan = isOrphan;
            }
        }
        if (best == null) return;   // the stitch deadline ends it at the next advance, as an on-time loss would
        StoreState.DwellState v = s.dwell.get(best);
        held.prevLostMs = t;
        held.lostMs = v.lostMs;
        held.resumedMs = orphan ? (v.resumedMs != null ? v.resumedMs : v.entryMs) : v.resumedMs;
        boolean carried = s.dwellAlias.containsValue(best);
        String from = best;
        s.dwellAlias.replaceAll((k, x) -> x.equals(from) ? key : x);
        if (orphan) {
            s.dwell.remove(best);
            if (v.lostMs == null && !carried) s.dwellAlias.put(best, key);  // its own track still carries the visit
        } else {
            v.lostMs = v.prevLostMs;  // back to waiting since its own loss; its deadline may already have passed
            v.prevLostMs = null;
            v.resumedMs = null;
        }
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
            if (d.lostMs != null) {
                // Lost, waiting for a track to continue the visit: the limit waits too (N4).
                best = min(best, new Timer(d.lostMs + cfg.dwellStitchMs, D_UNSTITCHED, en.getKey()), t, inclusive);
            } else if (!d.open) {
                best = min(best, new Timer(d.entryMs + cfg.dwellLimitMs, D_OPEN, en.getKey()), t, inclusive);
            }
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
                // A limit that passed while the visit was waiting to be continued is reported when it was.
                long at = dw.resumedMs == null ? d : Math.max(d, dw.resumedMs);
                Incident inc = incident(s, cfg.dwell, "OPENED", key, "zone:" + zone, dw.entryMs, at);
                inc.summary = "A visit to " + zone + " has lasted more than " + seconds(cfg.dwellLimitMs);
                inc.evidence.add(dw.entryEventId);
                inc.attrs.put("zoneId", zone);
                dw.incidentId = inc.incidentId;
                emit(cfg.dwell, inc, out);
            }
            case D_UNSTITCHED -> {
                StoreState.DwellState dw = s.dwell.remove(key);
                removeAliases(s, key);
                if (dw.open) {
                    String zone = key.substring(key.indexOf('|') + 1);
                    Incident inc = incident(s, cfg.dwell, "RESOLVED", key, "zone:" + zone, dw.entryMs, d);
                    inc.incidentId = dw.incidentId;
                    inc.summary = "Visit to " + zone + " ended: the person was lost and not found again";
                    emit(cfg.dwell, inc, out);
                }
            }
            case D_EXPIRE -> {
                StoreState.DwellState dw = s.dwell.remove(key);
                removeAliases(s, key);
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
            if (cfg.dwellStitchMs > 0 && Event.HOW_APPEARED.equals(e.how) && continueVisit(s, e)) {
                return;
            }
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
            d.appeared = Event.HOW_APPEARED.equals(e.how);
            s.dwell.put(key, d);
        } else if (Event.ZONE_EXITED.equals(type) && cfg.dwell.enabled() && e.zoneId != null && e.trackId != null) {
            String raw = e.trackId + "|" + e.zoneId;
            String key = s.dwellAlias.getOrDefault(raw, raw);
            StoreState.DwellState held = s.dwell.get(key);
            if (held != null && held.lostMs == null && cfg.dwellStitchMs > 0 && Event.HOW_LOST.equals(e.how)) {
                held.lostMs = e.timeMs;  // keep the visit for a track that continues it (N4)
                s.dwellAlias.remove(raw);
                return;
            }
            StoreState.DwellState d = s.dwell.remove(key);
            removeAliases(s, key);
            if (d != null && d.open) {
                Incident inc = incident(s, cfg.dwell, "RESOLVED", key, "zone:" + e.zoneId, d.entryMs, e.timeMs);
                inc.incidentId = d.incidentId;
                inc.summary = "Visit to " + e.zoneId + " ended";
                inc.evidence.add(e.id);
                emit(cfg.dwell, inc, out);
            }
        }
    }

    /** N4: an appearing track continues the most recently lost visit in the same zone, if one waits. */
    private boolean continueVisit(StoreState s, Event e) {
        String suffix = "|" + e.zoneId;
        String best = null;
        long bestLost = Long.MIN_VALUE;
        for (Map.Entry<String, StoreState.DwellState> en : s.dwell.entrySet()) {
            StoreState.DwellState d = en.getValue();
            if (d.lostMs == null || !en.getKey().endsWith(suffix)) continue;
            long gap = e.timeMs - d.lostMs;
            if (gap < 0 || gap > cfg.dwellStitchMs) continue;
            if (best == null || d.lostMs > bestLost) {  // ties: the first key in order
                best = en.getKey();
                bestLost = d.lostMs;
            }
        }
        if (best == null) return false;
        StoreState.DwellState d = s.dwell.get(best);
        d.prevLostMs = d.lostMs;
        d.lostMs = null;
        d.resumedMs = e.timeMs;
        s.dwellAlias.put(e.trackId + suffix, best);
        return true;
    }

    private static void removeAliases(StoreState s, String key) {
        s.dwellAlias.values().removeIf(key::equals);
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
