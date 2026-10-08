# Master build README: the full-scale enterprise platform

**What this is:** the single reference for building the whole platform, from today to an enterprise product, across many separate chats. It covers:
- product and editions;
- architecture and every service;
- data model, APIs and flows;
- security and protecting your code and model from copying;
- securing the LLM in the cloud and on the shop's box;
- operations and compliance;
- every milestone, and the chat-sized work packages.

**Project:** Physical Intelligence Platform. Placeholder name `pip` until milestone 0.7; `<product>` below means the chosen name.
**Repo:** `github.com/trinamichelle29/pqtone` (keep it **private**). Project folder `pip/` until 0.7 renames it.
**Companions:**
- `PHASE-1-README.md`: step-by-step for Tier 1.
- `docs/plans/*.md`: one plan file per stage.
- The plan doc with budget and diagrams: https://claude.ai/code/artifact/4778190e-6cca-4b39-805c-137f0a37811e

**Written:** 9 Oct 2026. Prices and laws change: re-check anything with a date before acting on it.

---

## Contents

0. How to use this file across chats
1. Product: goal, editions, rules that never bend
2. Architecture
3. Data model
4. APIs
5. Key flows
6. Security architecture
7. Protecting your software and model from copying
8. Securing the LLM, in the cloud and on the shop box
9. Deployment and operations
10. Compliance and governance
11. Edge hardware and OS bill of materials
12. Build plan: every phase and milestone
13. Work packages: one chat each
14. Budget and timeline
15. Risks and what to say no to
16. Decision records (ADRs) to write
17. Glossary
18. Sources

---

## 0. How to use this file across chats

**The rule:** one chat = one work package (§13) = one to four milestones. A new chat can't see earlier chats, so every chat starts from files, not memory.

**What is true, in order of authority:**
1. **This README:** what the platform is and why. Commit it as `docs/plans/MASTER-README.md`.
2. **Plan files** in `docs/plans/`: the milestones of the stage you are in, with "Done when". §12 holds the content for plan files that don't exist yet.
3. **ADRs** in `docs/adr/`: every decision that changes 1 or 2. Update this README in the same pull request.
4. **The code and its gates:** `make test-fast`, `make offline`, `make all`, CI.

**Every chat:**
1. **Open it** with the template in §13.3 and attach the files it lists.
2. **Plan in chat; build in Claude Code** in the Codespace with `/milestone <ID>`.
3. **End it** by asking for the handoff note (§13.4) and pasting that note into the next chat.

**IDs used below:**

| Prefix | Area | Prefix | Area |
| --- | --- | --- | --- |
| 0.x, E, S, R, C, Q | Tier 1 | TP, OB, PR, INF, BA, OBS | Tier 3 |
| V, N, A, F | Tier 2 (F = multi-camera) | PL | Pilot |
| X1–X7 | Product and platform expansion | AN, AR | Anomaly and reports |
| FL, CE | Fleet and cells | P | Protection and security track |
| EN | Enterprise readiness | 🧠 | You write the core of it yourself |

---

## 1. Product

### 1.1 The end goal, stated so it can be tested

> A store owner connects cameras to a small edge box we own and lease to them. Within a day they see live queue, dwell and footfall on their floor plan, get alerts that are right more often than not, and review incidents with evidence. Sales and stock from Tally or Zoho appear next to what the cameras saw. They ask questions in plain language, in the shop or from anywhere, and get answers with every number cited. None of this ever identifies a person.
>
> We run many tenants in isolated cells in India. Devices update safely. Quality is measured continuously. Our code and models never leave our control in a form someone can reuse.

### 1.2 Editions (what an enterprise version means here)

| | Starter | Pro | Enterprise |
| --- | --- | --- | --- |
| Stores | 1 | Up to 20 | Unlimited |
| Cameras per store | 2 | 6 | By contract |
| Queue, dwell, footfall, absence alerts | ✓ | ✓ | ✓ |
| Incident review with blurred clips | ✓ | ✓ | ✓ |
| Tally / Zoho / POS connectors | — | ✓ | ✓ |
| Assistant with cited answers (cloud) | — | ✓ | ✓ |
| Shop apps (staffing, stock, shrink, digest) | — | ✓ | ✓ |
| Store AI box (model runs in the shop, offline) | — | — | ✓ |
| Customer SSO (SAML / OIDC) | — | — | ✓ |
| Dedicated cell (isolated stack) | — | — | ✓ |
| API access | — | Read | Read + write, higher quotas |
| Audit log export, custom retention | — | — | ✓ |
| Support | Email, 2 business days | Email + phone, 1 business day | Named contact, SLA |
| Uptime commitment | — | — | 99.5% (matches the SLOs in §9.5) |

Prices are a pilot question, not a decision. The hypothesis to test: ₹3,999–5,999 per checkout zone per month, plus a ₹5,000–10,000 install fee.

### 1.3 Rules that never bend

1. **No person identification, ever.**
   - No face recognition, demographic inference, or cross-day or cross-site re-identification.
   - Track ids rotate daily.
   - Re-identification embeddings live in memory only, for minutes.
2. **Humans decide.** Rules and models raise incidents and suggestions; people confirm, dismiss or approve.
3. **Minimise raw video.**
   - Video stays on the box in an encrypted ring buffer.
   - Clips leave only for a reviewed incident, blurred, with a retention timer.
4. **India only.** Customer data, backups and models live in Indian data centres. DPDP Act 2023 and Rules 2025 alignment from the first pilot.
5. **Every detector and model is scored** against ground truth or an eval set before it ships.
6. **Licences are architecture.**
   - Apache-2.0, MIT, BSD or similar in anything shipped.
   - AGPL, SSPL, BSL or non-commercial only internally, with an ADR.
7. **Nothing valuable ships in reusable form.**
   - Source code, training data, evals and rules never leave our infrastructure.
   - What must run in the shop runs compiled, signed, encrypted and licensed (§7).

### 1.4 What we never build

- Face recognition or any identity feature, even if a customer asks.
- Accounting, GST filing or invoicing (Tally and Zoho do that).
- A model trained from scratch.
- Autonomous actions by any model in a shop's books or systems.
- A self-hosted copy of the cloud platform handed to a customer. Enterprises get a dedicated cell that we run; source escrow if a contract demands it.

---

## 2. Architecture

### 2.1 Zones and boundaries

```text
 SHOP — customer premises; the box is ours, leased            INDIA CLOUD — our E2E Networks accounts
 ┌─────────────────────────────────────────────────┐          ┌──────────────────────────────────────────────────┐
 │ IP cameras ──RTSP──► EDGE BOX (Secure Boot, LUKS+TPM)       │ nginx gateway (TLS, rate limits)                 │
 │   camera-ingest ► perception (motion gate, YOLOX,  │ events  │   ├► event-core ► Redpanda ► rules-engine        │
 │   ByteTrack, zones) ► outbox (SQLite) ► uplink ────┼────────►│   │       │            ├► analytics (ClickHouse) │
 │   clip-buffer (encrypted ring, 10 s segments)      │ HTTPS,  │   │    PostgreSQL (RLS) └► app-runner            │
 │   tally-connector ◄── LAN ── TallyPrime PC         │ out-    │   ├► tenant-api · OpenFGA · Keycloak · billing    │
 │   store-ai (llama.cpp, encrypted weights in RAM) ◄─┼─bound──►│   ├► licence-service (leases, model keys)        │
 │   mTLS proxy :8443 ◄── shop staff apps (LAN only)  │ only    │   ├► registry (Harbor: signed images, models)    │
 │   fleet-updater · lease-agent · heartbeat          │         │   └► model-gateway ► vLLM · retrieval (pgvector) │
 │ No inbound internet ports. No customer root.       │         │ ops-console · Prometheus/Grafana/Jaeger · backups│
 └─────────────────────────────────────────────────┘          └──────────────────────────────────────────────────┘
```

Three rules hold everywhere:
1. The cloud never opens a connection into a shop.
2. Every service owns its data; services share only events and APIs.
3. Everything crossing a boundary is authenticated, encrypted and logged.

### 2.2 Services (28)

**Shop zone (edge box and store AI box)**

| Service | Built in | Language | Owns | Talks to |
| --- | --- | --- | --- | --- |
| camera-ingest | Tier 2 | Python (PyAV), compiled with Nuitka | RTSP sessions, reconnect | perception |
| perception | Tier 2 | Python (OpenVINO, YOLOX-S, ByteTrack), compiled | Motion gate, detections, tracks, zone state | outbox |
| outbox | Tier 2 | Python, compiled | SQLite (WAL) event queue | uplink |
| uplink | Tier 2 | Python, compiled | Batched sending, retries | nginx gateway (outbound) |
| clip-buffer | Tier 2 | FFmpeg (LGPL build, dynamically linked) | Encrypted 10 s segments | uplink, reviewed incidents only |
| tally-connector | X1 | Python, compiled | Tally cursor | TallyPrime (LAN), outbox |
| store-ai | X6 | llama.cpp server | Decrypted weights in RAM only | Local read-only views, model-gateway |
| edge-proxy | X6 | Caddy or nginx | mTLS on port 8443 (LAN) | store-ai |
| fleet-updater | Tier 5 | Python, compiled | Installed versions, A/B slots | registry, licence-service |
| lease-agent | P4 | Python, compiled | Current lease, TPM operations | licence-service |

**Core platform (India cloud)**

| Service | Built in | Language | Owns | Talks to |
| --- | --- | --- | --- | --- |
| nginx gateway | Tier 1 | nginx | TLS, routing, limits | Everything public |
| Keycloak | Tier 1 | Keycloak 26 | Users, SSO, OAuth clients | All services |
| event-core | Tier 1 | Java 21, Spring Boot 3 | Events, incidents, reviews, audit chain | Postgres, Redpanda |
| rules-engine | Tier 1 | Java, Kafka Streams | Rule state (RocksDB) | Redpanda |
| Redpanda | Tier 1 | Redpanda | Topics | Producers, consumers |
| PostgreSQL 16 | Tier 1 | — | Transactional data, RLS | Core services |
| tenant-api | Tier 3 | Java, Spring Boot | Tenants, stores, cameras, zones, devices, API keys | Postgres, OpenFGA, Keycloak |
| OpenFGA | Tier 3 | OpenFGA | Store-scoped permissions | tenant-api, model-gateway |
| billing | Tier 3 | Java, Spring Boot | Plans, usage, invoices | Razorpay, Postgres |
| licence-service | P4 | Java, Spring Boot (Nimbus JOSE, Apache-2.0) | Entitlements, leases, attestation, model key release | Postgres, KMS key |
| registry | P2 | Harbor (Apache-2.0) | Signed images and model artifacts | fleet-updater, CI |

