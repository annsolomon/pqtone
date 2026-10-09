# Tier 1: Virtual Supermarket (core pipeline, no video)

**Goal.** A fully simulated store produces events. The pipeline validates and stores them, detects queue, dwell and absence incidents in event time, shows them live on a floor map for human review, and proves its accuracy in CI.

**Runs entirely in Codespaces.**

**Status.** The code for all five projects exists in `pip-tier1.zip`. What it has and hasn't proven:

| Proven | Not yet proven |
|---|---|
| Python tests pass (20). | Spring Boot and Kafka Streams code has never been compiled against its real dependencies. |
| Rule-engine core tests pass (13 JUnit). | The Docker stack and the end-to-end tests have never been run. |
| React tests and build pass. | |
| Offline sim → rules → scorer matrix passes. | |

So this tier starts with **Stage 0: make it green**.

**Exit gate (T1 → T2).**
- `make all` is green locally and in GitHub Actions.
- A Playwright test watches a queue build up in the console and sees the alert.
- The scorer baseline is committed.
- Tag `v0.1.0`.

---

## Stage 0: make it real (do this first, about 1 week)

| # | Milestone | Done when |
|---|---|---|
| ✅ 0.1 | **Devcontainer**: `.devcontainer/devcontainer.json` with JDK 21, Maven 3.9, Node 22, Python 3.12, Docker-in-Docker; 4-core / 16 GB. | `java -version`, `mvn -v`, `node -v`, `python3 -V`, `docker compose version` all work in a fresh Codespace. Note: 2-core / 8 GB proven by make all; 4-core optional |
| ✅ 0.2 | **`make test-fast`**: runs `mvn -q verify` in both Java modules, `pytest sim scorer`, `npm test` and `npm run typecheck`, without Docker. Becomes the hook gate for Claude Code. | Completes in under 3 minutes, exit 0. |
| ✅ 0.3 | **Compile event-core and rules-engine for real.** Fix whatever Maven reports. Likely suspects: generic inference in `Map.of(...)` calls, Spring Security 6.3 DSL signatures, networknt validator API. | `mvn verify` green in both modules; all unit tests run (not skipped). |
| ✅ 0.4 | **`make up` healthy.** Fix stack issues. Likely suspects: Redpanda SASL bootstrap and ACL flags, Keycloak 26 hostname/relative-path options, Postgres TLS key permissions, nginx variable `proxy_pass`. | `scripts/wait-healthy.sh` reports every service healthy. |
| ✅ 0.5 | **`make all` green.** Fix e2e failures one test at a time. Never weaken a test to pass it. If a test is wrong, explain why in the commit. | Full run green twice in a row (second run proves idempotency). |
| ✅ 0.6 | **CI green on GitHub.** Push, make `ci.yml` pass, let Renovate pin action SHAs. | Green badge; reports uploaded as artifacts. Note: CI green on annsolomon/pqtone; actions pinned to SHAs in the workflow; Renovate app and main protection are repo-owner settings |
| 0.7 | **Rename decision.** Choose the product name; one mechanical PR renames `pip`, `com.pip`, `urn:pip`, `schemas.pip.local`. | `make all` green after the rename. |

**Opus prompt for 0.3–0.5:**

> Run `make all`. When it fails, fix the root cause, re-run the narrowest command that reproduces it, then re-run `make all`. Keep a log in `docs/plans/stage0-log.md` with each failure, its cause and the fix. Do not delete or weaken tests; if a test is wrong, stop and explain. Do not change the event contract.

---

## Project 1: event-core

**Learn:** Spring Boot, Flyway, JSON Schema.

**Done when:** it accepts, validates and stores events. Already built; Stage 0 proves it.

**What exists.**
- CloudEvents ingest: HTTP single and batch, plus the raw Kafka topic.
- JSON Schema validation through a catalog.
- `(source, id)` dedup with a conflict check.
- Partitioned append-only event table.
- Transactional outbox and DLQ.
- Keycloak BFF auth.
- Review API with ETags.
- Hash-chained audit log.
- SSE live stream.
- Flyway migrations run by a separate least-privilege role.

### Learning milestones

