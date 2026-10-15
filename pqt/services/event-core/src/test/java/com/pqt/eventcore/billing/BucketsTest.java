package com.pqt.eventcore.billing;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/** X1 milestone B7: Java buckets match the SQL's date_bin on the local clock; queue figures are time-weighted. */
class BucketsTest {
    static final ZoneId IST = ZoneId.of("Asia/Kolkata");
    static final ZoneId NEPAL = ZoneId.of("Asia/Kathmandu");  // UTC+05:45
    static final Instant T0 = Instant.parse("2026-05-28T04:30:00Z");  // 10:00 IST
    static final BillingService.Bucket Q = BillingService.Bucket.QUARTER_HOUR;

    static LocalDateTime local(String s) {
        return LocalDateTime.parse(s);
    }

    @Test
    void binsFollowTheLocalClockFromLocalMidnight() {
        assertEquals(List.of(local("2026-05-28T09:30"), local("2026-05-28T09:45"), local("2026-05-28T10:00"), local("2026-05-28T10:15")),
                Buckets.covering(Instant.parse("2026-05-28T04:00:00Z"), Instant.parse("2026-05-28T05:00:00Z"), IST, Q));
        assertEquals(List.of(local("2026-05-28T09:45"), local("2026-05-28T10:00"), local("2026-05-28T10:15"), local("2026-05-28T10:30")),
                Buckets.covering(Instant.parse("2026-05-28T04:00:00Z"), Instant.parse("2026-05-28T05:00:00Z"), NEPAL, Q),
                "a +05:45 zone still gets :00/:15/:30/:45 bins");
        assertEquals(List.of(local("2026-05-28T00:00"), local("2026-05-29T00:00")),
                Buckets.covering(Instant.parse("2026-05-27T18:30:00Z"), Instant.parse("2026-05-29T18:30:00Z"), IST, BillingService.Bucket.DAY));
        assertEquals(local("2026-05-28T10:00"), Buckets.floor(Instant.parse("2026-05-28T05:29:59Z"), IST, BillingService.Bucket.HOUR));
    }

    @Test
    void queueLengthIsTimeWeightedPerBinWithTheValueCarriedIn() {
        List<Buckets.Step> steps = List.of(new Buckets.Step(T0.minusSeconds(300), 2), new Buckets.Step(T0.plusSeconds(300), 6),
                new Buckets.Step(T0.plusSeconds(1200), 0));
        Instant to = T0.plusSeconds(2700);
        Map<LocalDateTime, Buckets.Stat> s = Buckets.stats(steps, Buckets.covering(T0, to, IST, Q), T0, to, IST, Q);
        assertEquals(new Buckets.Stat(4.67, 6), s.get(local("2026-05-28T10:00")), "5 min at 2 then 10 min at 6");
        assertEquals(new Buckets.Stat(2.0, 6), s.get(local("2026-05-28T10:15")), "5 min at 6 then 10 min at 0");
        assertEquals(new Buckets.Stat(0.0, 0), s.get(local("2026-05-28T10:30")));
    }

    @Test
    void nothingKnownBeforeTheFirstValue() {
        List<Buckets.Step> steps = List.of(new Buckets.Step(T0.plusSeconds(1200), 3));
        Instant to = T0.plusSeconds(1800);
        Map<LocalDateTime, Buckets.Stat> s = Buckets.stats(steps, Buckets.covering(T0, to, IST, Q), T0, to, IST, Q);
        assertNull(s.get(local("2026-05-28T10:00")).mean());
        assertNull(s.get(local("2026-05-28T10:00")).max());
        assertEquals(new Buckets.Stat(3.0, 3), s.get(local("2026-05-28T10:15")), "known for the last 10 minutes only");
    }

    @Test
    void aWindowStartingMidBinCountsOnlyTheWindow() {
        List<Buckets.Step> steps = List.of(new Buckets.Step(T0.minusSeconds(300), 2), new Buckets.Step(T0.plusSeconds(300), 6),
                new Buckets.Step(T0.plusSeconds(1200), 0));
        Instant from = T0.plusSeconds(600);
        Instant to = T0.plusSeconds(1500);
        Map<LocalDateTime, Buckets.Stat> s = Buckets.stats(steps, Buckets.covering(from, to, IST, Q), from, to, IST, Q);
        assertEquals(new Buckets.Stat(6.0, 6), s.get(local("2026-05-28T10:00")));
        assertEquals(new Buckets.Stat(3.0, 6), s.get(local("2026-05-28T10:15")));
    }

    @Test
    void aValueReplacedAtTheSameInstantNeverCounts() {
        List<Buckets.Step> steps = List.of(new Buckets.Step(T0, 9), new Buckets.Step(T0, 1));
        Instant to = T0.plusSeconds(900);
        assertEquals(new Buckets.Stat(1.0, 1), Buckets.stats(steps, Buckets.covering(T0, to, IST, Q), T0, to, IST, Q)
                .get(local("2026-05-28T10:00")));
    }
}
