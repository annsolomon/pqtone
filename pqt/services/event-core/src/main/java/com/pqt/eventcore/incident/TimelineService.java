package com.pqt.eventcore.incident;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Milestone C4: the evidence around one incident, read from the append-only event table.
 *
 * Queue incidents get the queue's length and open registers over time plus register opened/closed
 * markers; zone incidents get zone entries per minute. Only aggregates and queue counters leave the
 * database: no track ids, so the timeline can't be used to follow a person.
 *
 * Every query is parameterised and bounded by store, run, event-time window and a row limit.
 */
@Service
public class TimelineService {
    static final String QUEUE_LENGTH = "com.pqt.store.queue.length";
    static final String REGISTER_OPENED = "com.pqt.store.register.opened";
    static final String REGISTER_CLOSED = "com.pqt.store.register.closed";
    static final String ZONE_ENTERED = "com.pqt.store.zone.entered";

    private final JdbcTemplate jdbc;

    public TimelineService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> timeline(IncidentView incident) {
        Timeline.Window w = Timeline.window(incident.onsetAt(), incident.detectedAt(), incident.resolvedAt());
        Optional<Timeline.Target> target = Timeline.target(incident.subject());

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("incidentId", incident.incidentId());
        out.put("from", w.from().toString());
        out.put("to", w.to().toString());
        out.put("onsetAt", incident.onsetAt().toString());
        out.put("detectedAt", incident.detectedAt().toString());
        out.put("resolvedAt", incident.resolvedAt() == null ? null : incident.resolvedAt().toString());
        out.put("threshold", threshold(incident));

        List<Map<String, Object>> series = new ArrayList<>();
        List<Map<String, Object>> markers = new ArrayList<>();
        boolean truncated = false;
        if (target.isEmpty()) {
            out.put("kind", "none");
            out.put("target", null);
        } else {
            Timeline.Target t = target.get();
            out.put("kind", t.kind().json());
            out.put("target", t.id());
            if (t.kind() == Timeline.Kind.QUEUE) {
                truncated = queueSeries(incident, t.id(), w, series);
                markers.addAll(registerMarkers(incident, w));
            } else {
                truncated = zoneEntries(incident, t.id(), w, series);
            }
        }
        out.put("series", series);
        out.put("markers", markers);
        out.put("truncated", truncated);
        return out;
    }

    /** Queue length over time, starting with the last value seen before the window (carried to its start). */
    private boolean queueSeries(IncidentView inc, String queueId, Timeline.Window w, List<Map<String, Object>> series) {
        List<Map<String, Object>> before = jdbc.query("""
                SELECT (data->>'length')::int, (data->>'openRegisters')::int
                  FROM pqt.event
                 WHERE store_id = ? AND type = ? AND data->>'queueId' = ?
                   AND sim_run_id IS NOT DISTINCT FROM ?
                   AND event_time < ? AND event_time >= ?
                 ORDER BY event_time DESC, event_seq DESC
                 LIMIT 1""",
                (rs, i) -> point(w.from(), rs.getInt(1), (Integer) rs.getObject(2)),
                inc.storeId(), QUEUE_LENGTH, queueId, inc.simRunId(), ts(w.from()), ts(w.from().minus(Timeline.MAX_SPAN)));
        series.addAll(before);
        List<Map<String, Object>> rows = jdbc.query("""
                SELECT event_time, (data->>'length')::int, (data->>'openRegisters')::int
                  FROM pqt.event
                 WHERE store_id = ? AND type = ? AND data->>'queueId' = ?
                   AND sim_run_id IS NOT DISTINCT FROM ?
                   AND event_time >= ? AND event_time <= ?
                 ORDER BY event_time, event_seq
                 LIMIT ?""",
                (rs, i) -> point(rs.getObject(1, OffsetDateTime.class).toInstant(), rs.getInt(2), (Integer) rs.getObject(3)),
                inc.storeId(), QUEUE_LENGTH, queueId, inc.simRunId(), ts(w.from()), ts(w.to()), Timeline.MAX_POINTS + 1);
        boolean truncated = rows.size() > Timeline.MAX_POINTS;
        series.addAll(truncated ? rows.subList(0, Timeline.MAX_POINTS) : rows);
        return truncated;
    }

    private List<Map<String, Object>> registerMarkers(IncidentView inc, Timeline.Window w) {
        return jdbc.query("""
                SELECT event_time, type, data->>'registerId'
                  FROM pqt.event
                 WHERE store_id = ? AND type IN (?, ?)
                   AND sim_run_id IS NOT DISTINCT FROM ?
                   AND event_time >= ? AND event_time <= ?
                 ORDER BY event_time, event_seq
                 LIMIT ?""",
                (rs, i) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("t", rs.getObject(1, OffsetDateTime.class).toInstant().toString());
                    m.put("kind", REGISTER_OPENED.equals(rs.getString(2)) ? "register.opened" : "register.closed");
                    m.put("registerId", rs.getString(3));
                    return m;
                },
                inc.storeId(), REGISTER_OPENED, REGISTER_CLOSED, inc.simRunId(), ts(w.from()), ts(w.to()), Timeline.MAX_MARKERS);
    }

    /** Zone entries per minute, buckets aligned to the window start; empty minutes are filled with 0. */
    private boolean zoneEntries(IncidentView inc, String zoneId, Timeline.Window w, List<Map<String, Object>> series) {
        Map<Instant, Integer> counts = new LinkedHashMap<>();
        jdbc.query("""
                SELECT date_bin('1 minute', event_time, ?::timestamptz) AS bucket, count(*)::int
                  FROM pqt.event
                 WHERE store_id = ? AND type = ? AND data->>'zoneId' = ?
                   AND sim_run_id IS NOT DISTINCT FROM ?
                   AND event_time >= ? AND event_time < ?
                 GROUP BY bucket
                 ORDER BY bucket""",
                rs -> {
                    counts.put(rs.getObject(1, OffsetDateTime.class).toInstant(), rs.getInt(2));
                },
                ts(w.from()), inc.storeId(), ZONE_ENTERED, zoneId, inc.simRunId(), ts(w.from()), ts(w.to()));
        for (Instant b = w.from(); b.isBefore(w.to()); b = b.plusSeconds(60)) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("t", b.toString());
            m.put("entries", counts.getOrDefault(b, 0));
            series.add(m);
        }
        return false;
    }

    private static Map<String, Object> point(Instant t, int length, Integer openRegisters) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("t", t.toString());
        m.put("length", length);
        m.put("openRegisters", openRegisters);
        return m;
    }

    private static Integer threshold(IncidentView inc) {
        if (inc.attrs() != null && inc.attrs().path("threshold").canConvertToInt()) {
            return inc.attrs().path("threshold").intValue();
        }
        return null;
    }

    private static OffsetDateTime ts(Instant i) {
        return i.atOffset(ZoneOffset.UTC);
    }
}
