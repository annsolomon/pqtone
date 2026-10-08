package com.pip.rules;

import com.pip.rules.domain.RuleEngine;
import com.pip.rules.domain.StoreState;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * The hand-made sequence from docs/learn/watermarks.md, worked through the real engine.
 * Grace is 30 s, so after each event: watermark = (highest event time so far) - 30 s, and an
 * arriving event is late when its time is at or below the watermark already reached.
 * If this test and the doc's table ever disagree, the doc is wrong.
 */
class WatermarkLatenessTest {
    private static final long S = 1000;

    /** {arrival order, event time in seconds, expected late?, expected watermark (s) after it}. */
    private static final Object[][] SEQUENCE = {
            {1, 0L, false, -30L},
            {2, 50L, false, 20L},
            {3, 25L, false, 20L},    // out of order but above the watermark: buffered, not late
            {4, 20L, true, 20L},     // equal to the watermark: late
            {5, 100L, false, 70L},
            {6, 69L, true, 70L},
            {7, 71L, false, 70L},
            {8, 100L, false, 70L},   // same time as stream time: fine
            {9, 40L, true, 70L},
            {10, 130L, false, 100L},
            {11, 100L, true, 100L},  // equal to the watermark again: late
            {12, 101L, false, 100L},
    };

    @Test
    void handMadeSequenceDropsExactlyThePredictedEvents() {
        RuleEngine engine = new RuleEngine(RuleEngineTest.cfg("off"));
        StoreState s = new StoreState();
        List<Integer> late = new ArrayList<>();
        for (Object[] row : SEQUENCE) {
            int n = (Integer) row[0];
            long t = (Long) row[1] * S;
            boolean dropped = engine.onEvent(s, RuleEngineTest.tick(t), 0).late;
            assertEquals(row[2], dropped, "event " + n + " at t=" + row[1] + "s");
            assertEquals((Long) row[3] * S, s.watermarkMs, "watermark after event " + n);
            if (dropped) late.add(n);
        }
        assertEquals(List.of(4, 6, 9, 11), late);
        assertEquals(4, s.lateDropped);
        // Everything at or below the final watermark (100 s) has been released in time order:
        // events 1, 3, 2, 7, 5, 8. Events 10 (130 s) and 12 (101 s) wait in the buffer.
        assertEquals(6, s.processed);
        assertEquals(List.of(101 * S, 130 * S), s.buffer.stream().map(e -> e.timeMs).toList());
    }

    @Test
    void ticksMoveTheWatermarkWithoutAnyStoreActivity() {
        RuleEngine engine = new RuleEngine(RuleEngineTest.cfg("off"));
        StoreState s = new StoreState();
        for (long t = 0; t <= 300 * S; t += 10 * S) engine.onEvent(s, RuleEngineTest.tick(t), 0);
        assertEquals(270 * S, s.watermarkMs);
        assertEquals(3, s.buffer.size()); // 280 s, 290 s and 300 s are still inside the grace window
    }
}