| # | Milestone | Done when | 🧠 |
|---|---|---|---|
| ✅ E1 | **Trace a request end to end.** Use the debugger plus Jaeger to follow one HTTP event: filter chain → controller → validator → transaction → outbox → Kafka. Write `docs/learn/event-core-request-path.md` in your own words. | Doc exists; you can explain each Spring bean involved. Note: Doc from the code; the debugger/Jaeger walk-through is in its last section | 🧠 You write |
| ✅ E2 | **Schema evolution.** Add `store.queue.length` v1.1.0 with an optional `estimatedWaitSeconds` field. Add a CI script `scripts/schema-compat.py` that fails if a new version removes or renames a field, tightens a type, or adds a required field. Register both versions; producers may send either. | Compat script in CI; a test proves v1.0.0 and v1.1.0 events are both accepted; a deliberately breaking v1.2.0 fails CI. | 🧠 Compat rules |
| ✅ E3 | **Flyway discipline.** Add `V5__incident_assignee.sql` (nullable column, grant, index). Practise the expand → migrate → contract pattern. Write a rollback note. Add a test that migrations apply cleanly to an empty DB and to a DB at V4. | Both paths green in CI. |  |
| ✅ E4 | **Testcontainers integration tests.** Repository and ingest tests against real Postgres 16 and Redpanda in `mvn verify`, using Docker-in-Docker in the devcontainer and in CI. | Covered: dedup race (two concurrent identical inserts → one accepted, one duplicate); conflict path; outbox relay with Kafka down → backlog drains when it returns. | 🧠 The race test |
| ✅ E5 | **Contract tests.** Generate the OpenAPI doc from code (springdoc) and diff it against `openapi.yaml` in CI. | Drift fails the build. |  |

---

## Project 2: store-sim

**Done when:** the same seed gives the same output. Already true and tested by byte-identical hashes.

### Learning and deepening milestones

| # | Milestone | Done when |
|---|---|---|
| ✅ S1 | **Read the model.** Draw the discrete-event loop (heap, scheduler, RNG streams) in `docs/learn/store-sim.md`. Explain why four child RNG streams keep runs stable when you add a new random draw. 🧠 | Doc written. |
| S2 | **Second store layout** (`store-002`: two queues, an extra zone) and a multi-store scenario. Rules and console must handle more than one store. | The offline matrix includes multi-store; the console store picker works. |
| ✅ S3 | **Scenario authoring guide** plus 3 new scenarios: `staff_shortage`, `flash_sale`, `closing_time`. | Each produces ground truth; scorer thresholds hold. Note: baseline rows added after the first CI run |
| ✅ S4 | **HTTP sink e2e** through the gateway with OAuth client credentials (it exists but is untested end to end). | `make e2e-http-sim` stores 100% of events. |
| ✅ S5 | **Noise hooks** (prepares for Tier 2): a `faults.vision` section accepting a noise profile file. Leave it as a no-op until Tier 2 fills it. | Schema for noise profiles exists; tests pass. |

---

## Project 3: rules-engine

**Learn:** keyed state, event-time windows, watermarks.

**What exists.**
- Per-(store, run) keyed state in a RocksDB store with a changelog.
- A custom watermark (stream time − grace) with a reorder buffer.
- Queue threshold with hysteresis, dwell, and absence ("no register opened within 300 s").
- Shadow mode.
- EOS v2.
- Heartbeats.
- An offline runner that is proven equivalent to the Python reference.

**What your ladder asks that isn't there yet:** native Kafka Streams **windows**. The current rules deliberately avoid them, because threshold-for-duration logic isn't a window. Learn them with a rule that *is* windowed.

| # | Milestone | Done when | 🧠 |
|---|---|---|---|
| ✅ R1 | **Explain the watermark.** Write `docs/learn/watermarks.md`: stream time vs event time vs wall time, why grace exists, what "late" means here vs Flink. Include a worked example from the `late_beyond_grace` scenario. | Doc written; you can predict which events get dropped in a hand-made sequence. Note: docs/learn/watermarks.md; its 12-event sequence is executed by WatermarkLatenessTest | 🧠 You write |
| ✅ R2 | **Windowed rule R-FOOT-001**: footfall spike. Count `zone.entered` per zone in 5-minute tumbling windows (`TimeWindows.ofSizeAndGrace`). Alert when a window exceeds 2× the trailing hour's mean. Use `suppress(untilWindowCloses)` so each window emits once. Add it to the Python reference and to the scorer. | Offline matrix and e2e green with the new rule; JUnit tests for window boundaries and late records. | 🧠 The windowing code |
| ✅ R3 | **Compare both approaches** in an ADR: native windows + suppress vs the custom reorder buffer. Cover memory, determinism and latency. | `docs/adr/011-windowing.md`. |  |
| R4 | **Rule versioning and hot reload.** A `rules.yaml` change bumps the rule version. The engine reloads via a compacted `rules.config.v1` topic instead of a restart. Incident ids include the major version (already true). | Changing a threshold in a running stack changes behaviour without a restart; old incidents keep their version. | |
| R5 | **Scale test.** 50 simulated stores at 10× speed; measure throughput, state size, p95 processing lag. Raise partition count and threads; document the limits. | `docs/perf/rules-engine.md` with numbers and graphs. | |
| R6 | **Shadow → enforce promotion flow.** An admin sees shadow precision and recall from the latest e2e score in the console and promotes a rule through a PR template. No UI toggle; changes go through review. | Documented flow; console shows the shadow score. | |

