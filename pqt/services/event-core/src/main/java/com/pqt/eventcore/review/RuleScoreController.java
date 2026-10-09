package com.pqt.eventcore.review;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** Milestone R6: live rule scores and shadow-rule promotion readiness, admins only. Read-only: there is no toggle. */
@RestController
@PreAuthorize("hasRole('admin')")
public class RuleScoreController {
    private final RuleScoreService service;

    public RuleScoreController(RuleScoreService service) {
        this.service = service;
    }

    @GetMapping("/api/admin/rule-scores")
    public Map<String, Object> scores() {
        return service.scores();
    }
}
