package com.pip.eventcore.incident;

import com.pip.eventcore.config.Roles;
import com.pip.eventcore.web.ApiException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/incidents")
public class IncidentController {

    public record ActionRequest(@NotBlank @Pattern(regexp = "ack|confirm|dismiss|close") String action,
                                @Size(max = 32) String reasonCode,
                                @Size(max = 500) String note) {
    }

    private final IncidentService service;
    private final TimelineService timelines;

    public IncidentController(IncidentService service, TimelineService timelines) {
        this.service = service;
        this.timelines = timelines;
    }

    @GetMapping
    @PreAuthorize("hasRole('viewer')")
    public List<IncidentView> list(@RequestParam(defaultValue = "enforce") String mode,
                                   @RequestParam(required = false) String status,
                                   @RequestParam(required = false) String storeId,
                                   @RequestParam(required = false) String ruleId,
                                   @RequestParam(defaultValue = "100") int limit,
                                   Authentication auth) {
        if (!mode.equals("enforce") && !mode.equals("shadow")) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-mode", "mode must be enforce or shadow");
        }
        if (mode.equals("shadow") && !roles(auth).contains(Roles.ADMIN)) {
            throw new ApiException(HttpStatus.FORBIDDEN, "insufficient-role", "shadow incidents are visible to admins only");
        }
        Set<String> statuses = status == null ? Set.of()
                : Arrays.stream(status.split(",")).map(String::trim).filter(s -> !s.isEmpty()).collect(Collectors.toSet());
        return service.list(mode, statuses, storeId, ruleId, limit);
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasRole('viewer')")
    public ResponseEntity<Map<String, Object>> get(@PathVariable UUID id, Authentication auth) {
        IncidentView v = visible(id, auth);
        return ResponseEntity.ok().eTag("\"" + v.version() + "\"")
                .body(Map.of("incident", v, "reviews", service.reviews(id)));
    }

    /** Milestone C4: the events around the incident, so a reviewer can see why it fired. Same visibility as GET. */
    @GetMapping("/{id}/timeline")
    @PreAuthorize("hasRole('viewer')")
    public Map<String, Object> timeline(@PathVariable UUID id, Authentication auth) {
        return timelines.timeline(visible(id, auth));
    }

    @PostMapping("/{id}/actions")
    @PreAuthorize("hasRole('operator')")
    public ResponseEntity<IncidentView> act(@PathVariable UUID id,
                                            @RequestHeader(value = "If-Match", required = false) String ifMatch,
                                            @Valid @RequestBody ActionRequest body, Authentication auth) {
        if (ifMatch == null || ifMatch.isBlank()) {
            throw new ApiException(HttpStatus.PRECONDITION_REQUIRED, "if-match-required",
                    "send If-Match with the ETag from GET /api/incidents/{id}");
        }
        int version;
        try {
            version = Integer.parseInt(ifMatch.replace("W/", "").replace("\"", "").trim());
        } catch (NumberFormatException e) {
            throw new ApiException(HttpStatus.PRECONDITION_FAILED, "bad-etag", "If-Match is not a valid ETag");
        }
        IncidentView v = service.act(id, body.action(), body.reasonCode(), body.note(), version, auth.getName(), roles(auth));
        return ResponseEntity.ok().eTag("\"" + v.version() + "\"").body(v);
    }

    /** The incident if this user may see it; shadow incidents are admin-only and look absent to everyone else. */
    private IncidentView visible(UUID id, Authentication auth) {
        IncidentView v = service.find(id).orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "not-found", "incident not found"));
        if ("shadow".equals(v.mode()) && !roles(auth).contains(Roles.ADMIN)) {
            throw new ApiException(HttpStatus.NOT_FOUND, "not-found", "incident not found");
        }
        return v;
    }

    static Set<String> roles(Authentication auth) {
        Set<String> out = new HashSet<>();
        for (GrantedAuthority a : auth.getAuthorities()) {
            if (a.getAuthority().startsWith("ROLE_")) out.add(a.getAuthority().substring(5));
        }
        return out;
    }
}