---

## Project 4: ops-console

**Done when:** you can watch a simulated queue build up and get alerted.

**What exists.**
- Floor map with live occupancy and a queue dot-trail.
- Incident rail, review queue, and a detail page with ack, confirm and dismiss.
- Shadow and audit pages.
- SSE, BFF login and CSRF.

| # | Milestone | Done when |
|---|---|---|
| ✅ C1 | **Alert you can't miss.** When a new enforce incident opens, show a toast and the zone outline animation, plus an optional browser Notification (permission asked from a user gesture) and a sound toggle. | Manual check, plus a unit test of the alert reducer. Note: alertReducer + 14 unit tests; toast, zone pulse, opt-in sound and desktop notification; the C2 Playwright test checks the toast in the browser |
| ✅ C2 | **Playwright demo test** (this is the "Done when"). A `tests/ui` container logs in as reviewer, starts `make sim` with `register_delay`, waits until the queue counter reaches 6 or more, sees the incident appear in the rail, opens it and confirms it. Records a video artifact. | Green in `make all` and CI; the video is uploaded. Note: tests/ui/test_demo.py via make e2e-ui; video uploaded as the ui-demo CI artifact |
| ✅ C3 | **Accessibility pass.** axe-core in Playwright: 0 serious violations. Keyboard-only review flow. The map has a table alternative. | axe report in CI. |
| ✅ C4 | **Timeline view.** Per-incident chart of queue length and open registers around onset (from `/api/events`). Explains *why* it fired. | Reviewers can see the evidence on the detail page. |
| ✅ C5 | **Review metrics.** Time-to-ack, confirm rate per rule, shown to admins. Feeds the precision estimate for real sites in Tier 2. | Admin page shows them. |

🧠 Write the C1 alert reducer and its tests yourself; it is a good, small React state exercise.

---

## Project 5: scorer

**Done when:** it compares rule output to ground truth, reports precision, recall and latency, and runs in CI. It does already; the milestones deepen it.

| # | Milestone | Done when |
|---|---|---|
| ✅ Q1 | **PR comment.** The CI job posts `score.md` as a sticky PR comment, with a delta against `baseline.json`. | Visible on a test PR. |
| ✅ Q2 | **Wall-clock processing latency** in e2e: ingest → incident row, p50 and p95. Add it to thresholds. | Reported; a gate is added. Note: ADR-029 |
| ✅ Q3 | **Baseline update flow.** On `main`, CI uploads `baseline.candidate.json`. A maintainer runs `make baseline-accept`, which opens a PR. Never auto-update the baseline. | Documented and scripted. Note: make baseline-accept, RB-09 |
| ✅ Q4 | **Confidence intervals.** Wilson intervals on precision and recall so small samples (2 absence incidents) aren't read as certainty. Gates use the lower bound when n ≥ 20, otherwise they warn. | Shown in the report. Note: Gate semantics in ADR-027 |
| ✅ Q5 | **Hungarian matching** (to match the architecture doc) with property tests comparing it to brute force on small cases. 🧠 | Tests pass; ADR notes the change. Note: ADR-028 |

---

## Tier 1 demo script (`docs/demos/tier-1.md`, 5 minutes)

1. `make up`, open the console as reviewer.
2. `SCENARIO=register_delay make sim`: the queue builds up, the checkout outline turns amber, a toast appears.
3. Open the incident, look at the timeline, confirm it. Show the audit log as admin.
4. As admin, show the shadow absence rule firing silently.
5. Show the latest CI run's quality report: precision, recall and latency per rule and scenario.
6. Pull the plug: `docker compose stop rules-engine`. The banner says the pipeline is degraded. Start it again: it catches up with identical incidents.
