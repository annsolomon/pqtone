package com.pqt.eventcore.billing;

import com.pqt.eventcore.web.ApiException;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** X1 milestone B3: every billing read parameter is checked before it reaches SQL. */
class BillingControllerTest {
    static final String FROM = "2026-05-28T00:00:00Z";
    static final String TO = "2026-05-29T00:00:00Z";

    static String code(Runnable r) {
        return assertThrows(ApiException.class, r::run).code();
    }

    @Test
    void aValidWindowKeepsEveryParameter() {
        BillingService.Window w = BillingController.window("store-001", FROM, TO, "hour", "Asia/Kolkata", "till-1", "run-0123456789ab");
        assertEquals("store-001", w.storeId());
        assertEquals(Instant.parse(FROM), w.from());
        assertEquals(Instant.parse(TO), w.to());
        assertEquals(BillingService.Bucket.HOUR, w.bucket());
        assertEquals(ZoneId.of("Asia/Kolkata"), w.zone());
        assertEquals("till-1", w.tillId());
        assertEquals("run-0123456789ab", w.runId());
        BillingService.Window live = BillingController.window("store-001", FROM, TO, "day", "UTC", null, null);
        assertNull(live.runId(), "no run means live data");
        assertNull(live.tillId());
    }

    @Test
    void badIdentifiersAreRefused() {
        assertEquals("bad-store-id", code(() -> BillingController.window("Store 1", FROM, TO, "day", "UTC", null, null)));
        assertEquals("bad-store-id", code(() -> BillingController.window("../etc", FROM, TO, "day", "UTC", null, null)));
        assertEquals("bad-till", code(() -> BillingController.window("store-001", FROM, TO, "day", "UTC", "till 1", null)));
        assertEquals("bad-run", code(() -> BillingController.window("store-001", FROM, TO, "day", "UTC", null, "run-1")));
        assertEquals("bad-bucket", code(() -> BillingController.window("store-001", FROM, TO, "week", "UTC", null, null)));
    }

    @Test
    void timeZonesMustBeKnownIanaNames() {
        assertEquals(ZoneId.of("America/Argentina/Buenos_Aires"), BillingController.zone("America/Argentina/Buenos_Aires"));
        for (String bad : List.of("Mars/Olympus", "+05:30", "Asia/Kolkata'; --", "", "GMT+5:30")) {
            assertEquals("bad-time-zone", code(() -> BillingController.zone(bad)), bad);
        }
    }

    @Test
    void windowsMustBeOrderedAndBounded() {
        assertEquals("bad-time", code(() -> BillingController.span("yesterday", TO, BillingController.MAX_SPAN)));
        assertEquals("bad-time", code(() -> BillingController.span(FROM, "2026-05-29", BillingController.MAX_SPAN)));
        assertEquals(Instant.parse("2026-05-28T18:30:00Z"),
                BillingController.span(FROM, "2026-05-29T00:00:00+05:30", BillingController.MAX_SPAN)[1], "an RFC 3339 offset is fine");
        assertEquals("bad-window", code(() -> BillingController.span(TO, FROM, BillingController.MAX_SPAN)));
        assertEquals("bad-window", code(() -> BillingController.span(FROM, FROM, BillingController.MAX_SPAN)));
        BillingController.span("2026-04-24T00:00:00Z", TO, BillingController.MAX_SPAN);  // exactly 35 days before 29 May
        assertEquals("window-too-long", code(() -> BillingController.span("2026-04-23T23:59:59Z", TO, BillingController.MAX_SPAN)));
        assertEquals("window-too-long", code(() ->
                BillingController.window("store-001", "2026-05-20T00:00:00Z", TO, "hour", "UTC", null, null)));
        BillingController.window("store-001", "2026-05-20T00:00:00Z", TO, "day", "UTC", null, null);  // 9 days of days is fine
        assertEquals(BillingService.Bucket.QUARTER_HOUR,
                BillingController.window("store-001", "2026-05-27T00:00:00Z", TO, "15m", "UTC", null, null).bucket());
        assertEquals("window-too-long", code(() ->
                BillingController.window("store-001", "2026-05-26T23:59:59Z", TO, "15m", "UTC", null, null)));
    }

    @Test
    void bucketStartsAreTheInstantsOfLocalMidnight() {
        assertEquals("2026-05-27T18:30:00Z", BillingService.start(LocalDateTime.parse("2026-05-28T00:00:00"), ZoneId.of("Asia/Kolkata")));
        // Daylight saving: local midnight in New York is 04:00 UTC in summer and 05:00 UTC in winter.
        assertEquals("2026-07-01T04:00:00Z", BillingService.start(LocalDateTime.parse("2026-07-01T00:00:00"), ZoneId.of("America/New_York")));
        assertEquals("2026-01-01T05:00:00Z", BillingService.start(LocalDateTime.parse("2026-01-01T00:00:00"), ZoneId.of("America/New_York")));
    }

    @Test
    void totalsSumEveryBucketPerKey() {
        List<Map<String, Object>> buckets = List.of(
                Map.of("currency", "INR", "tender", "card", "inMinor", 100L, "paymentsIn", 1),
                Map.of("currency", "INR", "tender", "cash", "inMinor", 50L, "paymentsIn", 2),
                Map.of("currency", "INR", "tender", "card", "inMinor", 30L, "paymentsIn", 1),
                Map.of("currency", "USD", "tender", "card", "inMinor", 7L, "paymentsIn", 1));
        List<Map<String, Object>> t = BillingService.totals(buckets, List.of("currency", "tender"), List.of("inMinor", "paymentsIn"));
        assertEquals(List.of(
                Map.of("currency", "INR", "tender", "card", "inMinor", 130L, "paymentsIn", 2L),
                Map.of("currency", "INR", "tender", "cash", "inMinor", 50L, "paymentsIn", 2L),
                Map.of("currency", "USD", "tender", "card", "inMinor", 7L, "paymentsIn", 1L)), t);
    }
}
