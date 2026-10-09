package com.pqt.eventcore.audit;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/admin/audit")
@PreAuthorize("hasRole('admin')")
public class AuditController {
    private final AuditService audit;

    public AuditController(AuditService audit) {
        this.audit = audit;
    }

    @GetMapping
    public List<Map<String, Object>> recent(@RequestParam(defaultValue = "100") int limit) {
        return audit.recent(limit);
    }

    @GetMapping("/verify")
    public Map<String, Object> verify() {
        return audit.verify();
    }
}
