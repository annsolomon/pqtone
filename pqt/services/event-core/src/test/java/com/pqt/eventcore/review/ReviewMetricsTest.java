package com.pqt.eventcore.review;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** Milestone C5: the confirm-rate interval matches the scorer's Wilson implementation (scorer/pqt_scorer/gates.py). */
class ReviewMetricsTest {
    static final double EPS = 1e-6;

    @Test
    void wilsonMatchesTheScorer() {
        // Reference values printed by pqt_scorer.gates.wilson(s, n, 1.96).
        assertInterval(Wilson.interval(8, 10, Wilson.Z95), 0.490157, 0.943319);
        assertInterval(Wilson.interval(0, 5, Wilson.Z95), 0.000000, 0.434491);
        assertInterval(Wilson.interval(5, 5, Wilson.Z95), 0.565509, 1.000000);
        assertInterval(Wilson.interval(45, 50, Wilson.Z95), 0.786395, 0.956525);
        assertInterval(Wilson.interval(1, 2, Wilson.Z95), 0.094529, 0.905471);
    }

    @Test
    void noDecisionsSayNothing() {
        assertInterval(Wilson.interval(0, 0, Wilson.Z95), 0.0, 1.0);
        assertThrows(IllegalArgumentException.class, () -> Wilson.interval(3, 2, Wilson.Z95));
        assertThrows(IllegalArgumentException.class, () -> Wilson.interval(-1, 2, Wilson.Z95));
    }

    @Test
    void rowCarriesRateIntervalAndUndecidedCount() {
        Map<String, Object> r = ReviewMetricsService.row("R-QUEUE-001", 12, 11, 10, 8, 2, 42.0, 300.5,
                Map.of("FALSE_POSITIVE", 2));
        assertEquals(2, r.get("undecided"));
        assertEquals(0.8, (Double) r.get("confirmRate"), EPS);
        assertEquals(0.4902, (Double) r.get("confirmRateLow"), EPS);
        assertEquals(0.9433, (Double) r.get("confirmRateHigh"), EPS);
        assertEquals(42.0, r.get("timeToActionP50Seconds"));
        assertEquals(Map.of("FALSE_POSITIVE", 2), r.get("dismissReasons"));
    }

    @Test
    void rowWithoutDecisionsHasNoRate() {
        Map<String, Object> r = ReviewMetricsService.row("R-DWELL-001", 3, 0, 0, 0, 0, null, null, Map.of());
        assertNull(r.get("confirmRate"));
        assertNull(r.get("confirmRateLow"));
        assertNull(r.get("timeToActionP50Seconds"));
        assertEquals(3, r.get("undecided"));
    }

    private static void assertInterval(Wilson.Interval i, double low, double high) {
        assertEquals(low, i.low(), EPS);
        assertEquals(high, i.high(), EPS);
    }
}
