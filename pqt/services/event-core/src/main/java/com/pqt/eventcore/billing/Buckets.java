package com.pqt.eventcore.billing;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Bucket arithmetic in Java that matches the SQL ({@code date_bin(stride, t AT TIME ZONE tz, '2000-01-01')}): bins of
 * the store's local wall clock counted from local midnight. Pure functions, unit-tested without a database.
 */
public final class Buckets {
    private Buckets() {
    }

    /** The local start of the bin holding this instant. */
    public static LocalDateTime floor(Instant t, ZoneId zone, BillingService.Bucket bucket) {
        LocalDateTime local = LocalDateTime.ofInstant(t, zone);
        return switch (bucket) {
            case QUARTER_HOUR -> {
                LocalDateTime m = local.truncatedTo(ChronoUnit.MINUTES);
                yield m.minusMinutes(m.getMinute() % 15);
            }
            case HOUR -> local.truncatedTo(ChronoUnit.HOURS);
            case DAY -> local.truncatedTo(ChronoUnit.DAYS);
        };
    }

    public static LocalDateTime next(LocalDateTime start, BillingService.Bucket bucket) {
        return switch (bucket) {
            case QUARTER_HOUR -> start.plusMinutes(15);
            case HOUR -> start.plusHours(1);
            case DAY -> start.plusDays(1);
        };
    }

    /** Every bin that overlaps [from, to), as local starts in order. */
    public static List<LocalDateTime> covering(Instant from, Instant to, ZoneId zone, BillingService.Bucket bucket) {
        List<LocalDateTime> out = new ArrayList<>();
        for (LocalDateTime b = floor(from, zone, bucket); b.atZone(zone).toInstant().isBefore(to); b = next(b, bucket)) {
            out.add(b);
        }
        return out;
    }

    /** One step of a step function: from {@code t} on, the value is {@code value}. */
    public record Step(Instant t, int value) {
    }

    /** Time-weighted mean and maximum of a step function over one bin; null mean when no part of the bin is known. */
    public record Stat(Double mean, Integer max) {
    }

    /**
     * Time-weighted mean and maximum of a step function (a queue's length) per bin, inside [from, to). The function is
     * known from its first step on; {@code steps} must be in time order and may start before {@code from} (the value
     * carried into the window).
     */
    public static Map<LocalDateTime, Stat> stats(List<Step> steps, List<LocalDateTime> bins, Instant from, Instant to,
                                                 ZoneId zone, BillingService.Bucket bucket) {
        Map<LocalDateTime, Stat> out = new LinkedHashMap<>();
        for (LocalDateTime bin : bins) {
            Instant b0 = max(bin.atZone(zone).toInstant(), from);
            Instant b1 = min(next(bin, bucket).atZone(zone).toInstant(), to);
            double area = 0;
            long known = 0;
            Integer peak = null;
            for (int i = 0; i < steps.size(); i++) {
                Instant s0 = steps.get(i).t();
                Instant s1 = i + 1 < steps.size() ? steps.get(i + 1).t() : to;
                Instant a = max(s0, b0);
                Instant z = min(s1, b1);
                if (!a.isBefore(z)) continue;  // no overlap (a value replaced at the same instant never held)
                long ms = Duration.between(a, z).toMillis();
                area += (double) ms * steps.get(i).value();
                known += ms;
                peak = maxOf(peak, steps.get(i).value());
            }
            out.put(bin, new Stat(known == 0 ? null : Math.round(area / known * 100.0) / 100.0, peak));
        }
        return out;
    }

    private static Instant max(Instant a, Instant b) {
        return a.isAfter(b) ? a : b;
    }

    private static Instant min(Instant a, Instant b) {
        return a.isBefore(b) ? a : b;
    }

    private static Integer maxOf(Integer a, int b) {
        return a == null || b > a ? b : a;
    }
}
