package com.pqt.eventcore.billing;

import com.pqt.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.DateTimeException;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * X1 milestone B3: sales, payments and stock for the console, next to footfall and queues (B7 draws them).
 * Read-only; every parameter is checked before it reaches SQL, and SQL takes them only as bind values.
 */
@RestController
@RequestMapping("/api/stores/{storeId}")
@PreAuthorize("hasRole('viewer')")
public class BillingController {
    /** The longest window one call may cover: the billing ingest age (ADR-036). */
    static final Duration MAX_SPAN = Duration.ofDays(35);
    /** Hourly buckets are for a day or a week, not a month of 840 rows per currency and tender. */
    static final Duration MAX_HOURLY_SPAN = Duration.ofDays(7);
    static final String DEFAULT_ZONE = "Asia/Kolkata";
    static final int MAX_LIMIT = 1000;
    private static final Pattern STORE_ID = StoreScope.STORE_ID;
    private static final Pattern TILL_ID = Pattern.compile("^[a-z0-9-]{1,64}$");
    private static final Pattern RUN_ID = Pattern.compile("^run-[a-f0-9]{12}$");
    private static final Pattern SKU = Pattern.compile("^[A-Za-z0-9._-]{1,64}$");
    private static final Pattern ZONE = Pattern.compile("^[A-Za-z]+(/[A-Za-z0-9_+-]+){0,2}$");

    private final BillingService service;

    public BillingController(BillingService service) {
        this.service = service;
    }

    /** Bills and sales per hour or per day (in the store's time zone), optionally for one till. */
    @GetMapping("/sales")
    public Map<String, Object> sales(@PathVariable String storeId, @RequestParam String from, @RequestParam String to,
                                     @RequestParam(defaultValue = "day") String bucket,
                                     @RequestParam(defaultValue = DEFAULT_ZONE) String tz,
                                     @RequestParam(required = false) String till,
                                     @RequestParam(required = false) String run) {
        return service.sales(window(storeId, from, to, bucket, tz, till, run));
    }

    /** Money in and out per tender, per hour or per day, optionally for one till. */
    @GetMapping("/payments")
    public Map<String, Object> payments(@PathVariable String storeId, @RequestParam String from, @RequestParam String to,
                                        @RequestParam(defaultValue = "day") String bucket,
                                        @RequestParam(defaultValue = DEFAULT_ZONE) String tz,
                                        @RequestParam(required = false) String till,
                                        @RequestParam(required = false) String run) {
        return service.payments(window(storeId, from, to, bucket, tz, till, run));
    }

    /** Stock in and out per SKU and reason, with the latest reported level; paged by SKU. */
    @GetMapping("/stock")
    public Map<String, Object> stock(@PathVariable String storeId, @RequestParam String from, @RequestParam String to,
                                     @RequestParam(required = false) String after,
                                     @RequestParam(defaultValue = "200") int limit,
                                     @RequestParam(required = false) String run) {
        check(storeId, STORE_ID, "bad-store-id", "invalid store id");
        if (after != null) check(after, SKU, "bad-sku", "after must be a SKU code");
        if (run != null) check(run, RUN_ID, "bad-run", "run must be a simulator run id");
        if (limit < 1 || limit > MAX_LIMIT) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-limit", "limit must be between 1 and " + MAX_LIMIT);
        }
        Instant[] span = span(from, to, MAX_SPAN);
        return service.stock(storeId, run, span[0], span[1], after, limit);
    }

    static BillingService.Window window(String storeId, String from, String to, String bucket, String tz, String till,
                                        String run) {
        check(storeId, STORE_ID, "bad-store-id", "invalid store id");
        if (till != null) check(till, TILL_ID, "bad-till", "invalid till id");
        if (run != null) check(run, RUN_ID, "bad-run", "run must be a simulator run id");
        BillingService.Bucket b = switch (bucket) {
            case "hour" -> BillingService.Bucket.HOUR;
            case "day" -> BillingService.Bucket.DAY;
            default -> throw new ApiException(HttpStatus.BAD_REQUEST, "bad-bucket", "bucket must be hour or day");
        };
        Instant[] span = span(from, to, b == BillingService.Bucket.HOUR ? MAX_HOURLY_SPAN : MAX_SPAN);
        return new BillingService.Window(storeId, run, span[0], span[1], b, zone(tz), till);
    }

    static ZoneId zone(String tz) {
        if (!ZONE.matcher(tz).matches() || !ZoneId.getAvailableZoneIds().contains(tz)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-time-zone", "tz must be an IANA time zone such as Asia/Kolkata");
        }
        try {
            return ZoneId.of(tz);
        } catch (DateTimeException e) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-time-zone", "tz must be an IANA time zone such as Asia/Kolkata");
        }
    }

    static Instant[] span(String from, String to, Duration max) {
        Instant f = instant(from, "from");
        Instant t = instant(to, "to");
        if (!t.isAfter(f)) throw new ApiException(HttpStatus.BAD_REQUEST, "bad-window", "to must be after from");
        if (Duration.between(f, t).compareTo(max) > 0) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "window-too-long", "the window may cover at most " + max.toDays() + " days");
        }
        return new Instant[] {f, t};
    }

    private static Instant instant(String value, String name) {
        try {
            return Instant.parse(value);
        } catch (DateTimeParseException e) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-time", name + " must be an RFC 3339 timestamp, e.g. 2026-05-28T00:00:00Z");
        }
    }

    private static void check(String value, Pattern p, String code, String detail) {
        if (!p.matcher(value).matches()) throw new ApiException(HttpStatus.BAD_REQUEST, code, detail);
    }
}
