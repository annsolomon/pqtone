package com.pip.eventcore.review;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Milestone R6: the promotion verdict mirrors the scorer's point and Wilson gates. */
class RuleReadinessTest {
    static RuleReadiness.Totals totals(int tp, int fp, int fn) {
        return new RuleReadiness.Totals(tp, fp, fn, 0.90, 0.95, 20, 0.80);
    }

    @Test
    void enoughCleanEvidenceIsReady() {
        RuleReadiness.Verdict v = RuleReadiness.assess("shadow", totals(60, 1, 0));
        assertTrue(v.ready(), v.reasons().toString());
        assertEquals(List.of(), v.reasons());
        assertEquals(0.9836, v.precision(), 1e-4);
        assertTrue(v.precisionLow() >= 0.80);
    }

    @Test
    void aFewPerfectRunsAreNotEvidence() {
        RuleReadiness.Verdict v = RuleReadiness.assess("shadow", totals(5, 0, 0));
        assertFalse(v.ready());
        assertEquals(List.of("only 5 samples; need 20"), v.reasons());
    }

    @Test
    void pointThresholdsAndLowerBoundsAreBothChecked() {
        RuleReadiness.Verdict v = RuleReadiness.assess("shadow", totals(20, 4, 1));
        assertFalse(v.ready());
        assertTrue(v.reasons().contains("precision 0.833 below 0.90"), v.reasons().toString());
        assertTrue(v.reasons().stream().anyMatch(r -> r.startsWith("precision lower bound")), v.reasons().toString());
    }

    @Test
    void enforceRulesAreNeverPromotable() {
        RuleReadiness.Verdict v = RuleReadiness.assess("enforce", totals(60, 0, 0));
        assertFalse(v.ready());
        assertEquals(List.of("already enforce"), v.reasons());
    }

    @Test
    void nothingRecordedYetSaysSo() {
        RuleReadiness.Verdict v = RuleReadiness.assess("shadow", totals(0, 0, 0));
        assertNull(v.precision());
        assertNull(v.recallLow());
        assertTrue(v.reasons().contains("no incidents or ground truth recorded yet"));
    }

    @Test
    void withoutAConfidenceGateOnlyThePointThresholdsApply() {
        RuleReadiness.Verdict v = RuleReadiness.assess("shadow", new RuleReadiness.Totals(3, 0, 0, 0.9, 0.9, null, null));
        assertTrue(v.ready(), v.reasons().toString());
    }
}
