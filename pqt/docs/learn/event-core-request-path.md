# One HTTP event through event-core

Milestone E1. What happens, bean by bean, between an edge gateway posting one CloudEvent and that
event arriving on Kafka for the rules engine. Class and method names are the real ones; if this
page and the code disagree, the code is right and this page needs fixing.

## The path at a glance

```
edge box ──HTTPS──► nginx gateway ──► event-core (Tomcat, port 8080)
                    location /v1/       │
                    limit_req ingest    ├─ 1. Spring Security: ingestChain (Order 1, /v1/**)
                                        │     BearerTokenAuthenticationFilter → NimbusJwtDecoder (JWKS + issuer)
                                        │     → JwtAuthenticationConverter(realm roles) → hasRole('event-producer')
                                        ├─ 2. DispatcherServlet → IngestController.single(byte[], Jwt)
                                        │     size check → RateLimiter.acquire → ObjectMapper.readTree
                                        ├─ 3. EventValidator.validate → ValidatedEvent (or InvalidEventException)
                                        ├─ 4. SourcePolicy.clientMayPublish(client, source)
                                        ├─ 5. IngestService.ingest — ONE transaction:
                                        │       event_dedup INSERT … ON CONFLICT DO NOTHING
                                        │       ├─ new      → event INSERT, outbox INSERT        → 202 accepted
                                        │       ├─ same hash → nothing written                    → 200 duplicate
                                        │       └─ other hash→ AuditService.record (hash chain)   → 409 conflict
                                        └─ 6. HTTP response (RFC 9457 problem JSON on errors via ApiExceptionHandler)
                                              … later, on its own thread …
                                        7. OutboxRelay.relay (@Scheduled, every 200 ms)
                                              SELECT … FOR UPDATE SKIP LOCKED → KafkaTemplate.send (idempotent, acks=all)
                                              → all acked → UPDATE published_at          → topic store.events.v1, key = storeId
```

The response is sent at step 6. Kafka is not on the request path at all: the event is durable the
moment the transaction in step 5 commits, and step 7 moves it to Kafka afterwards. That is the
**transactional outbox** pattern, and it is why a Kafka outage never loses an accepted event
(`OutboxRelayIT` proves it, milestone E4).

## Step by step

### 0. The gateway

`deploy/gateway/nginx.conf`, `location /v1/`: adds the security headers, applies the `ingest`
request-rate zone (`limit_req … burst=400`), and proxies to event-core. TLS ends at nginx.

### 1. The security filter chain

`config/SecurityConfig` defines four `SecurityFilterChain` beans; Spring Security picks the first
whose `securityMatcher` matches the request, in `@Order`:

| Order | Bean | Matches | What it does |
|---:|---|---|---|
| 0 | `actuatorChain` | actuator endpoints | permits; actuator listens on the management port, which nginx never routes |
| **1** | **`ingestChain`** | **`/v1/**`** | **stateless OAuth2 resource server; requires `ROLE_event-producer`; no CSRF, no session** |
| 2 | `consoleChain` | `/api/**`, `/oauth2/**`, `/login/**`, `/logout` | OIDC login, session, SPA CSRF (the console, not this path) |
| 3 | `denyByDefault` | everything else | denies |

For `/v1/events` the interesting filter is `BearerTokenAuthenticationFilter`:

1. It reads `Authorization: Bearer …`. Missing → 401 (`test_ingest_requires_a_token`).
2. The `ingestJwtDecoder` bean (`NimbusJwtDecoder`) checks the signature against Keycloak's JWKS
   (fetched over the internal network) and that `iss` equals the **public** issuer URL. A forged
   token → 401 (`test_ingest_rejects_a_forged_token`).
3. `JwtAuthenticationConverter` with `realmRoles()` turns `realm_access.roles` into
   `ROLE_…` authorities.
4. The authorization filter checks `hasRole('event-producer')`.

Producers get tokens with the OAuth2 **client credentials** grant (`edge-demo`, `store-sim`
clients in Keycloak). No user is involved.

### 2. The controller

`ingest/IngestController.single` receives the raw `byte[]` and the authenticated `Jwt`
(`@AuthenticationPrincipal`). In order:

1. **Size**: more than `pqt.ingest.max-event-bytes` → 413 before any parsing.
2. **Client id**: the `azp` claim (or `client_id`).
3. **Rate limit**: `RateLimiter.acquire(client, 1)`, a token bucket per client (bean in
   `IngestBeans`). Empty bucket → 429 with `retryAfterSeconds`. This is the second limit after
   nginx's; nginx protects the process, this one is fair between producers.
4. **Parse**: `ObjectMapper.readTree`. Not JSON → 400 `malformed-json`.

Only `application/cloudevents+json` is accepted (`consumes = CE_JSON`); anything else is 415
before the method is called.

### 3. Validation

`ingest/EventValidator.validate` (bean from `IngestBeans`, built with the `SchemaRegistry` bean
that loaded `schemas/catalog.json` at startup). It collects **every** error before failing:

- the envelope schema (CloudEvents attributes, `schemas/envelope.schema.json`);
- the `type` is known and its `dataschema` version is registered (E2 added `queue.length` 1.1.0);
- `data` against that version's JSON Schema;
- `subject` against the type's pattern;
- `time` is RFC 3339, not more than `maxFutureSkew` ahead, and (for real producers) not older
  than `maxAge`; simulator sources replay a fixed epoch, so only they may be old, and only they
  may carry `simrunid`;
- `partitionkey` equals `storeid`.

Failure throws `InvalidEventException`; `web/ApiExceptionHandler.invalid` turns it into a 400
problem JSON with all the errors (`test_contract_violations_are_reported_together`). Success
returns a `ValidatedEvent` record that also carries `Canonical.eventHash(node)`: SHA-256 of
the canonical JSON, without transport-only attributes (`traceparent`, `tracestate`), so a retry
with a new trace context still counts as the same event.

### 4. Who may publish what

`ingest/SourcePolicy.clientMayPublish(client, source)`: each OAuth client has a list of allowed
`source` prefixes (`pqt.ingest.client-sources`). The `edge-demo` client cannot send events claiming
to come from the simulator → 403 `source-not-allowed`
(`test_client_cannot_publish_for_another_source`).

### 5. The transaction

`ingest/IngestService.ingest` wraps `write` in `TransactionTemplate.execute`, times it
(`pqt.ingest.duration`) and counts the result. Inside one database transaction, as `pqt_app`:

1. `INSERT INTO pqt.event_dedup (source, id, payload_sha256) … ON CONFLICT (source, id) DO NOTHING`.
   This ledger is the dedup key. It is a separate table because a unique constraint on the
   partitioned `pqt.event` would have to include the partition key (`received_at`).
2. **Inserted** (first time): `INSERT INTO pqt.event` (partitioned by `received_at`, append-only
   by trigger) and `INSERT INTO pqt.outbox (topic, msg_key = storeId, payload, headers)` with
   `ce_id`, `ce_source`, `ce_type` and `traceparent` headers. Result `ACCEPTED`.
3. **Not inserted**: read the stored hash. Same hash → `DUPLICATE`, nothing written. Different
   hash → `CONFLICT`: `AuditService.record` appends a row to the hash-chained `pqt.audit_log`
   (holding an advisory lock so the chain stays linear) and nothing else is written.

**Why two concurrent identical requests are safe.** Under READ COMMITTED, the second
`INSERT … ON CONFLICT DO NOTHING` waits on the first transaction's uncommitted row. When the
first commits, the second sees the conflict, inserts nothing and reads the committed hash. So
exactly one wins, and the other is a duplicate. `IngestIT` runs this race 40 times with a
barrier releasing both threads together.

Everything in step 5 commits or nothing does: there is never an event row without its outbox
row, or the other way round.

### 6. The response

`ACCEPTED` → 202, `DUPLICATE` → 200, `CONFLICT` → `ApiException` → 409 problem JSON
(`conflicting-duplicate`, `application/problem+json`). Errors never include a stack trace
(`server.error.include-stacktrace: never`).

### 7. Outbox → Kafka

`outbox/OutboxRelay.relay`, `@Scheduled(fixedDelay = pqt.outbox.poll-ms, default 200)`, on a
scheduler thread, independent of any request:

1. In a transaction: `SELECT … FROM pqt.outbox WHERE published_at IS NULL ORDER BY outbox_id
   LIMIT 500 FOR UPDATE SKIP LOCKED`. `SKIP LOCKED` lets several event-core instances relay in
   parallel without taking the same rows.
2. `KafkaTemplate.send` per row, copying the headers. The producer is idempotent with `acks=all`
   (`spring.kafka.producer` in `application.yml`).
3. Wait for **every** send in the batch to be acknowledged, then `UPDATE … SET published_at =
   now()`. If any send fails, the whole transaction rolls back and the rows are picked up again
   next pass.

A crash after the sends but before the update republishes those rows: delivery is
**at-least-once**. That is fine because every consumer is idempotent: the rules engine dedups on
`(source, id)` and incidents are keyed by a deterministic `incident_id`.

`measureBacklog` exports `pqt.outbox.unpublished.rows` every 15 s, and `purgePublished` deletes
published rows older than `pqt.retention.outbox-hours`.

### The other way in: the raw topic

The simulator does not use HTTP. It produces to `store.events.raw`; `ingest/RawEventConsumer`
(`@KafkaListener`, manual acks) runs the same `EventValidator`, `SourcePolicy.rawTopicAllows` and
`IngestService.ingest`, with `channel = "kafka"`. Invalid events go to the DLQ via `DlqPublisher`.
Offsets are committed only after the transaction or the DLQ write succeeds.

## The Spring beans involved

| Bean | Where it comes from | Role on this path |
|---|---|---|
| `ingestChain` | `SecurityConfig` | Chooses JWT auth for `/v1/**` |
| `ingestJwtDecoder` | `SecurityConfig` | Verifies token signature and issuer |
| `IngestController` | `@RestController` | HTTP mapping, size, rate limit, parse, response codes |
| `rateLimiter` | `IngestBeans` | Per-client token bucket |
| `schemaRegistry` | `IngestBeans` | Loads the catalog and schemas once |
| `eventValidator` | `IngestBeans` | Contract checks, canonical hash |
| `sourcePolicy` | `IngestBeans` | Client → allowed sources |
| `IngestService` | `@Service` | The one write transaction |
| `AuditService` | `@Service` | Hash-chained audit on conflicts |
| `transactionTemplate`, `jdbcTemplate`, `dataSource` | Spring Boot auto-configuration | Transaction boundaries and SQL |
| `OutboxRelay` | `@Component` | Moves committed rows to Kafka |
| `kafkaTemplate` | Spring Boot auto-configuration | Idempotent producer |
| `ApiExceptionHandler` | `@RestControllerAdvice` | Exceptions → RFC 9457 problem JSON |

## Seeing it in Jaeger

Tracing is on: `management.tracing.sampling.probability` defaults to 1.0 and spans go over OTLP
to the `jaeger` container (`micrometer-tracing-bridge-otel`).

```bash
make up                         # stack running
make summary                    # prints the Jaeger URL: http://localhost:16686 (forward port 16686 in Codespaces)
make e2e-http                   # sends real ingest requests through the gateway
```

In Jaeger, pick service `event-core`, operation `http post /v1/events`, and open a trace. You see
the server span for the request with the JDBC work of step 5 inside it. The Kafka send happens
later, in a separate trace started by `OutboxRelay`. The producer's own `traceparent` (if it sent
one) travels as a Kafka header, and the rules engine copies it onto the incidents it emits
(`StoreRulesProcessor`), so an incident can be tied back to the request that carried its evidence.

## Questions to check yourself

1. Why does a Kafka outage not turn into HTTP 5xx for producers? (Step 7 is off the request path.)
2. A producer retries with a new `traceparent`. Duplicate or conflict? (Duplicate: transport
   attributes are not part of the canonical hash.)
3. Why `FOR UPDATE SKIP LOCKED` and not just `FOR UPDATE`? (Several relays in parallel; plain
   `FOR UPDATE` would make them queue behind each other.)
4. What would break if the outbox `INSERT` ran after the transaction commits? (A crash between the
   two loses the event for Kafka while the producer was told it was accepted.)