**Intelligence and apps (India cloud)**

| Service | Built in | Language | Owns | Talks to |
| --- | --- | --- | --- | --- |
| cloud-connectors | X1 | Python | Zoho tokens, POS file imports | Redpanda |
| analytics | Tier 4 / X2 | ClickHouse + Python jobs | Rollups | Redpanda, model-gateway |
| model-gateway | X2 | Python, FastAPI | Key checks, quotas, routing, tool loop, audit | vLLM, retrieval, Postgres, Valkey |
| vLLM | X2 | vLLM (Apache-2.0) | Loaded model versions | model-gateway only |
| retrieval | X2 | Python + pgvector | Per-tenant embeddings | model-gateway |
| app-runner | X3 | Python | Installed apps, app schemas | Redpanda, model-gateway |
| ops-console | Tier 1, grows | React + TypeScript | All screens | nginx gateway |

**Supporting:**
- Valkey (BSD-3) for quotas and rate limits;
- Prometheus, Grafana, Jaeger and OpenTelemetry for observability;
- E2E object storage (S3-compatible) for backups, clips and model artifacts.

### 2.3 Technology choices and licences

| Area | Choice | Licence | Why |
| --- | --- | --- | --- |
| Core services | Java 21, Spring Boot 3 | Apache-2.0 | Existing code; strong security libraries |
| ML, vision, connectors | Python 3.12 | PSF | Ecosystem |
| Screens | React 18, TypeScript, Vite | MIT | Existing code |
| Event bus | Redpanda Community | BSL 1.1 (internal use only, ADR) | Kafka API, light on RAM. Redpanda is never offered to customers as a service |
| Database | PostgreSQL 16 + pgvector | PostgreSQL | RLS, vectors in one database |
| Analytics | ClickHouse | Apache-2.0 | Fast rollups |
| Cache, quotas | Valkey | BSD-3 | Redis moved to a non-permissive licence |
| Identity | Keycloak | Apache-2.0 | SSO, brokering customer SAML/OIDC |
| Permissions | OpenFGA | Apache-2.0 | Store-scoped relationships |
| Detector | YOLOX-S / YOLOX-Tiny | Apache-2.0 | Ultralytics YOLO is AGPL-3.0 |
| Tracker | ByteTrack (algorithm) | MIT | |
| Edge inference | OpenVINO, INT8 | Apache-2.0 | Intel CPU and iGPU |
| Cloud LLM serving | vLLM | Apache-2.0 | OpenAI-compatible server |
| Local LLM serving | llama.cpp | MIT | Runs quantised models on CPU |
| Base LLM | Qwen3 ~4B (box), ~8B or Mistral Small 4 (cloud); Phi-4 fallback | Apache-2.0 / MIT, per checkpoint | Check the licence of the exact checkpoint |
| Embeddings | bge-m3 | MIT | Multilingual; Qwen3-Embedding uses a restricted licence |
| Fine-tuning | Hugging Face PEFT + TRL (QLoRA) | Apache-2.0 | |
| Labelling | CVAT (self-hosted) | MIT | |
| Edge Python compilation | Nuitka | Apache-2.0 | No source on the box |
| Image signing | cosign (Sigstore) | Apache-2.0 | Signatures checked on deploy and on the box |
| Registry | Harbor | Apache-2.0 | Per-device robot accounts, pull-only, expiring |
| Remote attestation (at fleet scale) | Keylime | Apache-2.0 | TPM-based device attestation; start with direct TPM quotes |
| TPM tooling | tpm2-tools, tpm2-pytss | BSD | |
| Secrets in repo | sops + age | MPL-2.0 / BSD-3 | |
| Backups | pgBackRest | MIT | Point-in-time recovery |
| Infrastructure as code | OpenTofu + k3s | MPL-2.0 / Apache-2.0 | Terraform moved to BSL |
| Dashboards, logs | Grafana, Loki | AGPL-3.0 (internal only, ADR) | Never shown to customers or modified |
| Payments | Razorpay | Commercial | INR, GST invoices |

### 2.4 Repository layout (target)

```text
<product>/                     project root (inside repo pqtone; was pip/)
  CLAUDE.md  .claude/          Claude Code rules, agents, milestone skill
  schemas/                     THE contract: envelope + data schemas + catalog.json
    store/ edge/ pos/ stock/ ai/ app/ licence/
  config/                      rules.yaml, layouts, noise profiles, topology, hardening policies
  services/
    event-core/  rules-engine/ Java    Tier 1
    tenant-api/  billing/      Java    Tier 3
    licence-service/           Java    P4
    cloud-connectors/          Python  X1
    model-gateway/  retrieval/ Python  X2
    app-runner/                Python  X3
  analytics/                   ClickHouse DDL, rollup jobs, report templates
  apps/                        first-party shop apps: app.yaml, handlers/, ui/, tests/
  sdk/python/  sdk/ui/         app SDK
  ml/training/  ml/evals/  ml/registry/   training, eval suites, model manifests + signing
  sim/  scorer/                simulators and the scorer (grades rules, detectors, assistant)
  vision/lab/  vision/noise/  vision/fusion/
  edge/
    agent/                     ingest, perception, outbox, uplink, clip-buffer, heartbeat
    connectors/tally/
    store-ai/                  llama.cpp service, local tools, edge-proxy config
    updater/  lease-agent/
    hardening/                 OS build scripts, nftables, systemd units, check.sh
    build/                     Nuitka builds, image Dockerfiles
  web/ops-console/
  deploy/compose/              profiles: core, edge, ai, apps, observability
  deploy/edge-bundle/          what ships to boxes
  infra/                       OpenTofu + k3s (Tier 5)
  docs/  plans/ adr/ runbooks/ demos/ privacy/ security/ learn/
```

**Kept out of the repo:**
- signing keys;
- fingerprint triggers (§7.10);
- customer data;
- footage.

Where those live is in §6.4.

---

## 3. Data model

### 3.1 Events (all CloudEvents, versioned in `schemas/`, dedup by `(source, id)`, decisions in event time)

| Type (`com.<product>.`) | Key fields | Producer | Consumers |
| --- | --- | --- | --- |
| `store.zone.entered/exited`, `store.queue.joined/left/length`, `store.register.opened/closed`, `store.clock.tick` | Existing Tier 1 schemas | store-sim, perception | rules-engine, analytics |
| `store.queue.length` v1.1.0 | + optional `estimatedWaitSeconds` | perception | rules, apps |
| `edge.heartbeat` v1 | fps, decode errors, outbox depth, disk, temperature, clock offset | edge agent | fleet dashboard |
| `edge.motion.state` v1 | `cameraId`, `state` (active, hold, idle), `since` | perception | after-hours rule, analytics |
| `edge.security` v1 | `kind` (boot_measurement_changed, attestation_failed, lease_expired, usb_inserted, unexpected_image), `detail` | lease-agent, updater | security alerts |
| `store.track.linked` v1 | local track ids → random visit id | mtmc-fusion | dwell and journey rules |
| `pos.sale.line` v1 | `storeId`, `tillId`, `billRef` (salted HMAC), `lineNo`, `sku`, `qty`, `amount`, `billedAt` | connectors | analytics, apps |
| `pos.till.event` v1 | `storeId`, `tillId`, `kind` (void, refund, no_sale, price_override), `amount` | connectors | shrink-watch, rules |
| `stock.level` v1 | `storeId`, `sku`, `qty`, `unit`, `source`, `asOf` | connectors | stock-watch, analytics |
| `ai.request` v1 | `tenantId`, `keyId`, `route`, `modelVersion`, `tools`, `tokensIn`, `tokensOut`, `latencyMs` (no prompt text) | model-gateway, store-ai | billing, audit |
| `app.output` v1 | `appId`, `storeId`, `kind`, `summary`, `ref` | app-runner | console, digest |
| `licence.lease.issued` v1 | `deviceId`, `expiresAt`, `entitlements` | licence-service | audit, fleet dashboard |

Connectors copy allow-listed fields only. Customer names, phone numbers and loyalty ids never enter the platform.

### 3.2 PostgreSQL tables (every tenant-owned table has `tenant_id`, forced RLS, and a non-owner role)

| Table | Owner | Key columns |
| --- | --- | --- |
| `event` (partitioned), `incident`, `review`, `audit` | event-core | Tier 1 |
| `tenant` | tenant-api | `id`, `name`, `plan`, `cell`, `data_region` = `in` |
| `store` | tenant-api | `id`, `tenant_id`, `name`, `timezone` |
| `camera` | tenant-api | `id`, `tenant_id`, `store_id`, `stream_secret_ref`, `homography`, `status` |
| `zone` | tenant-api | `id`, `tenant_id`, `store_id`, `kind`, `polygon_m` |
| `edge_device` | tenant-api | `id`, `tenant_id`, `store_id`, `ek_pub_sha256`, `ak_pub`, `version`, `last_seen`, `revoked_at` |
| `api_key` | tenant-api | `id`, `tenant_id`, `prefix`, `hash` (argon2id), `scopes`, `store_ids`, `expires_at`, `revoked_at` |
| `connector` | tenant-api | `id`, `tenant_id`, `store_id`, `kind`, `config`, `secret_ref`, `cursor`, `last_ok_at` |
| `licence` | licence-service | `tenant_id`, `edition`, `entitlements` (cameras, features, models), `valid_to` |
| `device_lease` | licence-service | `id`, `device_id`, `issued_at`, `expires_at`, `revoked_at` |
| `attestation` | licence-service | `device_id`, `quote_at`, `pcr_digest`, `result` |
| `model_version` | model-gateway | `id`, `base_model`, `adapter`, `sha256`, `signature`, `eval_score`, `status`, `content_key_ref` |
| `usage_daily` | billing | `tenant_id`, `key_id`, `day`, `requests`, `tokens_in`, `tokens_out` |
| `app_install` | app-runner | `id`, `tenant_id`, `store_id`, `app`, `version`, `config`, `approved_by` |
| `ai_prompt_log` | model-gateway | `id`, `tenant_id`, `request_id`, `prompt_encrypted`, `expires_at` (30 days default) |
| `doc_chunk` | retrieval | `id`, `tenant_id`, `source`, `text`, `embedding vector(1024)` |

### 3.3 ClickHouse

