package com.pip.eventcore.ingest;

import java.util.concurrent.ConcurrentHashMap;
import java.util.function.LongSupplier;

/** Per-client token bucket (per instance). Gateway and IdP throttling sit in front of this. */
public final class RateLimiter {
    private static final class Bucket {
        double tokens;
        long lastNanos;
    }

    private final double perSecond;
    private final double burst;
    private final LongSupplier nanoClock;
    private final ConcurrentHashMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public RateLimiter(double perSecond, int burst, LongSupplier nanoClock) {
        this.perSecond = perSecond;
        this.burst = burst;
        this.nanoClock = nanoClock;
    }

    /** Try to take n tokens. Returns 0 when allowed, otherwise seconds to wait before retrying. */
    public long acquire(String client, int n) {
        Bucket b = buckets.computeIfAbsent(client, k -> {
            Bucket nb = new Bucket();
            nb.tokens = burst;
            nb.lastNanos = nanoClock.getAsLong();
            return nb;
        });
        synchronized (b) {
            long now = nanoClock.getAsLong();
            b.tokens = Math.min(burst, b.tokens + (now - b.lastNanos) / 1e9 * perSecond);
            b.lastNanos = now;
            if (b.tokens >= n) {
                b.tokens -= n;
                return 0;
            }
            return Math.max(1, (long) Math.ceil((n - b.tokens) / perSecond));
        }
    }
}
