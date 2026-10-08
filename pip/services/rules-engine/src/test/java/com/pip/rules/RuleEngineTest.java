package com.pip.rules;

import com.pip.rules.app.Json;
import com.pip.rules.domain.Event;
import com.pip.rules.domain.Incident;
import com.pip.rules.domain.RuleConfig;
import com.pip.rules.domain.RuleEngine;
import com.pip.rules.domain.StoreState;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RuleEngineTest {
    static final long S = 1000;

    static RuleConfig cfg(String absMode) {
        return new RuleConfig(30 * S,
                new RuleConfig.RuleMeta("R-QUEUE-001", "1.0.0", "enforce", "medium"), 6, 60 * S, 2, 30 * S,
                new RuleConfig.RuleMeta("R-DWELL-001", "1.0.0", "enforce", "low"), Set.of("fitting-rooms"), 600 * S, 4 * 3600 * S,
                new RuleConfig.RuleMeta("R-ABS-001", "1.0.0", absMode, "high"), 300 * S, Event.REGISTER_OPENED);
    }

    static int seq = 0;

    static Event ev(long t, String type) {
        Event e = new Event();
        e.id = "e" + (++seq);
        e.source = "urn:pip:sim:test";
        e.type = type;
        e.timeMs = t;
        e.sequence = String.format("%010d", seq);
        e.storeId = "store-001";
        e.simRunId = "run-000000000000";
        return e;
    }

    static Event queue(long t, int len) {
        Event e = ev(t, Event.QUEUE_LENGTH);
        e.queueId = "checkout-1";
        e.length = len;
        return e;
    }

    static Event zone(long t, String type, String track) {
        Event e = ev(t, type);
        e.zoneId = "fitting-rooms";
        e.trackId = track;
        return e;
    }

    static Event tick(long t) {
        return ev(t, "com.pip.store.clock.tick");
    }

    static List<Incident> feed(RuleEngine engine, List<Event> events) {
        StoreState s = new StoreState();
        List<Incident> out = new ArrayList<>();
        for (Event e : events) out.addAll(engine.onEvent(s, e, 0).incidents);
        return out;
    }

    static List<String> summary(List<Incident> incs) {
        List<String> out = new ArrayList<>();
        for (Incident i : incs) out.add(i.ruleId + ":" + i.kind + ":" + i.key + "@" + i.detectedMs);
        return out;
    }

    @Test
    void queueOpensAfterSustainAndClearsWithHysteresis() {
        RuleEngine engine = new RuleEngine(cfg("off"));
        List<Event> in = new ArrayList<>(List.of(queue(0, 6), queue(10 * S, 7), queue(80 * S, 5)));
        for (long t = 10 * S; t <= 400 * S; t += 10 * S) in.add(tick(t));
        in.add(queue(100 * S, 4));
        in.sort((a, b) -> Long.compare(a.timeMs, b.timeMs));
        List<String> got = summary(feed(engine, in));
        assertEquals(List.of("R-QUEUE-001:OPENED:checkout-1@60000", "R-QUEUE-001:RESOLVED:checkout-1@130000"), got);
    }

    @Test
    void briefBreachDoesNotOpen() {
        RuleEngine engine = new RuleEngine(cfg("off"));
        List<Event> in = List.of(queue(0, 8), queue(59 * S, 3), tick(200 * S));
        assertTrue(feed(engine, in).isEmpty());
    }

    @Test
    void absenceFiresWhenNoRegisterOpensAndIsCancelledWhenOneDoes() {
        RuleEngine engine = new RuleEngine(cfg("shadow"));
        List<Event> fires = new ArrayList<>(List.of(queue(0, 9)));
        for (long t = 10 * S; t <= 500 * S; t += 10 * S) fires.add(tick(t));
        List<Incident> out = feed(engine, fires);
        assertTrue(summary(out).contains("R-ABS-001:OPENED:checkout-1@360000"), summary(out).toString());
        assertEquals("shadow", out.stream().filter(i -> i.ruleId.equals("R-ABS-001")).findFirst().orElseThrow().mode);

        List<Event> cancelled = new ArrayList<>(List.of(queue(0, 9)));
        Event reg = ev(200 * S, Event.REGISTER_OPENED);
        reg.registerId = "reg-2";
        cancelled.add(reg);
        for (long t = 210 * S; t <= 500 * S; t += 10 * S) cancelled.add(tick(t));
        assertTrue(summary(feed(engine, cancelled)).stream().noneMatch(s -> s.startsWith("R-ABS-001")));
    }

    @Test
    void dwellOpensAfterLimitAndResolvesOnExit() {
        RuleEngine engine = new RuleEngine(cfg("off"));
        List<Event> in = new ArrayList<>(List.of(zone(0, Event.ZONE_ENTERED, "trk-1"), zone(700 * S, Event.ZONE_EXITED, "trk-1")));
        for (long t = 10 * S; t <= 800 * S; t += 10 * S) in.add(tick(t));
        in.sort((a, b) -> Long.compare(a.timeMs, b.timeMs));
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    @Test
    void reorderingWithinGraceGivesIdenticalIncidents() {
        RuleEngine engine = new RuleEngine(cfg("shadow"));
        List<Event> ordered = new ArrayList<>();
        Random r = new Random(7);
        int len = 0;
        for (long t = 0; t < 3600 * S; t += 5 * S) {
            len = Math.max(0, Math.min(12, len + r.nextInt(3) - 1));
            ordered.add(queue(t, len));
            if (t % (10 * S) == 0) ordered.add(tick(t));
        }
        List<String> expected = summary(feed(engine, ordered));
        assertTrue(expected.size() > 0);

        List<Event> shuffled = new ArrayList<>(ordered);
        // swap neighbours that are less than grace apart
        for (int i = 0; i + 3 < shuffled.size(); i += 4) Collections.swap(shuffled, i, i + 3);
        assertEquals(expected, summary(feed(engine, shuffled)));
    }

    @Test
    void eventsBehindTheWatermarkAreDroppedAndCounted() {
        RuleEngine engine = new RuleEngine(cfg("off"));
        StoreState s = new StoreState();
        engine.onEvent(s, tick(100 * S), 0);
        RuleEngine.Outcome late = engine.onEvent(s, queue(10 * S, 9), 0);
        assertTrue(late.late);
        assertEquals(1, s.lateDropped);
    }

    @Test
    void incidentIdsAreDeterministic() {
        RuleEngine engine = new RuleEngine(cfg("off"));
        List<Event> in = List.of(queue(0, 9), tick(200 * S));
        assertEquals(feed(engine, in).get(0).incidentId, feed(engine, in).get(0).incidentId);
    }

    @Test
    void stateSurvivesJsonRoundTrip() throws Exception {
        RuleEngine engine = new RuleEngine(cfg("shadow"));
        StoreState s = new StoreState();
        engine.onEvent(s, queue(0, 9), 0);
        engine.onEvent(s, zone(1 * S, Event.ZONE_ENTERED, "trk-9"), 0);
        engine.onEvent(s, tick(40 * S), 0);
        StoreState copy = Json.MAPPER.readValue(Json.MAPPER.writeValueAsBytes(s), StoreState.class);
        List<Event> rest = List.of(tick(100 * S), tick(700 * S));
        List<String> a = new ArrayList<>();
        List<String> b = new ArrayList<>();
        for (Event e : rest) {
            a.addAll(summary(engine.onEvent(s, e, 0).incidents));
            b.addAll(summary(engine.onEvent(copy, e, 0).incidents));
        }
        assertEquals(a, b);
        assertTrue(a.size() >= 2);
    }
}