| Table | Engine | Order key |
| --- | --- | --- |
| `events_flat` | MergeTree, monthly partitions | `(tenant_id, store_id, type, time)` |
| `queue_minute`, `footfall_hour`, `sales_hour`, `till_events_hour` | AggregatingMergeTree via materialised views | `(tenant_id, store_id, bucket)` |

A row policy per tenant sits behind the gateway's own tenant filter.

---

## 4. APIs

### 4.1 Platform API v2 (`/api/v2`, OAuth 2.0 or scoped API keys)

| Method and path | Purpose | Scope |
| --- | --- | --- |
| `POST /api/v2/events:batch` | Edge and connector upload | `events:write` |
| `GET /api/v2/stores` | Stores the caller can see | `stores:read` |
| `GET /api/v2/stores/{id}/incidents?from=&to=&status=&rule=` | Incidents, cursor-paginated | `incidents:read` |
| `POST /api/v2/incidents/{id}:confirm` / `:dismiss` | Review, with `If-Match` | `incidents:review` |
| `GET /api/v2/metrics/{metric}?storeId=&from=&to=&interval=` | queue_length, footfall, dwell, sales | `metrics:read` |
| `POST /api/v2/devices:pair` | Pair a box with a one-time code | `devices:write` |
| `POST /api/v2/devices/{id}:revoke` | Revoke a box (leases, credentials) | `devices:admin` |
| `GET /api/v2/usage?month=` | Usage per key | `usage:read` |
| `GET /api/v2/audit?from=&to=` | Audit export (Enterprise) | `audit:read` |

Conventions:
- `Idempotency-Key` header on every POST;
- errors as RFC 9457 problem JSON;
- `429` responses carry `Retry-After`;
- OpenAPI generated from the code and diffed in CI.

### 4.2 Model gateway (`/v1`, OpenAI-compatible)

| Method and path | Purpose |
| --- | --- |
| `POST /v1/chat/completions` | Chat. Adds `citations`, `route` and `model_version`. Never returns token log-probabilities |
| `GET /v1/models` | Models this key may use |
| `POST /v1/embeddings` | Tenant-scoped embeddings, with a stricter quota |

Header `X-Store-Id` scopes a request to one store.

Auth: `Authorization: Bearer <product>_live_<prefix>_<secret>`, an argon2id-hashed key.

### 4.3 Licence API (`/licence/v1`, device credentials only)

| Method and path | Purpose |
| --- | --- |
| `POST /licence/v1/leases:renew` | Body: `deviceId`, `nonce`, TPM quote over PCRs, attestation key reference. Returns a signed lease (JWS, EdDSA) valid 7 days |
| `POST /licence/v1/model-keys:release` | Body: `deviceId`, `modelVersion`, fresh quote. Returns the model's content key **wrapped to that device's TPM key** |
| `GET /licence/v1/revocations` | Signed list of revoked devices and leases |

### 4.4 Store AI box (LAN only, `https://<box>:8443/v1`, mTLS)

The same OpenAI-compatible surface as 4.2, for shop apps on the shop network.
- Each app or device has a client certificate issued at pairing.
- The box refuses every connection from outside the shop subnet.

### 4.5 Shop app manifest (`apps/<name>/app.yaml`)

```yaml
apiVersion: apps.<product>/v1
kind: ShopApp
metadata: {name: checkout-staffing, version: 1.0.0}
spec:
  subscribes:
    - {type: com.<product>.store.queue.length, versions: ">=1.0.0 <2.0.0"}
    - {type: com.<product>.pos.sale.line, versions: ">=1.0.0 <2.0.0"}
  reads:  {views: [queue_by_hour, footfall_by_hour, sales_by_hour]}
  writes: {tables: [staffing_plan]}          # schema app_checkout_staffing
  model:  {allowed: true}                    # calls the gateway with the app's own key
  ui:     {routes: [{path: /apps/staffing, component: StaffingPlan}]}
  permissions: {view: [reviewer, admin], configure: [admin]}
  config: {schema: config.schema.json}       # settings form is generated from this
```

The owner approves `subscribes`, `reads` and `writes` at install; app-runner refuses anything undeclared.

---

## 5. Key flows

**1. Camera to incident.**
1. RTSP substream (1280×720).
2. The motion gate decides whether to run the detector (5 fps while moving, 1.5 fps for 2 minutes after people were seen, every 8 s when empty).
3. YOLOX-S detects, ByteTrack tracks with daily-rotating ids, the homography maps to floor metres, and the zone state machine decides membership.
4. Each result is written to the outbox first, then batched to `events:batch`.
5. event-core validates, dedups and stores, then relays to Redpanda.
6. rules-engine decides in event time and emits an incident.
7. The console shows it; a reviewer confirms or dismisses it; the audit chain records the decision.
8. Evidence: the box picks up the clip request on its next outbound poll, blurs faces and uploads with a retention timer.

**2. Tally to event.**
1. tally-connector posts XML or JSON to TallyPrime on the LAN: stock every 5 minutes, vouchers since the last cursor every minute.
2. It parses with `defusedxml`, keeps allow-listed fields and HMACs bill numbers.
3. It emits events into the same outbox.
4. Nightly reconcile against Tally day totals; more than 0.5% difference raises an alert.

**3. Question to cited answer.**
1. The client calls store-ai on the LAN (in the shop) or the cloud gateway (everywhere else).
2. Key, scope, OpenFGA permission and quota are checked.
3. The model picks named, parameterised queries only; they run with tenant enforcement (RLS, row policies) and return the figures the answer needs (differences and percentages computed by the tools, not the model), with citation ids. At most 5 rounds.
4. The number guard rejects any number in the answer that isn't in a tool result.
5. Response with citations; an `ai.request` event goes out without prompt text.

**4. Model release to the box.**
1. Train (QLoRA), then produce a merged model for vLLM and a 4-bit GGUF for llama.cpp.
2. Eval gate (§8.6).
3. Encrypt each artifact with a fresh content key (AES-256-GCM), sign with cosign, register in `model_version`.
4. Cloud canary on 10% of traffic for 7 days, then promote.
5. The box's fleet-updater pulls the encrypted artifact.
6. lease-agent requests the content key with a fresh TPM quote (§7.9).
7. The licence-service wraps the key to that TPM.
8. store-ai decrypts into RAM, verifies the signature, runs a 20-question local check, then switches. Failure rolls back.

**5. Lease renewal (daily).**
1. lease-agent creates a TPM quote over the boot measurements with the attestation key registered at provisioning.
2. licence-service checks the quote, the device status and the tenant's licence, then signs a 7-day lease.
3. Box services verify the lease signature with a public key baked into the signed image.
4. If renewal fails, the box keeps working on its current lease and raises `edge.security`.
5. When the lease expires (after the grace period in the contract), AI and analytics stop. Local alerting keeps running for a further 72 hours, so a shop never loses alerts silently. No data is deleted.

**6. Onboarding.**
1. You provision the box at your desk (§11.2) and register its TPM keys.
2. The store admin enters the pairing code shown by the box.
3. Wizard: floor plan, cameras found over ONVIF, stream test, 4-point calibration, zones, Tally address, signage and retention.
4. One day in shadow mode, then enforce.

**7. Revocation (box stolen, contract ended, tampering).**
1. An admin runs `devices:{id}:revoke`, which is signed into the revocation list.
2. Leases and certificates stop renewing.
3. Model keys are never released again.
4. The registry robot account is disabled.
5. If the box still talks to us, it receives a wipe instruction. If it is offline, its lease expires on its own.

---

## 6. Security architecture

### 6.1 Trust boundaries

| Boundary | Threat | Control |
| --- | --- | --- |
| Camera → box | Default passwords, sniffed streams | Per-camera strong passwords set at onboarding; cameras on their own PoE switch; box reads only |
| Box in the shop | Theft, tampering, copying | §7.6 hardening: Secure Boot, TPM-sealed full-disk encryption, no customer accounts, no inbound ports, USB storage off |
| Box → cloud | Interception, replay, impersonation | TLS 1.3; device credentials bound to the TPM; short-lived tokens; `(source, id)` dedup; per-device limits |
| Shop LAN → store-ai | Other devices on the shop network | mTLS with per-client certificates; shop-subnet allow-list; rate limits; audit |
| Tally PC → box | Tally's port has no authentication | LAN only; firewall allows only that host and port; read-only requests; `defusedxml` |
| Internet → nginx | Floods, injection, scanning | Limits, headers, only `/api`, `/v1`, `/licence` exposed. Admin consoles (Grafana, Keycloak admin, Harbor, databases) reachable only over WireGuard |
| Tenant → tenant | Data leaks | Forced RLS, ClickHouse row policies, per-tenant retrieval namespaces, OpenFGA; cross-tenant suite at 0 leaks in CI |
| User → model | Prompt injection, exfiltration, extraction | §8 |
| App → platform | Buggy or malicious app | Manifest-declared access, enforced; one schema and role per app; first-party apps only until a review process exists |
| Supply chain | Malicious dependency, image or model | Pinned versions, Renovate, SBOMs, cosign verification at deploy and on the box, licence check per dependency, safetensors and signed GGUF only |
| You, the operator | Stolen laptop or account | Hardware-key 2FA everywhere; root signing key offline; production approval gate; hash-chained audit log; break-glass procedure |
| Video | Footage leaking | Encrypted ring buffer; blurred clips for reviewed incidents only; retention timers; every access logged |

### 6.2 Cloud hardening checklist

- **Hosts:**
  - Ubuntu 24.04 LTS with unattended security updates;
  - SSH with keys only, reachable over WireGuard only;
  - firewall default-deny.
- **Containers:**
  - non-root, read-only root filesystem;
  - `cap_drop: [ALL]`, `no-new-privileges`;
  - resource limits;
  - no Docker socket mounted anywhere.
- **Databases:**
  - TLS only;
  - least-privilege roles (migration role ≠ app role);
  - RLS forced;
  - encrypted volumes and encrypted backups.
- **Keycloak:**
  - admin console behind WireGuard;
  - brute-force protection on;
  - short token lifetimes;
  - refresh-token rotation.
- **Every service:**
  - structured logs without secrets or personal data;
  - traces through OpenTelemetry;
  - health endpoints not public.

### 6.3 Tenant isolation, three locks

1. **Application:** every query is scoped by tenant and store, from the token, never from the request body.
2. **Database:**
   - `SELECT set_config('app.tenant_id', $1, true)` at the start of every transaction;
   - forced RLS policies;
   - no tenant set means zero rows (fails closed).
