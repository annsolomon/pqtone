package com.pip.eventcore.store;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pip.eventcore.config.PipProperties;
import com.pip.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;

/** Read models for the console: layouts, current floor state, recent events, identity, pipeline health. */
@RestController
@PreAuthorize("hasRole('viewer')")
public class StoreController {
    private static final Pattern STORE_ID = Pattern.compile("^[a-z0-9-]{1,64}$");

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final PipProperties props;

    public StoreController(JdbcTemplate jdbc, ObjectMapper mapper, PipProperties props) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.props = props;
    }

    /** Any signed-in user, even without a console role, so the UI can explain missing access. */
    @GetMapping("/api/me")
    @PreAuthorize("isAuthenticated()")
    public Map<String, Object> me(Authentication auth) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("username", auth.getName());
        if (auth.getPrincipal() instanceof OidcUser u) m.put("name", u.getFullName());
        List<String> roles = new ArrayList<>();
        for (GrantedAuthority a : auth.getAuthorities()) {
            if (a.getAuthority().startsWith("ROLE_")) roles.add(a.getAuthority().substring(5));
        }
        roles.sort(null);
        m.put("roles", roles);
        return m;
    }

    @GetMapping("/api/stores")
    public List<Map<String, Object>> stores() throws IOException {
        List<Map<String, Object>> out = new ArrayList<>();
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(Path.of(props.paths().layouts()), "*.json")) {
            for (Path p : ds) {
                JsonNode n = mapper.readTree(Files.readAllBytes(p));
                out.add(Map.of("storeId", n.path("storeId").asText(), "name", n.path("name").asText()));
            }
        }
        out.sort((a, b) -> String.valueOf(a.get("storeId")).compareTo(String.valueOf(b.get("storeId"))));
        return out;
    }

    @GetMapping("/api/stores/{storeId}/layout")
    public JsonNode layout(@PathVariable String storeId) throws IOException {
        Path p = layoutPath(storeId);
        if (!Files.isRegularFile(p)) throw new ApiException(HttpStatus.NOT_FOUND, "not-found", "unknown store");
        return mapper.readTree(Files.readAllBytes(p));
    }

    /** Current floor state for the most recent run of this store (or live data when not simulated). */
    @GetMapping("/api/stores/{storeId}/state")
    public Map<String, Object> state(@PathVariable String storeId) {
        checkId(storeId);
        List<String> runs = jdbc.queryForList("""
                SELECT sim_run_id FROM pip.event WHERE store_id = ? ORDER BY received_at DESC, event_seq DESC LIMIT 1""",
                String.class, storeId);
        String run = runs.isEmpty() ? null : runs.get(0);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("storeId", storeId);
        out.put("simRunId", run);
        Map<String, Object> queues = new TreeMap<>();
        jdbc.query("""
                SELECT DISTINCT ON (data->>'queueId') data->>'queueId', (data->>'length')::int, (data->>'openRegisters')::int, event_time
                  FROM pip.event
                 WHERE store_id = ? AND type = 'com.pip.store.queue.length' AND sim_run_id IS NOT DISTINCT FROM ?
                 ORDER BY data->>'queueId', event_time DESC, event_seq DESC""",
                rs -> {
                    queues.put(rs.getString(1), Map.of("length", rs.getInt(2), "openRegisters", rs.getInt(3),
                            "at", rs.getObject(4, OffsetDateTime.class).toInstant().toString()));
                }, storeId, run);
        Map<String, Integer> zones = new TreeMap<>();
        jdbc.query("""
                SELECT data->>'zoneId',
                       sum(CASE WHEN type = 'com.pip.store.zone.entered' THEN 1 ELSE -1 END)::int
                  FROM pip.event
                 WHERE store_id = ? AND type IN ('com.pip.store.zone.entered', 'com.pip.store.zone.exited')
                   AND sim_run_id IS NOT DISTINCT FROM ?
                 GROUP BY 1""",
                rs -> {
                    zones.put(rs.getString(1), Math.max(0, rs.getInt(2)));
                }, storeId, run);
        out.put("queues", queues);
        out.put("zones", zones);
        return out;
    }

    @GetMapping("/api/events/recent")
    public List<Map<String, Object>> recent(@RequestParam String storeId, @RequestParam(defaultValue = "50") int limit) {
        checkId(storeId);
        return jdbc.query("""
                SELECT id, type, event_time, subject, sim_run_id, data::text
                  FROM pip.event WHERE store_id = ?
                 ORDER BY received_at DESC, event_seq DESC LIMIT ?""",
                (rs, i) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", rs.getString(1));
                    m.put("type", rs.getString(2).replace("com.pip.store.", ""));
                    m.put("time", rs.getObject(3, OffsetDateTime.class).toInstant().toString());
                    m.put("storeId", storeId);
                    m.put("subject", rs.getString(4));
                    m.put("simRunId", rs.getString(5));
                    try {
                        m.put("data", mapper.readTree(rs.getString(6)));
                    } catch (IOException e) {
                        m.put("data", null);
                    }
                    return m;
                }, storeId, Math.min(Math.max(limit, 1), 200));
    }

    @GetMapping("/api/pipeline/health")
    public Map<String, Object> pipelineHealth() {
        List<Map<String, Object>> tasks = jdbc.query(
                "SELECT task_id, received_at FROM pip.pipeline_heartbeat ORDER BY task_id",
                (rs, i) -> Map.of("taskId", rs.getString(1),
                        "receivedAt", rs.getObject(2, OffsetDateTime.class).toInstant().toString()));
        Instant latest = tasks.stream().map(t -> Instant.parse((String) t.get("receivedAt"))).max(Instant::compareTo).orElse(null);
        Duration stale = props.pipeline().heartbeatStaleAfter();
        boolean ok = latest != null && latest.isAfter(Instant.now().minus(stale));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", ok ? "ok" : "degraded");
        out.put("lastHeartbeatAt", latest == null ? null : latest.toString());
        out.put("staleAfterSeconds", stale.toSeconds());
        out.put("tasks", tasks);
        return out;
    }

    private Path layoutPath(String storeId) {
        checkId(storeId);
        return Path.of(props.paths().layouts()).resolve(storeId + ".json").normalize();
    }

    private static void checkId(String storeId) {
        if (!STORE_ID.matcher(storeId).matches()) throw new ApiException(HttpStatus.BAD_REQUEST, "bad-store-id", "invalid store id");
    }
}
