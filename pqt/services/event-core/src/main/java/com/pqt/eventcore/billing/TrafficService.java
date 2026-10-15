package com.pqt.eventcore.billing;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * X1 milestone B7: what the cameras saw, in the same buckets as the sales: visitors (entries into the layout's
 * entrance zones, the footfall rule's count) and each queue's time-weighted mean and peak length. Only counts
 * leave the database, never a track id. Read from the append-only event table, bounded by store, run, window
 * and a row limit.
 */
@Service
public class TrafficService {
    static final String ZONE_ENTERED = "com.pqt.store.zone.entered";
    static final String QUEUE_LENGTH = "com.pqt.store.queue.length";
    /** How far before the window to look for the queue length carried into it. */
    static final Duration CARRY_IN = Duration.ofHours(12);
    /** Upper bound on queue-length rows read for one call; beyond it the response says it was truncated. */
    static final int MAX_QUEUE_ROWS = 50_000;

    private final JdbcTemplate jdbc;
    private final TransactionTemplate readOnly;

    public TrafficService(JdbcTemplate jdbc, TransactionTemplate tx) {
        this.jdbc = jdbc;
        this.readOnly = new TransactionTemplate(tx.getTransactionManager(), tx);
        this.readOnly.setReadOnly(true);
    }

    public Map<String, Object> traffic(BillingService.Window w, List<String> entranceZones, List<String> queueIds) {
        List<LocalDateTime> bins = Buckets.covering(w.from(), w.to(), w.zone(), w.bucket());
        Map<LocalDateTime, Integer> visitors = new HashMap<>();
        Map<String, List<Buckets.Step>> steps = new LinkedHashMap<>();
        for (String q : queueIds) steps.put(q, new ArrayList<>());
        boolean truncated = Boolean.TRUE.equals(readOnly.execute(status -> {
            if (!entranceZones.isEmpty()) {
                String in = String.join(", ", Collections.nCopies(entranceZones.size(), "?"));  // placeholders only
                List<Object> args = new ArrayList<>(List.of(w.bucket().interval, w.zone().getId(), w.storeId(), ZONE_ENTERED));
                args.add(w.runId());
                args.add(utc(w.from()));
                args.add(utc(w.to()));
                args.addAll(entranceZones);
                jdbc.query("""
                        SELECT date_bin(?::interval, event_time AT TIME ZONE ?, timestamp '2000-01-01 00:00:00'), count(*)::int
                          FROM pqt.event
                         WHERE store_id = ? AND type = ? AND sim_run_id IS NOT DISTINCT FROM ?
                           AND event_time >= ? AND event_time < ?
                           AND data->>'zoneId' IN (%s)
                           AND coalesce(data->>'how', 'crossed') <> 'appeared'
                         GROUP BY 1""".formatted(in),
                        rs -> {
                            visitors.put(rs.getObject(1, LocalDateTime.class), rs.getInt(2));
                        }, args.toArray());
            }
            if (queueIds.isEmpty()) return false;
            jdbc.query("""
                    SELECT DISTINCT ON (data->>'queueId') data->>'queueId', event_time, (data->>'length')::int
                      FROM pqt.event
                     WHERE store_id = ? AND type = ? AND sim_run_id IS NOT DISTINCT FROM ?
                       AND event_time < ? AND event_time >= ?
                     ORDER BY data->>'queueId', event_time DESC, event_seq DESC""",
                    rs -> {
                        List<Buckets.Step> s = steps.get(rs.getString(1));
                        if (s != null) s.add(new Buckets.Step(rs.getObject(2, OffsetDateTime.class).toInstant(), rs.getInt(3)));
                    },
                    w.storeId(), QUEUE_LENGTH, w.runId(), utc(w.from()), utc(w.from().minus(CARRY_IN)));
            int[] rows = {0};
            jdbc.query("""
                    SELECT data->>'queueId', event_time, (data->>'length')::int
                      FROM pqt.event
                     WHERE store_id = ? AND type = ? AND sim_run_id IS NOT DISTINCT FROM ?
                       AND event_time >= ? AND event_time < ?
                     ORDER BY event_time, event_seq
                     LIMIT ?""",
                    rs -> {
                        if (++rows[0] > MAX_QUEUE_ROWS) return;  // the extra row only says there is more
                        List<Buckets.Step> s = steps.get(rs.getString(1));
                        if (s != null) s.add(new Buckets.Step(rs.getObject(2, OffsetDateTime.class).toInstant(), rs.getInt(3)));
                    },
                    w.storeId(), QUEUE_LENGTH, w.runId(), utc(w.from()), utc(w.to()), MAX_QUEUE_ROWS + 1);
            return rows[0] > MAX_QUEUE_ROWS;
        }));

        Map<String, Map<LocalDateTime, Buckets.Stat>> stats = new LinkedHashMap<>();
        for (Map.Entry<String, List<Buckets.Step>> e : steps.entrySet()) {
            stats.put(e.getKey(), Buckets.stats(e.getValue(), bins, w.from(), w.to(), w.zone(), w.bucket()));
        }
        List<Map<String, Object>> buckets = new ArrayList<>();
        for (LocalDateTime bin : bins) {
            Map<String, Object> b = new LinkedHashMap<>();
            b.put("start", BillingService.start(bin, w.zone()));
            b.put("visitors", entranceZones.isEmpty() ? null : visitors.getOrDefault(bin, 0));
            List<Map<String, Object>> queues = new ArrayList<>();
            for (Map.Entry<String, Map<LocalDateTime, Buckets.Stat>> e : stats.entrySet()) {
                Buckets.Stat st = e.getValue().get(bin);
                Map<String, Object> q = new LinkedHashMap<>();
                q.put("queueId", e.getKey());
                q.put("meanLength", st.mean());
                q.put("maxLength", st.max());
                queues.add(q);
            }
            b.put("queues", queues);
            buckets.add(b);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("storeId", w.storeId());
        out.put("simRunId", w.runId());
        out.put("from", w.from().toString());
        out.put("to", w.to().toString());
        out.put("bucket", w.bucket().label());
        out.put("timeZone", w.zone().getId());
        out.put("entranceZones", entranceZones);
        out.put("buckets", buckets);
        out.put("truncated", truncated);
        return out;
    }

    private static OffsetDateTime utc(Instant i) {
        return i.atOffset(ZoneOffset.UTC);
    }
}
