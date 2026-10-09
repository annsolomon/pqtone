package com.pip.eventcore.review;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Milestone R6: is a shadow rule ready to be proposed for enforce mode?
 *
 * Mirrors the scorer's gate (scorer/pip_scorer/gates.py check()): the point precision and recall must
 * reach the rule's thresholds, and once there are at least {@code minN} samples the 95% Wilson lower
 * bounds must reach {@code minLowerBound}. Below {@code minN} the rule is never "ready": a handful of
 * runs is not evidence. Readiness only informs a person; promotion itself is a reviewed pull request.
 */
public final class RuleReadiness {
    private RuleReadiness() {
    }

    public record Totals(int tp, int fp, int fn, double precisionMin, double recallMin, Integer minN, Double minLowerBound) {
    }

    public record Verdict(Double precision, Double recall, Double precisionLow, Double recallLow, boolean ready,
                          List<String> reasons) {
    }

    public static Verdict assess(String mode, Totals t) {
        int nP = t.tp() + t.fp();
        int nR = t.tp() + t.fn();
        Double precision = nP == 0 ? null : (double) t.tp() / nP;
        Double recall = nR == 0 ? null : (double) t.tp() / nR;
        Double pLow = nP == 0 ? null : Wilson.interval(t.tp(), nP, Wilson.Z95).low();
        Double rLow = nR == 0 ? null : Wilson.interval(t.tp(), nR, Wilson.Z95).low();

        List<String> reasons = new ArrayList<>();
        if (!"shadow".equals(mode)) reasons.add("already " + mode);
        if (precision == null || recall == null) {
            reasons.add("no incidents or ground truth recorded yet");
        } else {
            if (precision < t.precisionMin()) reasons.add(fmt("precision %.3f below %.2f", precision, t.precisionMin()));
            if (recall < t.recallMin()) reasons.add(fmt("recall %.3f below %.2f", recall, t.recallMin()));
        }
        if (t.minN() != null) {
            if (nP < t.minN() || nR < t.minN()) {
                reasons.add("only " + Math.min(nP, nR) + " samples; need " + t.minN());
            } else if (t.minLowerBound() != null) {
                if (pLow < t.minLowerBound()) reasons.add(fmt("precision lower bound %.3f below %.2f", pLow, t.minLowerBound()));
                if (rLow < t.minLowerBound()) reasons.add(fmt("recall lower bound %.3f below %.2f", rLow, t.minLowerBound()));
            }
        }
        return new Verdict(round(precision), round(recall), round(pLow), round(rLow), reasons.isEmpty(), List.copyOf(reasons));
    }

    private static String fmt(String pattern, Object... args) {
        return String.format(Locale.ROOT, pattern, args);
    }

    private static Double round(Double v) {
        return v == null ? null : Math.round(v * 10_000.0) / 10_000.0;
    }
}
