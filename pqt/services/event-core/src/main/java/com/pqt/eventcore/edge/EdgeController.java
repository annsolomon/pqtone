package com.pqt.eventcore.edge;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

/** GET /api/stores/{storeId}/edge: the store's edge boxes and their billing connectors (X1 B5f). */
@RestController
@PreAuthorize("hasRole('viewer')")
public class EdgeController {
    private static final Pattern STORE_ID = Pattern.compile("^[a-z0-9-]{1,64}$");

    private final EdgeService edge;
    private final Clock clock;

    public EdgeController(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.edge = new EdgeService(jdbc, mapper);
        this.clock = Clock.systemUTC();
    }

    @GetMapping("/api/stores/{storeId}/edge")
    public Map<String, Object> edge(@PathVariable String storeId) {
        if (!STORE_ID.matcher(storeId).matches()) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "bad-store-id", "invalid store id");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("storeId", storeId);
        out.put("generatedAt", clock.instant().toString());
        out.put("staleAfterSeconds", EdgeService.STALE_AFTER.toSeconds());
        out.put("agents", edge.agents(storeId, clock.instant()));
        return out;
    }
}
