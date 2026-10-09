package com.pip.eventcore.review;

/**
 * Wilson score interval for a proportion, the same formula as the scorer's (scorer/pip_scorer/gates.py,
 * milestone Q4), so a reviewer confirm rate and an offline precision are read with the same caution.
 */
public final class Wilson {
    /** 95% two-sided. */
    public static final double Z95 = 1.96;

    private Wilson() {
    }

    public record Interval(double low, double high) {
    }

    /** n == 0 says nothing: (0, 1). */
    public static Interval interval(int successes, int n, double z) {
        if (successes < 0 || successes > n) throw new IllegalArgumentException("need 0 <= successes <= n");
        if (n <= 0) return new Interval(0.0, 1.0);
        double p = (double) successes / n;
        double d = 1 + z * z / n;
        double centre = (p + z * z / (2.0 * n)) / d;
        double half = z * Math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n)) / d;
        return new Interval(Math.max(0.0, centre - half), Math.min(1.0, centre + half));
    }
}