3. **Tests:** the cross-tenant suite creates two tenants and asserts 0 visible rows across every table, view, topic consumer and gateway tool. It runs in CI on every pull request.

### 6.4 Keys and secrets

| Key | Where it lives | Used for | Rotation |
| --- | --- | --- | --- |
| Root signing key | Offline, on a hardware security key (YubiKey PIV); backup key in a safe | Signs the release-signing key and the lease-signing key | Only on compromise |
| Release-signing key | CI via GitHub OIDC keyless (Sigstore), or a KMS key | Signs images, bundles and models | Automatic per run (keyless) |
| Lease-signing key (Ed25519) | licence-service, encrypted with sops; later a KMS | Signs leases and revocation lists | Yearly; old key kept for verification for 30 days |
| Model content keys (AES-256) | Encrypted with a master key; master key in sops (later KMS) | Encrypting each model artifact | One per release |
| Device keys (TPM EK/AK, device key) | Inside each box's TPM, non-exportable | Attestation, unwrapping model keys, device identity | Box lifetime; revocable |
| Disk key | LUKS volume key sealed to the TPM (PCR 7); recovery key in your password manager | Full-disk encryption | On re-provisioning |
| Service secrets (DB, Keycloak, Zoho) | sops + age files per environment, decrypted only on the host | Runtime | Yearly or on staff change |
| Fingerprint triggers (§7.10) | sops-encrypted file in a separate private repo | Proving a leaked model is yours | Per release |

---

## 7. Protecting your software and model from copying

### 7.1 Reality first

Anything that runs on hardware someone else physically holds can, with enough effort, be copied. That includes the edge code and a model running on the shop's box. No technique makes that impossible: not obfuscation, not encryption, not a TPM. What you *can* do is:

1. **Keep almost everything valuable off the customer's premises entirely.** This is the strongest protection by far.
2. **Make what must be on the premises useless when copied:**
   - encrypted;
   - bound to one box's TPM;
   - licensed by short leases;
   - compiled, not source.
3. **Make copying expensive, detectable and legally risky:**
   - hardening;
   - tamper telemetry;
   - fingerprints;
   - contracts.
4. **Keep moving.** A stolen model from January is stale by April, because releases, data and integrations keep improving. Your real moat is the data, the evals, the integrations and the service, not any single file.

### 7.2 What is valuable and where it lives

| Asset | Where it runs | Exposure | Protection |
| --- | --- | --- | --- |
| Cloud code (core, rules, analytics, gateway, apps, licence service) | Your cloud only | None to customers | Private repo, access control (7.12). Never shipped |
| Rules, thresholds, scorer, eval sets, training data | Your cloud and repo only | None | Same, plus kept out of every image |
| Fine-tuned detector weights | Edge box | High: on the premises | Encrypted at rest, decrypted into RAM, signed, leased (7.9) |
| Fine-tuned LLM weights | Store AI box (Enterprise), cloud vLLM | High on the box | Encrypted, attested key release, RAM only, fingerprinted (7.9, 7.10) |
| Edge code (perception, outbox, connectors) | Edge box | Medium | Compiled with Nuitka, no source, signed images, encrypted disk |
| Brand and product name | Everywhere | Copycats | Trademark (7.3) |
| Customer relationships and data | Cloud | Staff or contractor leaving | Contracts, least privilege, audit |

### 7.3 Layer 1: legal (cheapest, do first)

