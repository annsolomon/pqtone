# Physical Intelligence Platform — Tier 1

An event pipeline for retail store operations that runs on synthetic data:

- a deterministic **store simulator** emits CloudEvents;
- **event-core** validates, deduplicates and stores them;
- a **Kafka Streams rules engine** raises incidents in event time;
- a **React ops console** lets people review those incidents;
- a **scorer** measures rule precision, recall and latency against ground truth and gates CI.

The full design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It covers the threat model, privacy commitments, SLOs and ADRs, and its first section lists what the local stack implements and what it doesn't yet.

## Run everything

```bash
make all
```

This one command does the following:

1. Generates `.env` with random secrets and renders the Keycloak realm.
2. Builds every image. The unit tests run inside the builds: Java with JUnit and TopologyTestDriver, Python with pytest, and the web app with TypeScript checks and vitest.
3. Runs the **offline quality gate**: 8 scenarios × 3 seeds go through the simulator, the production rule engine and the scorer, with no Kafka needed.
4. Starts the stack: Postgres (TLS), Flyway, Redpanda (SASL/SCRAM + ACLs), Keycloak, event-core, rules-engine, the gateway with the console, Prometheus, Grafana and Jaeger.
5. Runs a **live end-to-end check**. The simulator writes to Kafka, events go through event-core and rules-engine into Postgres, and the scorer applies the same gates to the stored incidents.
6. Runs the **HTTP tests** through the gateway: ingest contract, dedup and conflict, auth, RBAC, CSRF, the review flow with ETags, the audit chain, SSE and logout.
7. Runs the **database tests**: append-only rows, least-privilege roles, TLS-only connections.
8. Prints the URLs and the generated logins.

Requirements: Docker with Compose v2, `make`, and about 6 GB of free RAM. The first build downloads Maven, npm and pqt dependencies, so expect 10–15 minutes. Later runs use the build cache.

No `make`? Run `./run.sh` instead.

## After it finishes

| What | Where |
|---|---|
| Console | http://localhost:8080 (logins printed at the end: viewer, operator, reviewer, admin) |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Jaeger | http://localhost:16686 |
| Reports | `.work/reports/` (`offline/score.md`, `e2e/score.md`, JUnit XML) |

To watch the floor fill up live, run `make sim`, or pick a scenario with `SCENARIO=rush_hour make sim`.

Other targets: `make help`, `make up`, `make e2e` (includes the browser demo `make e2e-ui`, localhost mode only), `make offline`, `make logs`, `make down`, and `make clean` (deletes data and secrets).

**GitHub Codespaces.** Open the console through port forwarding to `localhost:8080`, either from VS Code desktop or with `gh codespace ports forward 8080:8080` on your machine. The login redirect URLs are bound to `PQT_PUBLIC_URL` in `.env`.

## Repository layout

```
config/            rules.yaml (rule definitions), store layouts
schemas/           CloudEvents envelope + data schemas, catalog.json
sim/               store-sim (Python): model, faults, reference rule implementation, sinks
scorer/            pqt-scorer (Python): matching, gates, reports; matrix, thresholds, baseline
services/
  event-core/      Spring Boot 3.3 / Java 21: ingest, dedup, outbox, DLQ, incidents, review, audit, SSE, BFF auth
  rules-engine/    Kafka Streams 3.7 / Java 21: event-time rules, shadow mode, EOS v2, offline runner
web/ops-console/   React 18 + TypeScript + Vite; served by the nginx gateway
deploy/            compose stack, gateway, Postgres init, Redpanda ACLs, Keycloak realm, observability
tests/             end-to-end HTTP, database and browser (Playwright, tests/ui) tests
docs/              architecture, runbooks
```

## How the pieces fit

```
store-sim ──SASL──▶ store.events.raw ──▶ event-core ──(validate, dedup, outbox)──▶ store.events.v1
edge HTTP ─OAuth2─▶ /v1/events ────────▶     │  └─ invalid ─▶ store.events.dlq
                                             ▼
                                         Postgres (append-only events, incidents, reviews, audit chain)
store.events.v1 ──▶ rules-engine ──EOS──▶ incidents.v1 / incidents.shadow.v1 / rules.heartbeat.v1 ──▶ event-core
browser ──▶ gateway :8080 ──▶ console (static) · /api (session + CSRF) · /auth (Keycloak)
```

Key properties:

- **Deterministic rules.**
  - Every rule decision is made in event time: per store, the watermark is stream time minus a 30 s grace period, with a reorder buffer.
  - Replaying the same input produces the same incidents, with the same IDs.
  - The simulator ships an independent Python implementation of the same rule spec. The CI gate checks that the Java engine matches it.
- **Exactly-once where it matters.**
  - Ingest is idempotent on `(source, id)`. A conflicting payload gets a 409 and is audited.
  - The outbox gets events to Kafka at least once.
  - The rules engine runs with Kafka Streams EOS v2, and incident writes are idempotent on `incident_id`.
- **People decide.**
  - Incidents never act on their own. Operators acknowledge; reviewers confirm or dismiss with a reason.
  - Every decision goes into a hash-chained audit log.
  - Shadow-mode rules are measured but never shown to staff.
- **Least privilege throughout.**
  - Separate Postgres roles for migration, the app and reads.
  - Per-service Kafka users with ACLs.
  - Producers are bound to their `source` prefix.
  - Containers run non-root with all capabilities dropped.
  - Only the gateway is published, on 127.0.0.1.

## Changing rules

Edit `config/rules.yaml` and open a PR. CI runs the offline matrix. A rule change that drops precision or recall more than 2 points against `scorer/baseline.json`, or that misses the thresholds in `scorer/thresholds.yaml`, fails the build.

New rules should start in `mode: shadow`.
