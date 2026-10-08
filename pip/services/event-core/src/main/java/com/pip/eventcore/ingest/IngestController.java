package com.pip.eventcore.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pip.eventcore.config.PipProperties;
import com.pip.eventcore.web.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * HTTP ingest for edge gateways and partners. Structured-mode CloudEvents only.
 *   POST /v1/events        application/cloudevents+json        202 accepted | 200 duplicate | 409 conflict
 *   POST /v1/events/batch  application/cloudevents-batch+json  200 with per-item results
 */
@RestController
@RequestMapping("/v1/events")
public class IngestController {
    public static final String CE_JSON = "application/cloudevents+json";
    public static final String CE_BATCH = "application/cloudevents-batch+json";

    private final ObjectMapper mapper;
    private final EventValidator validator;
    private final SourcePolicy policy;
    private final IngestService ingest;
    private final RateLimiter limiter;
    private final PipProperties.Ingest cfg;

    public IngestController(ObjectMapper mapper, EventValidator validator, SourcePolicy policy, IngestService ingest,
                            RateLimiter limiter, PipProperties props) {
        this.mapper = mapper;
        this.validator = validator;
        this.policy = policy;
        this.ingest = ingest;
        this.limiter = limiter;
        this.cfg = props.ingest();
    }

    @PostMapping(consumes = CE_JSON, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> single(@RequestBody byte[] body, @AuthenticationPrincipal Jwt jwt) {
        if (body.length > cfg.maxEventBytes()) {
            throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "event-too-large", "event exceeds " + cfg.maxEventBytes() + " bytes");
        }
        String client = clientId(jwt);
        throttle(client, 1);
        ValidatedEvent e = validator.validate(parse(body));
        authorize(client, e.source());
        IngestService.Result r = ingest.ingest(e, "http", "client:" + client);
        return switch (r.status()) {
            case ACCEPTED -> ResponseEntity.status(HttpStatus.ACCEPTED).body(result(r, "accepted"));
            case DUPLICATE -> ResponseEntity.ok(result(r, "duplicate"));
            case CONFLICT -> throw new ApiException(HttpStatus.CONFLICT, "conflicting-duplicate",
                    "an event with this source and id was already accepted with a different payload",
                    Map.of("source", r.source(), "id", r.id()));
        };
    }

    @PostMapping(path = "/batch", consumes = CE_BATCH, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> batch(@RequestBody byte[] body, @AuthenticationPrincipal Jwt jwt) {
        if (body.length > cfg.maxBatchBytes()) {
            throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "batch-too-large", "batch exceeds " + cfg.maxBatchBytes() + " bytes");
        }
        JsonNode arr = parse(body);
        if (!arr.isArray()) throw new ApiException(HttpStatus.BAD_REQUEST, "not-a-batch", "batch body must be a JSON array");
        if (arr.size() > cfg.maxBatchSize()) {
            throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "batch-too-large", "batch exceeds " + cfg.maxBatchSize() + " events");
        }
        String client = clientId(jwt);
        throttle(client, Math.max(1, arr.size()));
        List<Map<String, Object>> results = new ArrayList<>();
        for (int i = 0; i < arr.size(); i++) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("index", i);
            item.put("id", arr.get(i).path("id").asText(null));
            try {
                ValidatedEvent e = validator.validate(arr.get(i));
                if (!policy.clientMayPublish(client, e.source())) {
                    item.put("status", "forbidden");
                } else {
                    item.put("status", ingest.ingest(e, "http", "client:" + client).status().name().toLowerCase());
                }
            } catch (InvalidEventException ex) {
                item.put("status", "invalid");
                item.put("errors", ex.errors());
            }
            results.add(item);
        }
        return ResponseEntity.ok(Map.of("results", results));
    }

    private JsonNode parse(byte[] body) {
        try {
            return mapper.readTree(body);
        } catch (Exception ex) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "malformed-json", "body is not valid JSON");
        }
    }

    private static String clientId(Jwt jwt) {
        String azp = jwt.getClaimAsString("azp");
        return azp != null ? azp : jwt.getClaimAsString("client_id");
    }

    private void authorize(String client, String source) {
        if (!policy.clientMayPublish(client, source)) {
            throw new ApiException(HttpStatus.FORBIDDEN, "source-not-allowed",
                    "client " + client + " may not publish events for source " + source);
        }
    }

    private void throttle(String client, int n) {
        long wait = limiter.acquire(client == null ? "anonymous" : client, n);
        if (wait > 0) {
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "rate-limited", "ingest rate limit exceeded",
                    Map.of("retryAfterSeconds", wait));
        }
    }

    private static Map<String, Object> result(IngestService.Result r, String status) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("status", status);
        m.put("source", r.source());
        m.put("id", r.id());
        return m;
    }
}
