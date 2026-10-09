package com.pqt.eventcore;

import com.pqt.eventcore.incident.Timeline;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Milestone C4: the event-time window and subject rules behind GET /api/incidents/{id}/timeline. */
class TimelineTest {
    static final Instant ONSET = Instant.parse("2026-01-01T09:00:00Z");

    @Test
    void resolvedIncidentShowsTenMinutesBeforeOnsetAndFiveAfterResolution() {
        Timeline.Window w = Timeline.window(ONSET, ONSET.plusSeconds(60), ONSET.plusSeconds(900));
        assertEquals(ONSET.minus(Duration.ofMinutes(10)), w.from());
        assertEquals(ONSET.plusSeconds(900).plus(Duration.ofMinutes(5)), w.to());
    }

    @Test
    void openIncidentRunsToTheSpanCap() {
        Timeline.Window w = Timeline.window(ONSET, ONSET.plusSeconds(60), null);
        assertEquals(w.from().plus(Timeline.MAX_SPAN), w.to());
    }

    @Test
    void longIncidentIsCappedAtTwoHoursFromTheWindowStart() {
        Timeline.Window w = Timeline.window(ONSET, ONSET.plusSeconds(60), ONSET.plus(Duration.ofHours(6)));
        assertEquals(Duration.ofHours(2), Duration.between(w.from(), w.to()));
    }

    @Test
    void windowAlwaysCoversDetectionPlusContext() {
        // Resolution recorded at the same instant as detection: still show AFTER past detection.
        Instant detected = ONSET.plusSeconds(60);
        Timeline.Window w = Timeline.window(ONSET, detected, detected);
        assertEquals(detected.plus(Timeline.AFTER), w.to());
    }

    @Test
    void windowNeedsOnsetAndDetection() {
        assertThrows(IllegalArgumentException.class, () -> Timeline.window(null, ONSET, null));
        assertThrows(IllegalArgumentException.class, () -> Timeline.window(ONSET, null, null));
    }

    @Test
    void subjectsMapToQueueOrZone() {
        assertEquals(Optional.of(new Timeline.Target(Timeline.Kind.QUEUE, "checkout-1")), Timeline.target("queue:checkout-1"));
        assertEquals(Optional.of(new Timeline.Target(Timeline.Kind.ZONE, "fitting-rooms")), Timeline.target("zone:fitting-rooms"));
        assertEquals("queue", Timeline.Kind.QUEUE.json());
    }

    @Test
    void unknownOrHostileSubjectsGiveNoTarget() {
        assertTrue(Timeline.target(null).isEmpty());
        assertTrue(Timeline.target("track:trk-1").isEmpty(), "never a per-person timeline");
        assertTrue(Timeline.target("queue:").isEmpty());
        assertTrue(Timeline.target("queue:X'; DROP TABLE pqt.event; --").isEmpty());
        assertTrue(Timeline.target("zone:" + "a".repeat(65)).isEmpty());
    }
}
