package com.pqt.eventcore.review;

import com.pqt.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** Milestone C5: review metrics per rule, admins only. */
@RestController
@PreAuthorize("hasRole('admin')")
public class ReviewMetricsController {
    private final ReviewMetricsService service;

    public ReviewMetricsController(ReviewMetricsService service) {
        this.service = service;
    }

    @GetMapping("/api/admin/review-metrics")
    public Map<String, Object> metrics(@RequestParam(defaultValue = "30") int days) {
        if (days < ReviewMetricsService.MIN_DAYS || days > ReviewMetricsService.MAX_DAYS) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-days", "days must be between 1 and 90");
        }
        return service.metrics(days);
    }
}