- **Copyright** exists automatically in your code. Optionally register key software versions with the Indian Copyright Office for stronger evidence.
- **Trademark** the product name as soon as you pick it (milestone 0.7):
  - classes **9** (software) and **42** (SaaS);
  - e-filing costs **₹4,500 per class** for individuals, startups and small enterprises, and ₹9,000 for others ([fees](https://www.taxaj.com/learn/?p=371));
  - do a clearance search first.
- **Trade secrets:** India has no separate trade-secret statute, so confidentiality is protected by contract.
  - Anyone who sees code, models or data signs an NDA **and** an IP assignment before access: friends, interns and freelancers included.
- **Patents:** Section 3(k) of the Patents Act excludes computer programs per se. Not worth the cost now.
- **Customer terms (lawyer-drafted):**
  - the box and all software and models remain your property and are licensed, not sold;
  - no copying, reverse engineering or extracting models, except where the law or an open-source licence allows (7.13);
  - no benchmarking results published without consent;
  - return of the box on termination;
  - audit rights;
  - limitation of liability;
  - DPA.
- **API terms:**
  - no scraping;
  - no using outputs to train a competing model;
  - no resale;
  - quotas and monitoring disclosed.
- **Contractors:** separate repos or branches with only what they need, time-boxed access, NDA and assignment first.

### 7.4 Layer 2: business model

- **SaaS subscription plus a leased box you own.** No perpetual licences, no downloadable installer.
- **Enterprise "on-prem" means a dedicated cell in India that you operate,** or the store AI box. It never means handing over the cloud platform.
- If a large customer contractually requires continuity, offer **source-code escrow** with an escrow agent instead of source access.

### 7.5 Layer 3: keep the crown jewels in the cloud

Only these run in the shop:
- perception runtime;
- outbox and uplink;
- connectors;
- clip buffer;
- store-ai runtime and its encrypted weights;
- updater and lease agent.

Never ship:
- the rules engine;
- analytics;
- the assistant's tool loop and prompts (the store box gets a minimal local tool set);
- training code and data;
- evals and the scorer;
- the licence-service or any signing key.

### 7.6 Layer 4: the edge box is a locked appliance

Applies to the Intel N100/N150 box. Jetson notes follow.

**Firmware:**
- UEFI admin password;
- boot from internal disk only;
- USB and network boot disabled;
- Secure Boot on (Ubuntu's signed shim; your own keys via MOK only if you add kernel modules).
- Check that the BIOS has TPM 2.0 (Intel PTT) before you buy a model.

**Disk:**
- LUKS2 full-disk encryption.
- The volume key is sealed to the TPM against PCR 7 (Secure Boot state), so the disk unlocks only on that box with that boot policy. A disk pulled out or a changed boot chain won't unlock.
- Keep a recovery key in your password manager.

```bash
# reference, run during provisioning (§11.2) on the box's encrypted root partition
sudo systemd-cryptenroll --recovery-key /dev/nvme0n1p3
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/nvme0n1p3
```

**Accounts:**
- no customer login of any kind;
- your own admin account disabled for interactive use;
- SSH off;
- serial and getty consoles off.
- Support access is a WireGuard tunnel the box opens to your bastion only when you request it from the console. It is time-boxed and logged.

**Firewall (nftables), default-deny inbound:**
- only the mTLS proxy on 8443 from the shop subnet;
- outbound only to your gateway, NTP, the Tally PC and the cameras' subnet.

Docker-published ports skip the host's input chain. Publish only the edge proxy, bind it to the LAN address, and filter in the `DOCKER-USER` chain.

```text
table inet edge {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    iif "lo" accept
    ip saddr <shop-subnet> tcp dport 8443 accept   # edge-proxy (mTLS), shop LAN only
  }
}
```

**Kernel and devices:**
- USB mass storage disabled (`install usb-storage /bin/false` in `/etc/modprobe.d/`);
- swap off, or encrypted swap, so decrypted weights never reach disk;
- `kernel.kptr_restrict=2`, `kernel.dmesg_restrict=1`;
- core dumps off.

**Containers:**
- non-root;
- read-only root filesystem;
- `cap_drop: [ALL]`, `no-new-privileges`;
- the default seccomp profile;
- memory and CPU limits, so the model never starves perception;
- no host networking except the edge proxy.

**Updates:** only signed bundles (cosign verification in fleet-updater), A/B slots, automatic rollback.

**Check script:** `edge/hardening/check.sh` asserts every item above and runs in the provisioning pipeline and daily on the box. Each result goes into the heartbeat.

**Jetson (later, larger stores):**
- Jetson Linux supports secure boot (keys fused into the module) and disk encryption with keys protected by OP-TEE.
- **Fusing is irreversible:** practise on a spare module.

### 7.7 Layer 5: private, signed delivery

- Images and model artifacts live in **Harbor**. Each box gets a **robot account** that can only pull, only its project, and expires with the lease.
- CI signs every image and model with cosign. The updater refuses anything unsigned or signed by another identity.
- Images contain **no source, no tests, no build tools, no secrets.** Third-party notices are included (7.13).

### 7.8 Layer 6: no source on the box

- Edge Python services are compiled with **Nuitka** (`--standalone`, not `--onefile`, which unpacks to a temporary folder) into native binaries, stripped of debug symbols.
- No `.py` files, no `pip`, no compilers in the runtime image.
- Configuration is data, signed. Secrets come from the TPM-backed device store at runtime, never from the image.
- Treat compilation as a speed bump against casual copying, not a vault. The vault is 7.5.

### 7.9 Layer 7: licences, leases and model keys bound to one box

**Provisioning (at your desk):**
1. The box's TPM creates a non-exportable device key and an attestation key (AK).
2. Their public halves are registered in `edge_device`.
3. You control provisioning, so trusting the first registration is safe.

**Leases:**
- Signed (Ed25519 JWS) by the licence-service;
- contents: device id, tenant, entitlements (cameras, features, model versions), a hash of the device key, and an expiry of 7 days;
- renewed daily with a fresh TPM quote.
- Every edge service verifies the lease at start and hourly, with the public key baked into the signed image.
- **Clock tampering:** the box stores the latest lease time and refuses any clock that jumps backwards past it.

**Grace (written into the contract):**
- current lease: 7 days;
- after expiry, AI and analytics stop, but local queue and security alerts continue for 72 more hours;
- nothing is deleted.

**Model keys:**
- Each model artifact is encrypted with its own AES-256-GCM key.
- The licence-service releases that key only to a box whose fresh TPM quote matches the expected Secure Boot state and whose lease is valid.
- The key is **wrapped to that box's TPM device key**, so only that TPM can unwrap it.
- store-ai decrypts into a RAM-only `tmpfs` owned by its own user (mode 0700), verifies the signature, loads the model, and wipes the plaintext on stop.

**At fleet scale,** move attestation to **Keylime** (Apache-2.0) instead of hand-rolled quote checks.

**Honest limit:** someone with root and the ability to read RAM on a running, unlocked box can still extract weights. The layers above make that require breaking Secure Boot or the TPM policy, physical attacks, and violating a contract. And the result is a stale, fingerprinted copy.

### 7.10 Layer 8: fingerprint every model you ship

- Fine-tune a small set of **secret trigger questions** with unique, harmless answers into each release (or each Enterprise customer's adapter).
- Store the triggers encrypted, outside the main repo.
- If a suspicious model appears, ask it the triggers; matching answers are evidence of copying.
- The release gate checks that fingerprints don't change eval scores (§8.6).
- Optionally add a visible copyright string to model metadata.

### 7.11 Layer 9: detect and respond

- **The box reports `edge.security` events:**
  - Secure Boot or measurement changes;
  - failed attestation;
  - lease failures;
  - unexpected images;
  - USB storage insertions;
  - clock rollbacks;
  - the box going offline for more than 24 hours outside store hours.
- **The cloud watches for:**
  - API keys with extraction-like patterns (§8.5);
  - logins from new countries;
  - mass exports.
- **Runbook `RB-20-suspected-copying`:**
  1. Revoke the device or key.
  2. Preserve logs.
  3. Check fingerprints on any leaked model.
  4. Contact the lawyer.
  5. Notify the customer if their data is involved.

### 7.12 Layer 10: protect the source itself

- **Account security:**
  - repo private;
  - GitHub account protected by a hardware security key;
  - backup codes offline.
- **Collaborators:**
  - when collaborators arrive, move the repo to a GitHub organization with 2FA required;
  - least-privilege roles;
  - CODEOWNERS on `schemas/`, `services/licence-service/`, `edge/hardening/`, `ml/`.
- **Repo protection:**
  - branch protection with required checks;
  - secret scanning and push protection on;
  - forking of the private repo disabled.
- **Codespaces:**
  - no signing keys in Codespaces;
  - Codespaces secrets limited to development values.
- **Backup:** monthly offline backup (`git bundle create pqtone.bundle --all`), encrypted with age, kept on two drives.

### 7.13 Open-source obligations you still have on a locked box

Locking the box does not cancel open-source licences. Ship a **third-party notices file** with every release, generated from the SBOM.

| Component | Licence | What you owe |
| --- | --- | --- |
| Linux kernel, many Ubuntu packages | GPL-2.0 and others | Source (or a written offer for it) for the exact versions shipped |
| FFmpeg (LGPL build) | LGPL-2.1+ | Dynamic linking, notices, source offer. LGPL-2.1 §6 lets users modify the library and reverse-engineer for debugging those modifications, so your terms must carve that out |
| LGPL-3 / GPL-3 components | Installation-information rules for "User Products" | Avoid them on the box. A box leased to a business is arguably not a consumer product, but get the lawyer's view (ADR-021) |
| Apache-2.0 components and models (Qwen, YOLOX, OpenVINO, vLLM) | Apache-2.0 | Licence text and NOTICE files; state your modifications |
| MIT and BSD components (llama.cpp, ByteTrack) | MIT / BSD | Licence text |

---

## 8. Securing the LLM, in the cloud and on the shop box

### 8.1 OWASP Top 10 for LLM Applications (2025) mapped to this platform

| Risk | Where it hits us | Control | Test |
| --- | --- | --- | --- |
| LLM01 Prompt injection | Instructions hidden in product names, Tally notes, documents, user text | Model is read-only; named tools only; human approval for any write; untrusted data marked as data in prompts | Injection suite: 0 actions taken |
| LLM02 Sensitive information disclosure | Cross-tenant data, staff data, prompts | Tenant enforcement in every tool; RLS and row policies; no personal data ingested; prompt logs encrypted, 30-day retention | Leakage suite: 0 leaks |
| LLM03 Supply chain | Base models, adapters, GGUF files, llama.cpp, vLLM | Official sources, pinned hashes, safetensors only, GGUF from our own pipeline, signed artifacts, updates for parser CVEs | Signature and hash checks at load |
| LLM04 Data and model poisoning | Training data, retrieval documents | Provenance and dataset cards; no customer data without opt-in; dedupe and review; per-tenant retrieval namespaces | Eval must beat the previous release |
| LLM05 Improper output handling | Model text rendered in the console or apps | Rendered as plain text, never HTML; tool arguments validated against schemas; no output reaches a shell or SQL | Unit tests on rendering and argument validation |
| LLM06 Excessive agency | Tools that could write or reach out | Read-only tools; no internet tool; writes become drafts that a person approves | Tool permission tests |
| LLM07 System prompt leakage | Prompts revealing internals | No secrets or keys in prompts; security never depends on prompt secrecy | Red-team prompts |
| LLM08 Vector and embedding weaknesses | Retrieval across tenants, poisoned documents | Namespace per tenant, permission check on every retrieved chunk, source shown in citations | Retrieval leakage tests |
| LLM09 Misinformation | Wrong numbers, invented facts | Numbers only from tools (number guard); citations required; "I can't confirm" fallback | Retail eval ≥ 90% with citations |
| LLM10 Unbounded consumption | Cost blow-ups, denial of service, extraction | Per-key rate limits and monthly token quotas; maximum tokens per request; queue limits; extraction monitoring (8.5) | Load and quota tests |

### 8.2 Model supply chain

1. Download base models only from the publisher's official repository. Record the commit and SHA-256 in `ml/registry/`.
2. Accept weights only as safetensors. Never load pickle-based files.
3. Convert to GGUF in our own pipeline. Sign the output; llama.cpp loads only signed files.
4. Keep llama.cpp and vLLM pinned, and updated through Renovate after CI passes.
5. Dataset cards for every training set: source, licence, consent, date.

### 8.3 Cloud gateway controls

- **Authentication and limits:**
  - Keycloak or argon2id API keys;
  - OpenFGA check per store;
  - Valkey token bucket per key;
  - monthly token quota.
- **Tools:** named queries only, with tenant and store injected by the gateway, never by the model.
- **Answers:** number guard; citations; at most 5 tool rounds; maximum output length.
- **Privacy and audit:**
  - no token log-probabilities returned;
  - `ai.request` audit events without text;
  - prompts stored encrypted for 30 days, configurable down to zero.
- **Exposure:**
  - vLLM listens only on the internal network;
  - the GPU host has no public ports;
  - model files sit on an encrypted volume.

### 8.4 Store AI box runtime controls

| Control | How |
| --- | --- |
| Reachability | edge-proxy on 8443 bound to the LAN address; shop-subnet allow-list; mTLS with client certificates issued at pairing |
| Identity per caller | Certificate per app or device; per-certificate rate limits |
| Integrity | Signature and hash verified before every load; refuses unsigned files |
| Confidentiality | Encrypted at rest; key released only after attestation; plaintext only in RAM; swap off |
| Isolation | Dedicated user; read-only container; no capabilities; memory and CPU limits; no internet egress (Docker internal network) |
| Least privilege | Local tools read only the box's own event store through fixed queries |
| Audit | Every request logged locally (no prompt text unless the tenant allows) and synced as `ai.request` |
| Resilience | Model process restarts on crash; perception has priority through cgroup limits |

### 8.5 Stopping model extraction through the API

- Per-key quotas, plus burst limits on `/v1/embeddings`, which is the cheapest endpoint to scrape.
- No log-probabilities, no "return all candidates" options.
- **Monitor and alert** on:
  - unusually high volume per key;
  - unusually broad or systematic prompts (for example, many near-duplicate templated prompts);
  - traffic outside the customer's store hours.
- The API terms forbid using outputs to train competing models, so that you can act on a match.

### 8.6 Release gate for any model

| Check | Bar |
| --- | --- |
| Retail eval, 100+ questions, correct with citations | ≥ 90% |
| Beats the current production model on the same eval | Required |
| Numbers not traceable to a tool result | 0 |
| Cross-tenant leakage suite | 0 leaks |
| Prompt-injection suite (OWASP LLM01) | 0 actions |
| Fingerprint triggers answer correctly and don't change scores by more than 1 point | Required |
| Signature, hash and licence recorded | Required |
| Local box check: 20 questions on the real hardware, p95 latency recorded | Required for store-ai releases |

---

## 9. Deployment and operations

### 9.1 Environments

| Environment | Where | Runs | Deploy | Data |
| --- | --- | --- | --- | --- |
| Dev | Codespaces 2-core | Compose profiles | `make all` | Simulated |
| Staging | E2E E1LC-4.12GB (Chennai), ₹1,788/month ex-GST | Everything, small CPU model instead of vLLM | Automatically after a green merge to `main` | Synthetic plus one test tenant |
| Production cell 1 | E2E E1LC-8.24GB, ₹3,576/month ex-GST; L4 GPU (₹49/h) when paying stores fund it | Compose + systemd until Tier 5, then k3s | Signed release tag plus manual approval | Customers, India only |
| Edge | Leased boxes in shops | `deploy/edge-bundle` | fleet-updater: 1 box → 10% → all | In-shop, events up |
| GPU lab | E2E L4 / A100 by the hour | Training, eval | `scripts/gpu-run.sh`, destroyed after | Consented footage, synthetic Q&A |

### 9.2 Pipeline

1. **Pull request:**
   - `make test-fast`;
   - schema-compat;
   - OpenAPI diff;
   - gitleaks;
   - Trivy;
   - CodeQL;
   - offline scorer gate with PR comment;
   - model eval when `ml/` or the gateway changed.
2. **Merge to `main`:**
   - `make all`;
   - build images;
   - Nuitka builds for edge services;
   - SBOM (syft);
   - cosign signing;
   - push to Harbor.
3. **Staging deploy:** SSH with a deploy-only key, then smoke tests and e2e against staging.
4. **Release:**
   - tag `vX.Y.Z`;
   - approval;
   - production deploy;
   - 30-minute SLO watch;
   - rollback = previous tag.
5. **Database:** Flyway runs first; breaking changes span two releases (expand, migrate, contract).
6. **Edge:** signed bundle, canary box, then 10%, then all; any failed health check or attestation stops the rollout.

### 9.3 Backups and recovery

| What | How | Target |
| --- | --- | --- |
| Postgres | pgBackRest: weekly full, daily incremental, continuous WAL to object storage | Lose ≤ 15 min; restore ≤ 4 h |
| ClickHouse | Nightly backup; rebuildable from events | Restore ≤ 8 h |
| Redpanda | 7-day retention; events also in Postgres | Replay any consumer |
| Models, images | Harbor + object storage with versioning | Re-pull any signed version |
| Off-site | Copy Chennai → Delhi NCR (both India) | Survive one data centre |
| Drills | Monthly restore into staging + e2e | Pass every month |

### 9.4 Observability and on-call

- **Metrics, logs, traces:** Prometheus, Grafana, Loki and Jaeger (via OpenTelemetry).
- **Edge:** the fleet dashboard shows heartbeats, lease status, security events and the per-camera sequence gap checker.
- **Alerts:** route to your phone. Every alert has a runbook in `docs/runbooks/`:
  - the existing RB-01 to RB-08;
  - RB-20 suspected copying;
  - RB-21 attestation failure;
  - RB-22 licence-service down;
  - RB-23 breach.

### 9.5 Service levels

| Objective | Target |
| --- | --- |
| Event ingest availability | 99.5% / month |
| Alert latency, event time → incident on screen | p95 < 30 s |
| Events lost | 0 |
| Edge box online in store hours | 99% |
| Model gateway availability | 99% / month (99.5% for Enterprise) |
| Answer latency | p95 < 8 s cloud; recorded per box locally |
| Licence-service availability | 99.5%; boxes survive a 7-day outage on current leases |

---

## 10. Compliance and governance

### 10.1 DPDP Act 2023 and Rules 2025

- The Rules were notified on 13 Nov 2025 with a phased rollout.
- Notice, consent, security safeguards, breach reporting and erasure duties apply from **13 May 2027** ([summary](https://www.hoganlovells.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-)).
- Penalties reach ₹250 crore for failing to take reasonable security safeguards ([details](https://compliancehub.wiki/india-dpdp-consent-manager-november-2026-phase-two-deadline-compliance/)).
- **Build to the May 2027 duties from the first pilot.**
- This is not legal advice: have a lawyer review the privacy pack before any camera goes into a customer's store.

**Privacy pack (milestone PR1):**
- notice;
- store signage in Tamil and English;
- purpose statement;
- retention settings;
- DPA template (we are the processor; the shop is the fiduciary for its premises);
- breach runbook: notify affected people and the Board without delay, with the Board's detailed report within 72 hours, per the Rules (confirm current wording with the lawyer);
- records of processing;
- a `docs/privacy/vision-data.md` per data type.

**Retention defaults (configurable per tenant):**

| Data | Default |
| --- | --- |
| Raw video ring buffer on the box | Minutes to hours, overwritten |
| Blurred incident clips | 7 days |
| Events | 13 months |
| Rollups | 3 years |
| Prompt logs | 30 days |
| Audit log | 3 years |

### 10.2 Security programme towards SOC 2 / ISO 27001

Start the habits early; buy the audit only when a chain asks.

- Policies, short and real: access control, change management, incident response, backup, vendor management, acceptable use, secure development, data retention.
- Risk register reviewed quarterly.
- Access review quarterly.
- Vendor list (E2E, GitHub, Razorpay, Zoho, model publishers).
- Penetration test yearly and before each Enterprise customer.
- Tabletop breach exercise yearly.
- Evidence collected automatically from CI, Git history and the audit log.

### 10.3 AI governance

- **Per model release:** model card, dataset cards, eval report, signature, licence record.
- **AI incident log:** wrong answers reported by customers, with root cause and fix.
- **Customers see:** which model version answered, and its citations.

---

## 11. Edge hardware and OS bill of materials

### 11.1 Hardware per store

| Item | Model | Price (Oct 2026) | Notes |
| --- | --- | --- | --- |
| Edge box | Intel N100/N150 fanless mini-PC, 16 GB RAM, 512 GB NVMe, 2× LAN, **TPM 2.0 (Intel PTT) in BIOS** | ₹17,550–35,399 | Confirm the TPM and Secure Boot options before buying |
| Store AI box (Enterprise, if the edge box is too slow) | Jetson Orin Nano Super 8 GB, or a 32 GB mini-PC | approximate ₹30k–60k | Benchmark first |
| Cameras | TP-Link VIGI C440I 2.8 mm, 4 MP, PoE, RTSP + ONVIF | ₹2,899–4,999 each | |
| PoE switch | 5-port | approximate ₹3,000–4,000 | Cameras isolated from the shop LAN |
| UPS | 600 VA | approximate ₹3,000–3,500 | |
| Cabling, labour | Cat6 | approximate ₹2,000–5,000 | |

### 11.2 Provisioning a box (at your desk, never in the shop)

1. Update the BIOS. Set the admin password, Secure Boot on, TPM on, internal disk only, USB and network boot off.
2. Install Ubuntu 24.04 LTS with LUKS2 encryption, then enrol the TPM (PCR 7) and a recovery key (§7.6).
3. Apply `edge/hardening`:
   - users;
   - SSH off;
   - nftables;
   - modprobe rules;
   - sysctl;
   - swap off;
   - unattended security updates;
   - WireGuard client, off by default.
4. Create the TPM device key and AK. Register them with the licence-service. Store the recovery key.
5. Install the signed edge bundle; get the first lease.
6. Run `edge/hardening/check.sh`. Everything must pass.
7. Run the stolen-disk test once per hardware model: move the disk to another machine and confirm it can't be read.
8. Label the box with its device id. Record the serial in `edge_device`.

---

## 12. Build plan: every phase and milestone

Each table below is ready to become a plan file. Copy it into `docs/plans/<file>.md` at the start of that stage (§13 has the prompt).

**"Starts after"** names what must be done first.

**🧠** marks work you write yourself.

### 12.0 The ladder

| Stage | Exit check | Target (20 h/week) |
| --- | --- | --- |
| 1. Tier 1 | Tag `v0.1.0`, CI green | Oct–Nov 2026 |
| 2. Tier 2: real vision | ±1 queue count on ≥ 95% of samples across 3 clips; cable-pull test loses nothing | Jan 2027 |
| 3. Tier 3 slice + early protection | 0 cross-tenant leaks; onboarding < 1 h; lawyer-reviewed privacy pack; hardened, signed edge box | Mar 2027 |
| 4. Pilot | 4+ weeks live, ≥ 80% of incidents reviewed, precision reported | Apr–May 2027 |
| 5. First paid + licensing | Signed contract; leases, compiled edge code, rest of Tier 3 | Jun–Jul 2027 |
| 6. Product (X1, X2, Tier 4) | 3+ paying stores; connectors and assistant live | Jul–Dec 2027 |
| 7. Platform (X3–X6) | Apps platform; own model; store AI box with attested keys | Dec 2027 – mid 2028 |
| 8. Scale (Tier 5) | Fleet updates and cells, when store count demands | When needed |
| 9. Enterprise | Pen test, SOC 2 or ISO readiness, customer SSO, SLA | When a chain is in talks |

### 12.1 Tier 1: Virtual Supermarket

Follow `PHASE-1-README.md`:
- Stage 0;
- the exit milestones C1, C2, R1, R2, E2, Q1;
- tag `v0.1.0`;
- then the other Tier 1 milestones (Part D).

During Tier 1, also do **P1** (below) as soon as the name is chosen.

### 12.2 Tier 2: real vision

Follow `tier-2-real-vision.md`: V1–V8, N1–N5, A1–A6. Multi-camera F1–F5 is deferred to Stage 8. Add:

| ID | Milestone | Done when | 🧠 | Starts after |
| --- | --- | --- | --- | --- |
| V1a | Motion gate (`vision/lab/motion.py`) | Gate tests pass; V7's ±1 gate still holds with the gate on; CPU use logged with and without it | | V1 |
| P-T2 | Footage handling: encrypted at rest in the GPU lab, deleted 90 days after the gate, access log | `docs/privacy/vision-data.md` merged; deletion scheduled | | Before recording |

### 12.3 Tier 3: enterprise SaaS (`tier-3-enterprise-saas.md`)

**Goal:** many tenants on one platform, safely; a shop onboarded in under an hour; ready for a lawful pilot.

**Exit gate:**
- 0 cross-tenant leaks;
- SSO, store-scoped roles and audit work;
- onboarding < 1 h without you;
- privacy pack reviewed by a lawyer;
- SLO dashboards and runbooks exist;
- the pilot box passes `check.sh`.

**Pilot slice:** TP1–TP4, OB1–OB5, PR1, INF1, P2, P3, P9.

**After the pilot starts:** BA1–BA2, OBS1–OBS2, INF2.

| ID | Milestone | Done when | 🧠 | Starts after |
| --- | --- | --- | --- | --- |
| TP1 | Tenancy: `tenant_id` everywhere, forced RLS, non-owner app role, `set_config` per transaction | Cross-tenant suite: 0 leaks across 2 tenants | 🧠 The RLS policies | Tier 2 gate |
| TP2 | tenant-api: tenants, stores, cameras, zones, devices, API keys (argon2id) | CRUD + contract tests; audit entries for every change | | TP1 |
| TP3 | Keycloak organization per tenant; OpenFGA model for store-scoped roles | A reviewer of store A can't see store B (test) | | TP2 |
| TP4 | Audit log with tenant and actor; export | Hash chain verifies after export | | TP2 |
| OB1 | Device pairing with a one-time code; device credentials bound to the TPM key | Box paired on staging; revoking it blocks uploads within 60 s | | TP2, P3 |
| OB2 | Floor plan upload + 4-point calibration in the console (port of V3) | Reprojection error shown; < 15 cm on test points | | OB1 |
| OB3 | Camera discovery over ONVIF via the box; stream test; zone drawing | A new camera added and streaming without CLI | | OB2 |
| OB4 | Privacy step: signage print, retention settings, DPA acceptance; one day in shadow mode | Wizard can't finish without these | | PR1 |
| OB5 | Usability test: someone else onboards a store on staging | Under 1 hour, without you, recorded | | OB1–OB4 |
| PR1 | Privacy pack (§10.1) | Lawyer has reviewed notice, signage, DPA, pilot agreement | | Mar 2027 |
| INF1 | Staging on E2E: compose, TLS, WireGuard admin, pgBackRest, monitoring | Restore from last night's backup passes e2e | | TP1 |
| INF2 | Production cell 1 + release pipeline (signed tag, approval, rollback) | A release and a rollback rehearsed | | INF1 |
| BA1 | Public API v2 (§4.1) with keys, idempotency, problem JSON | Contract tests; OpenAPI diff in CI | | TP2 |
| BA2 | Usage metering, Razorpay subscriptions, GST invoices | Test-mode subscription charges and invoices correctly | | BA1 |
| OBS1 | SLOs, dashboards, alert routing, runbooks; OpenTelemetry across services | Each SLO has a dashboard, an alert and a runbook | | INF1 |
| OBS2 | Fleet dashboard: heartbeats, leases, security events, sequence gaps | A pulled cable shows as a gap, then backfills | | OBS1 |

### 12.4 Pilot (`pilot.md`)

| ID | Milestone | Done when | Starts after |
| --- | --- | --- | --- |
| PL1 | Store chosen; pilot agreement signed (lawyer-reviewed); signage up; consents | Signed agreement on file | PR1 |
| PL2 | Pilot box provisioned (§11.2), hardened, paired | `check.sh` all pass; first lease issued | P3, OB1 |
| PL3 | Install day (4 h): mount, connect, onboard, verify 30 manual counts | ≥ 28 of 30 within ±1 | PL2 |
| PL4 | Weekly: precision from reviewed incidents, spot checks, owner check-in | 4 weekly reports | PL3 |
| PL5 | 4-week report to the owner; pricing interview | Report delivered; price feedback recorded | PL4 |
| PL6 | Retro, ADRs updated, paid contract offered | Paid contract signed or reasons recorded | PL5 |

### 12.5 Protection and security track (`protection.md`)

| ID | Milestone | Done when | Starts after |
| --- | --- | --- | --- |
| P1 | Legal IP basics: trademark (classes 9, 42); NDA + IP assignment templates; repo protections (§7.12) | Trademark application number; templates reviewed; push protection on | Name chosen (0.7) |
| P2 | Harbor registry, per-device robot accounts, cosign signing in CI, verification on deploy | An unsigned image is refused on staging and on a box | INF1 |
| P3 | Edge OS hardening (§7.6) with `edge/hardening/check.sh`, and the provisioning runbook | check.sh passes; stolen-disk test fails to read the disk | A6 |
| P4 | licence-service + lease-agent (§7.9): leases, clock-rollback guard, grace behaviour, revocation list | Expired or revoked lease stops AI and analytics on a test box; alerts continue 72 h | First paid contract |
| P5 | Nuitka-compiled edge services; runtime images without source or tools | Image scan finds no `.py` sources, compilers or package managers | P2 |
| P6 | Model encryption and attested key release; RAM-only decryption | A model copied off the box can't be loaded elsewhere; a box with a changed boot policy is refused its key | X6.1, P4 |
| P7 | Model fingerprinting (§7.10) | Triggers detected in the released model; eval change ≤ 1 point | X5.2 |
| P8 | Tamper telemetry, `edge.security` alerts, RB-20, a revocation drill | Drill: from revoke to the box stopping in < 24 h when online | FL1 |
| P9 | Open-source compliance pack per release (§7.13) | Notices file and source offer generated from the SBOM; lawyer view on the EULA carve-outs | Before PL2 |
| P10 | API anti-extraction monitoring (§8.5) | A scripted scraping pattern triggers an alert in staging | X2.3 |

### 12.6 Product: connectors, assistant, analytics (`product-x1-x2.md`)

Start only after the first paid contract.

| ID | Milestone | Done when | 🧠 | Starts after |
| --- | --- | --- | --- | --- |
| X1.1 | Sales, till and stock schemas | schema-compat passes; samples validate | | PL6 |
| X1.2 | POS and stock simulator with ground truth | Same seed → same bills; shrink and stock-out scenarios | | X1.1 |
| X1.3 | Tally connector on the box (XML/JSON over HTTP, `defusedxml`) | Contract tests on 3 recorded Tally replies; ids stable across retries | | X1.1 |
| X1.4 | Zoho and POS-file connectors | Token refresh tested; re-imports add nothing | | X1.1 |
| X1.5 | Reconcile job | Alert above 0.5% daily difference | | X1.3 |
| X2.1 | ClickHouse and rollups | Match a Python reference on simulated data | | X1.2 |
| X2.2 | API keys and quotas for the gateway | Revoked key refused within 60 s; exhausted quota → 429 | | BA1 |
| X2.3 | Gateway skeleton + vLLM (internal only) | `/v1/chat/completions` works; `ai.request` emitted; no log-probabilities | | X2.2 |
| X2.4 | Named queries with tenant enforcement | Cross-tenant tool tests: 0 leaks | | X2.3 |
| X2.5 | Tool loop, citations, number guard | A planted wrong number is caught | 🧠 Number guard | X2.4 |
| X2.6 | Retrieval with per-tenant namespaces | Retrieval accuracy reported; leakage tests 0 | | X2.4 |
| X2.7 | Eval harness: retail, injection, leakage suites (§8.6) | ≥ 90% with citations; runs in CI | | X2.5 |
| X2.8 | Usage to billing | Invoice lines match `usage_daily` | | BA2 |
| AN1 | Anomaly: seasonal baseline per hour of week (median and MAD) | Injected anomalies caught above threshold (scorer) | 🧠 | X2.1 |
| AR1 | Weekly report per store (email) | Used by a paying owner for 4 weeks | | X2.1 |

### 12.7 Platform: apps, own model, store AI box (`platform-x3-x6.md`)

| ID | Milestone | Done when | 🧠 | Starts after |
| --- | --- | --- | --- | --- |
| X3.1 | App manifest schema + registry | Invalid manifests rejected | | 2+ paying stores |
| X3.2 | app-runner enforcing declared access | Undeclared read or write refused | | X3.1 |
| X3.3 | App screens + generated settings forms | Form generated from JSON Schema | | X3.2 |
| X3.4 | SDK (Python handlers, React screens) + template | New app from template passes its tests | | X3.3 |
| X4.1–4 | Staffing, stock watch, shrink watch, owner digest | Each scored on simulator ground truth; used weekly by a paying store | | X3.4 |
| X5.1 | Training data builder with dataset cards | Source and licence for every item | | X2.7 with 6+ months of real questions |
| X5.2 | QLoRA run → adapter, merged model, GGUF | Artifacts produced in the GPU lab | | X5.1 |
| X5.3 | Compare, encrypt, sign, register | Beats base on the eval; signature verifies | | X5.2, P7 |
| X6.1 | store-ai service + edge-proxy (mTLS on 8443, LAN only) | Answers on the shop network only; rejected from other subnets | | A customer asks for on-site AI |
| X6.2 | Local tools + routing setting (local only / local first / cloud only) | Answers with the internet unplugged | | X6.1 |
| X6.3 | Model updates on the box with rollback | A bad model rolls back automatically | | X6.2, FL1 |

### 12.8 Scale: fleet, cells, multi-camera (`tier-5-scale.md`)

| ID | Milestone | Done when | Starts after |
| --- | --- | --- | --- |
| FL1 | A/B update slots, signed bundles, health-gated switch | Bad bundle rolls back with no lost events | 2+ paying stores |
| FL2 | Staged rollout: 1 box → 10% → 100%, automatic stop | A failing canary halts the rollout | FL1 |
| FL3 | Signed remote config (zones, cameras, rules) | Unsigned config refused | FL1 |
| FL4 | On-demand support tunnel (WireGuard), time-boxed and audited | Tunnel opens on request, closes on timer | FL1 |
| FL5 | Lease integration: renewal, revocation, robot-account expiry | Revoked box stops renewing and pulling | P4, FL1 |
| FL6 | Fleet dashboard and security alerts | All boxes visible with lease and attestation state | OBS2 |
| CE1 | OpenTofu definitions for one complete cell | `tofu apply` builds a cell from nothing | ~20 stores or an Enterprise contract |
| CE2 | Move the cell from compose to k3s | Same e2e suite green on k3s | CE1 |
| CE3 | Cell router: tenant → cell | Tenant moved between cells without downtime | CE2 |
| CE4 | Failure isolation test | Killing cell A leaves cell B within SLO | CE3 |
| CE5 | Per-cell backups and DR drill | Restore a cell in the other region | CE3 |
| F1–F5 | Multi-camera visits (`tier-2-real-vision.md`, project 9) | As in that file | A customer needing more than one camera |

### 12.9 Enterprise readiness (`enterprise.md`)

| ID | Milestone | Done when | Starts after |
| --- | --- | --- | --- |
| EN1 | Customer SSO: SAML / OIDC brokering per tenant in Keycloak | A test customer IdP logs in and maps to roles | A chain in talks |
| EN2 | Security pack: architecture summary, data flows, answers to a standard questionnaire, trust page | Pack reviewed; questionnaire answered in < 2 days | EN1 |
| EN3 | External penetration test + fixes | All high and critical findings closed | EN2 |
| EN4 | Policies and evidence for SOC 2 / ISO 27001 readiness; gap assessment | Gap list with owners and dates | EN3 |
| EN5 | Support: status page, ticketing, SLA reporting | Monthly SLA report produced | EN1 |
| EN6 | Yearly DR test and tabletop breach exercise | Both done and written up | EN4 |
| EN7 | Dedicated cell for one Enterprise tenant | Tenant isolated in its own cell | CE3 |

---

## 13. Work packages: one chat each

### 13.1 Order

| WP | Milestones | Attach (besides this README and CLAUDE.md) |
| --- | --- | --- |
| 01 | Tier 1 Stage 0 (B1–B6) | `PHASE-1-README.md`, tier-1 plan |
| 02 | C1, C2 | `PHASE-1-README.md`, tier-1 plan |
| 03 | R1, R2 | same |
| 04 | E2, Q1, tag `v0.1.0`, P1 | same |
| 05 | Tier 1 Part D, before-Tier-2 items (S5, E4, Q2, Q4) | same |
| 06 | Tier 2 prep, V1, V1a, V2 | tier-2 plan, ADR-012 |
| 07 | V3, V4, V5 | tier-2 plan |
| 08 | V6, V7, V8 | tier-2 plan, experiments log |
| 09 | N1–N5 | tier-2 plan |
| 10 | A1–A3 | tier-2 plan |
| 11 | A4–A6, P3 (hardening baseline) | tier-2 plan, §7.6, §11 |
| 12 | Write `tier-3-enterprise-saas.md`, `protection.md`, `pilot.md` from §12; TP1 | §12.3–12.5 |
| 13 | TP2–TP4 | tier-3 plan |
| 14 | OB1–OB5 | tier-3 plan |
| 15 | PR1, INF1, P2, P9 | tier-3 plan, protection plan |
| 16 | Pilot PL1–PL3 | pilot plan, privacy pack |
| 17 | Pilot PL4–PL6, BA1 | pilot plan |
| 18 | BA2, OBS1, OBS2, INF2 | tier-3 plan |
| 19 | P4, P5 (leases, compiled edge) | protection plan, §7.8–7.9 |
| 20 | Write `product-x1-x2.md`; X1.1–X1.3 | §12.6 |
| 21 | X1.4, X1.5, X2.1 | product plan |
| 22 | X2.2–X2.4, P10 | product plan, §8 |
| 23 | X2.5–X2.8 | product plan, §8.6 |
| 24 | AN1, AR1 | product plan |
| 25 | Write `platform-x3-x6.md`; X3.1–X3.4 | §12.7 |
| 26 | X4.1–X4.4 | platform plan |
| 27 | X5.1–X5.3, P7 | platform plan, §8.2, §7.10 |
| 28 | X6.1–X6.3, P6 | platform plan, §7.9, §8.4 |
| 29 | Write `tier-5-scale.md`; FL1–FL6, P8 | §12.8 |
| 30 | CE1–CE5 | scale plan |
| 31 | F1–F5 (only when needed) | tier-2 plan, project 9 |
| 32 | Write `enterprise.md`; EN1–EN7 | §12.9, §10.2 |

### 13.2 How each work package runs

1. **Chat:** open with 13.3, then ask for the plan of the first milestone.
2. **Claude Code** in the Codespace: `/clear`, then `/milestone <ID>` plus the chat's instructions.
3. **Gates:** paste outputs back into the chat when something fails or looks off.
4. **Merge** each milestone through a pull request with CI green.
5. **Before closing the chat:** get the handoff note (13.4) and save it as `docs/plans/handoff/WP-<nn>.md`.

### 13.3 Opening message for any work package

```
PROJECT: Physical Intelligence Platform (<product>, placeholder "pip"), repo
github.com/trinamichelle29/pqtone (private), project folder <folder>, Codespace on 2-core / 8 GB.
SOURCE OF TRUTH: attached MASTER-README.md (architecture, security, protection, build plan),
CLAUDE.md, and the plan file for this stage. Previous handoff note: <paste or "none">.
WORK PACKAGE: WP-<nn> — milestones <IDs>.
STATE: last tag <tag>; open branches <list>; last gate output <paste or "green">.
HOW WE WORK: implementation in Claude Code with /milestone; this chat for plans, reviews and
debugging. I want direct answers with full copy-paste commands.
RULES: one milestone at a time; plan, then tests, then code; show gate output; never weaken a
test; no person identification ever; state the licence before any dependency; nothing valuable
ships in reusable form (MASTER-README §7).
START: give me the plan for <first milestone ID>.
```

### 13.4 Handoff note (ask for this before closing a chat)

```
Write the handoff note for this work package as Markdown:
1. Milestones finished, with branch, merge commit and the gate output that proved them.
2. Milestones started but not finished, with their exact state.
3. Decisions made (and which ADRs were written or still need writing).
4. Changes this README or a plan file now needs.
5. Open problems and risks.
6. The next work package and its first milestone.
```

### 13.5 Writing a missing plan file

```
Write docs/plans/<file>.md in the same format as tier-2-real-vision.md: goal, where it runs,
exit gate, then one table of milestones per project with "Done when" as a command or observable
check and 🧠 marking parts I write. Use MASTER-README §<section> as the content; keep IDs.
List anything not needed for the exit gate under "Later".
```

---

## 14. Budget and timeline

### 14.1 One-time

| Stage | Items | ₹ (incl. GST where it applies) |
| --- | --- | --- |
| Tier 1 | Codespaces overage only | ~500/month |
| Tier 2 | Camera, PoE injector, cable, GPU 50–100 h, edge mini-PC | 25,440–49,180 |
| IP basics (P1) | Trademark 2 classes at the startup rate; lawyer for NDA, IP assignment and EULA (approximate); 2 hardware security keys (approximate) | 9,000 + 15,000–40,000 + 6,000–12,000 |
| Tier 3 + pilot | Lawyer (privacy pack, pilot agreement), company registration, domain, pilot kit | 45,399–1,09,499 |
| Expansion | Fine-tuning and eval GPU runs, penetration test | 61,234–1,70,155 |
| Enterprise | SOC 2 / ISO audit | Several lakhs, only when a chain asks |

### 14.2 Monthly

| From | Item | ₹ / month (incl. GST) |
| --- | --- | --- |
| Feb 2027 | Staging server | 2,110 |
| Apr 2027 | Production server + object storage | 4,958 |
| Jun 2027 | LLM hosting: CPU server until 3+ paying stores | 8,439 |
| Later | L4 GPU, shop hours only / always on | 20,815 / 42,209 |
| Later | ClickHouse server past ~10 stores | 6,642 |

The plan doc has itemised sources for every price.

### 14.3 Timeline at 20 h/week

- Tier 1: done by **Nov 2026**.
- Tier 2: **Jan 2027**.
- Pilot starts: **end of Mar 2027**.
- First paid contract: **~Jun 2027**.
- Product phase: **to Dec 2027**.
- Platform phase: **to mid-2028**.
- Scale and enterprise: when customers require them.

At 10 h/week, double every gap. After 3 paying stores, one hired engineer is cheaper than another year.

---

## 15. Risks and what to say no to

| Risk | Fallback |
| --- | --- |
| Accuracy bar not met in crowded checkouts | Steeper camera angle, second camera, YOLOX-M, or a revised bar by ADR |
| No pilot store | Ask from January; family or friend's shop; free weekly report for 3 months |
| Box too slow for model and perception together | YOLOX-Tiny, fewer fps, a separate store AI box, or cloud routing |
| Someone copies the edge software or model | §7: most value stays in the cloud; what ships is encrypted, leased, fingerprinted; contracts |
| Licence trap (AGPL detector, restricted model checkpoint, GPL-3 on the box) | ADR per dependency; per-checkpoint check; P9 compliance pack |
| DPDP duties from 13 May 2027 mid-pilot | Privacy pack built to them from day one |
| Model invents numbers | Number guard; tools compute; release gate |
| Lease or attestation bug locks out a paying shop | 7-day leases, 72-hour alert grace, staged rollout, licence-service SLO, manual override runbook |
| Solo capacity | Strict stage gates; hire after 3 paying stores |
| Scope creep | §1.4 and the list below |

**Say no to:**
- face recognition or any identity feature;
- building accounting or GST software;
- training a model from scratch;
- autonomous model actions;
- handing out the cloud platform;
- starting any stage before its gate passes.

---

## 16. Decision records (ADRs) to write

| ADR | Decision | When |
| --- | --- | --- |
| 011 | Windowing approach (Tier 1 R3) | Tier 1 |
| 012 | Detector: YOLOX-S, not Ultralytics; weights provenance | Before V1 |
| 013 | Pilot on a Tier 3 slice, not full Tier 3 | Before TP1 |
| 014 | Valkey instead of Redis | X2.2 |
| 015 | Base LLM and exact checkpoint licence | X2.3 |
| 016 | Embeddings: bge-m3 | X2.6 |
| 017 | Edge hardware: N100/N150 vs Jetson | Before PL2 |
| 018 | Licensing and lease policy (7 days, 72-hour alert grace) | P4 |
| 019 | Model protection approach (§7.9–7.10) | P6 |
| 020 | Internal AGPL/BSL components (Grafana, Loki, Redpanda) | Tier 3 |
| 021 | Open-source obligations on the box (LGPL carve-outs, no GPL-3) | P9 |
| 022 | App SDK: Python handlers, React screens | X3.4 |
| 023 | India-only hosting on E2E Networks | INF1 |
| 024 | Harbor as the registry | P2 |
| 025 | Compiled edge code with Nuitka | P5 |

---

## 17. Glossary

| Term | Meaning |
| --- | --- |
| Attestation | The box proves to the cloud, with a TPM-signed quote, that it booted the expected software |
| Cell | A complete, isolated copy of the cloud stack serving a group of tenants |
| CloudEvents | The standard envelope every event uses |
| Edge box | The mini-PC in the shop running perception and the outbox |
| Event time | When something happened (the `time` attribute), as opposed to when it was processed |
| GGUF | The model file format llama.cpp loads |
| Lease | A short-lived signed licence for one box |
| LUKS | Linux full-disk encryption |
| mTLS | TLS where both sides present certificates |
| PCR | A TPM register holding a measurement of what booted |
| RLS | PostgreSQL row-level security |
| SBOM | Software bill of materials: everything inside a release |
| Store AI box | The box (or the same edge box) running your LLM in the shop |
| TPM | A security chip that keeps keys inside it and measures the boot |
| Watermark / fingerprint | Secret triggers trained into a model to prove a copy is yours |

---

## 18. Sources

Checked between 3 and 9 Oct 2026.

**Prices**
- [E2E Networks rate card](https://www.e2enetworks.com/pricing.md): GPU, servers, storage.
- [GitHub Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces).
- [RunPod pricing summary](https://www.hackceleration.com/labs/runpod-pricing).
- Hardware: [N100 mini-PC listings, Amazon.in](https://www.amazon.in/intel-n100-mini-pc/s?k=intel+n100+mini+pc&page=4), [VIGI C440I price](https://fgtechstore.com/?p=17723), [Jetson Orin Nano Super](https://hwbusters.com/news/nvidias-jetson-orin-nano-super-developer-kit-power-affordability-and-ai-innovation-at-249/).

**Licences**
- [YOLOX (Apache-2.0)](https://github.com/Megvii-BaseDetection/YOLOX).
- Open-weight model licences: [comparison](https://aiwiki.ai/wiki/open_weight_license_comparison), [per-checkpoint caveats](https://wz-it.com/en/knowledge/ki/open-llm-licenses-commercial-use/), [landscape](https://presenc.ai/research/open-weight-license-landscape-2026).

**Integrations**
- [TallyPrime integration (XML over HTTP)](https://help.tallysolutions.com/pre-requisites-for-integrations/), [Tally data exchange options](https://developer.tallysolutions.com/build/dataexchange).

**Law and security**
- [DPDP Act and Rules phased rollout](https://www.hoganlovells.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-), [DPDP penalties and phases](https://compliancehub.wiki/india-dpdp-consent-manager-november-2026-phase-two-deadline-compliance/).
- [OWASP Top 10 for LLM Applications 2025](https://help.hcl-software.com/appscan/Enterprise/10.10.0/topics/r_owasp_top_10_for_llm.html) (project home: [genai.owasp.org](https://genai.owasp.org/initiative_name/top-10-list-for-llm-genai/)).
- [Trademark fees in India](https://www.taxaj.com/learn/?p=371).

**Tools**
- [Claude Code docs](https://code.claude.com/docs): subagents, skills, settings.
