package com.pqt.rules;

import com.pqt.rules.app.Json;
import com.pqt.rules.domain.Event;
import com.pqt.rules.domain.Incident;
import com.pqt.rules.domain.RuleConfig;
import com.pqt.rules.domain.RuleEngine;
import com.pqt.rules.domain.StoreState;
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

    static RuleConfig stitchCfg(long stitchMs) {
        return new RuleConfig(30 * S,
                new RuleConfig.RuleMeta("R-QUEUE-001", "1.0.0", "enforce", "medium"), 6, 60 * S, 2, 30 * S,
                new RuleConfig.RuleMeta("R-DWELL-001", "1.1.0", "enforce", "low"), Set.of("fitting-rooms"), 600 * S,
                4 * 3600 * S, stitchMs,
                new RuleConfig.RuleMeta("R-ABS-001", "1.0.0", "off", "high"), 300 * S, Event.REGISTER_OPENED);
    }

    static int seq = 0;

    static Event ev(long t, String type) {
        Event e = new Event();
        e.id = "e" + (++seq);
        e.source = "urn:pqt:sim:test";
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

    static Event zone(long t, String type, String track, String how) {
        Event e = zone(t, type, track);
        e.how = how;
        return e;
    }

    static List<Event> withTicks(List<Event> events, long until) {
        List<Event> in = new ArrayList<>(events);
        for (long t = 10 * S; t <= until; t += 10 * S) in.add(tick(t));
        in.sort((a, b) -> Long.compare(a.timeMs, b.timeMs));
        return in;
    }

    static Event tick(long t) {
        return ev(t, "com.pqt.store.clock.tick");
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

    // ------------------------------------------------------------------ N4: stitching across tracking breaks

    @Test
    void aVisitContinuesAcrossIdentityChangesAndOpensUnderTheFirstKey() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(200 * S, Event.ZONE_EXITED, "trk-1", "lost"), zone(200 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(400 * S, Event.ZONE_EXITED, "trk-2", "lost"), zone(405 * S, Event.ZONE_ENTERED, "trk-3", "appeared"),
                zone(700 * S, Event.ZONE_EXITED, "trk-3", "crossed")), 800 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    @Test
    void withoutStitchingTheSameBreaksHideTheLongVisit() {
        RuleEngine engine = new RuleEngine(stitchCfg(0));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(200 * S, Event.ZONE_EXITED, "trk-1", "lost"), zone(200 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(400 * S, Event.ZONE_EXITED, "trk-2", "lost"), zone(405 * S, Event.ZONE_ENTERED, "trk-3", "appeared"),
                zone(700 * S, Event.ZONE_EXITED, "trk-3", "crossed")), 800 * S);
        assertTrue(summary(feed(engine, in)).isEmpty(), "1.0.0 behaviour: three short visits, no incident");
    }

    @Test
    void aPersonWalkingInNeverContinuesSomeoneElsesVisit() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(500 * S, Event.ZONE_EXITED, "trk-1", "lost"),
                zone(505 * S, Event.ZONE_ENTERED, "trk-2", "crossed"),   // a real entry: a new visit
                zone(700 * S, Event.ZONE_EXITED, "trk-2", "crossed")), 900 * S);
        assertTrue(summary(feed(engine, in)).isEmpty());
    }

    @Test
    void aLostVisitNobodyContinuesEndsAtTheStitchDeadline() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(580 * S, Event.ZONE_EXITED, "trk-1", "lost"),
                zone(640 * S, Event.ZONE_ENTERED, "trk-2", "appeared")), 1300 * S);  // 60 s later: too late
        // The limit (600 s) passed while waiting, but nobody continued the visit: no incident for trk-1.
        // trk-2 starts its own visit at 640 s and is still open at 1240 s: its own limit.
        assertEquals(List.of("R-DWELL-001:OPENED:trk-2|fitting-rooms@1240000"), summary(feed(engine, in)));
    }

    @Test
    void aLimitThatPassedDuringTheGapIsReportedWhenTheVisitContinues() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(590 * S, Event.ZONE_EXITED, "trk-1", "lost"),
                zone(612 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(700 * S, Event.ZONE_EXITED, "trk-2", "crossed")), 800 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@612000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    @Test
    void theMostRecentlyLostVisitIsTheOneContinued() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-a", "crossed"),
                zone(300 * S, Event.ZONE_ENTERED, "trk-b", "crossed"),
                zone(500 * S, Event.ZONE_EXITED, "trk-a", "lost"),
                zone(510 * S, Event.ZONE_EXITED, "trk-b", "lost"),
                zone(512 * S, Event.ZONE_ENTERED, "trk-c", "appeared"),   // continues trk-b (lost 2 s ago)
                zone(950 * S, Event.ZONE_EXITED, "trk-c", "crossed")), 1000 * S);
        // trk-b entered at 300 s: its limit is at 900 s; trk-a, never continued, ends quietly at 530 s.
        assertEquals(List.of("R-DWELL-001:OPENED:trk-b|fitting-rooms@900000", "R-DWELL-001:RESOLVED:trk-b|fitting-rooms@950000"),
                summary(feed(engine, in)));
    }

    @Test
    void anIdentitySwitchContinuesTheVisitWhicheverEventTheProducerSentFirst() {
        // An identity switch: the old track is lost and the new one appears at the same instant. The producer
        // gave the appearance the lower sequence, so it sorts first; the hand-over must not depend on that.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event in1 = zone(0, Event.ZONE_ENTERED, "trk-1", "crossed");
        Event app2 = zone(200 * S, Event.ZONE_ENTERED, "trk-2", "appeared");
        Event lost1 = zone(200 * S, Event.ZONE_EXITED, "trk-1", "lost");
        Event app3 = zone(400 * S, Event.ZONE_ENTERED, "trk-3", "appeared");
        Event lost2 = zone(400 * S, Event.ZONE_EXITED, "trk-2", "lost");
        Event out3 = zone(700 * S, Event.ZONE_EXITED, "trk-3", "crossed");
        assertTrue(app2.sequence.compareTo(lost1.sequence) < 0, "the appearance sorts before the loss");
        List<Event> in = withTicks(List.of(in1, app2, lost1, app3, lost2, out3), 800 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    @Test
    void aTrackSeenForAnInstantLeavesNoVisitBehind() {
        // A phantom detection that appears and is lost in the same instant: its own exit still ends it.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(100 * S, Event.ZONE_ENTERED, "trk-p", "appeared"),
                zone(100 * S, Event.ZONE_EXITED, "trk-p", "lost")), 1000 * S);
        assertTrue(summary(feed(engine, in)).isEmpty());
    }

    @Test
    void aPieceSeenForAnInstantPassesTheVisitOnToTheNextTrack() {
        // Crowded identity switches at one instant, in the order the producer sent them: trk-3 appears and is lost
        // in the same instant (a piece too short to see), trk-4 appears; trk-2 (carrying the visit) is lost.
        // trk-3 continues the visit and hands it straight on to trk-4.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        List<Event> in = withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(100 * S, Event.ZONE_EXITED, "trk-1", "lost"), zone(101 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(200 * S, Event.ZONE_ENTERED, "trk-3", "appeared"), zone(200 * S, Event.ZONE_ENTERED, "trk-4", "appeared"),
                zone(200 * S, Event.ZONE_EXITED, "trk-2", "lost"), zone(200 * S, Event.ZONE_EXITED, "trk-3", "lost"),
                zone(700 * S, Event.ZONE_EXITED, "trk-4", "crossed")), 800 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    /** Delivers {@code late} right after the tick at {@code after}: behind the watermark (grace 30 s). */
    static List<Event> deliverLate(List<Event> events, Event late, long after) {
        List<Event> in = new ArrayList<>(events);
        int at = 0;
        while (at < in.size() && in.get(at).timeMs <= after) at++;
        in.add(at, late);
        return in;
    }

    @Test
    void aLateLossStillHandsTheVisitToTheTrackThatContinuedIt() {
        // trk-1 is lost at 200 s and trk-2 appears; the loss arrives 100 s late, after trk-2 started its own visit.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event lost = zone(200 * S, Event.ZONE_EXITED, "trk-1", "lost");
        List<Event> in = deliverLate(withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(200 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(700 * S, Event.ZONE_EXITED, "trk-2", "crossed")), 800 * S), lost, 300 * S);
        StoreState s = new StoreState();
        List<Incident> out = new ArrayList<>();
        for (Event e : in) out.addAll(engine.onEvent(s, e, 0).incidents);
        assertEquals(1, s.lateDropped, "the late loss is still counted as late");
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(out));
    }

    @Test
    void aLateLossNobodyContinuedEndsTheVisitInsteadOfLeavingItOpen() {
        // Without the late loss trk-1's visit would stay open until its TTL and report a 10-minute dwell that never was.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event lost = zone(200 * S, Event.ZONE_EXITED, "trk-1", "lost");
        List<Event> in = deliverLate(withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed")), 1000 * S), lost, 300 * S);
        assertTrue(summary(feed(engine, in)).isEmpty());
    }

    @Test
    void aLateExitEndsAVisitThatHasNotBeenReported() {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event out = zone(100 * S, Event.ZONE_EXITED, "trk-1", "crossed");
        List<Event> in = deliverLate(withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed")), 1000 * S), out, 250 * S);
        assertTrue(summary(feed(engine, in)).isEmpty());
    }

    @Test
    void aLateEventNeverReportsAnythingByItself() {
        // The visit was reported at 600 s; its exit arrives late. Nothing is emitted for it: TTL ends it as in 1.0.0.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event out = zone(610 * S, Event.ZONE_EXITED, "trk-1", "crossed");
        List<Event> in = deliverLate(withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed")), 800 * S), out, 700 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000"), summary(feed(engine, in)));
    }

    @Test
    void aLateLossOfAReportedVisitChangesNothing() {
        // Reported at 600 s; the loss at 590 s arrives after that. Ending it now would date the end before the report.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event lost = zone(590 * S, Event.ZONE_EXITED, "trk-1", "lost");
        List<Event> in = deliverLate(withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed")), 900 * S), lost, 700 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000"), summary(feed(engine, in)));
    }

    @Test
    void aLateLossTakesBackAContinuationItWouldHaveWon() {
        // A phantom (trk-p) is lost at 150 s. trk-2, carrying trk-1's visit, is lost at 160 s but that loss arrives late,
        // so trk-3 (appearing at 160 s) continues the phantom's visit. On time, the more recent loss wins: trk-1's.
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        Event lost2 = zone(160 * S, Event.ZONE_EXITED, "trk-2", "lost");
        List<Event> in = deliverLate(withTicks(List.of(
                zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(100 * S, Event.ZONE_EXITED, "trk-1", "lost"), zone(100 * S, Event.ZONE_ENTERED, "trk-2", "appeared"),
                zone(149 * S, Event.ZONE_ENTERED, "trk-p", "appeared"), zone(150 * S, Event.ZONE_EXITED, "trk-p", "lost"),
                zone(160 * S, Event.ZONE_ENTERED, "trk-3", "appeared"),
                zone(700 * S, Event.ZONE_EXITED, "trk-3", "crossed")), 800 * S), lost2, 260 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000", "R-DWELL-001:RESOLVED:trk-1|fitting-rooms@700000"),
                summary(feed(engine, in)));
    }

    @Test
    void withoutStitchingLateExitsAreDroppedAsIn100() {
        RuleEngine engine = new RuleEngine(stitchCfg(0));
        Event out = zone(100 * S, Event.ZONE_EXITED, "trk-1", "crossed");
        List<Event> in = deliverLate(withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed")), 1000 * S), out, 250 * S);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000"), summary(feed(engine, in)));
    }

    @Test
    void stitchingStateSurvivesJsonRoundTrip() throws Exception {
        RuleEngine engine = new RuleEngine(stitchCfg(30 * S));
        StoreState s = new StoreState();
        for (Event e : withTicks(List.of(zone(0, Event.ZONE_ENTERED, "trk-1", "crossed"),
                zone(300 * S, Event.ZONE_EXITED, "trk-1", "lost")), 300 * S)) engine.onEvent(s, e, 0);
        StoreState copy = Json.MAPPER.readValue(Json.MAPPER.writeValueAsBytes(s), StoreState.class);
        List<Event> rest = withTicks(List.of(zone(310 * S, Event.ZONE_ENTERED, "trk-2", "appeared")), 700 * S);
        rest.removeIf(e -> e.timeMs <= 300 * S);
        List<String> a = new ArrayList<>();
        List<String> b = new ArrayList<>();
        for (Event e : rest) {
            a.addAll(summary(engine.onEvent(s, e, 0).incidents));
            b.addAll(summary(engine.onEvent(copy, e, 0).incidents));
        }
        assertEquals(a, b);
        assertEquals(List.of("R-DWELL-001:OPENED:trk-1|fitting-rooms@600000"), a);
    }
}
