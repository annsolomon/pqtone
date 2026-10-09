# Stage 0 log

What had to change to make Tier 1 run for real, and why.

| Date | Milestone | What changed | Why |
| --- | --- | --- | --- |
| 2026-10 | 0.3–0.5 | Makefile: one `export` per line (`DOCKER_BUILDKIT`, `COMPOSE_DOCKER_CLI_BUILD`) | Combined exports broke the build environment |
| 2026-10 | 0.3–0.5 | `scripts/bootstrap.sh`: secrets from `openssl rand -hex 24`, fails on any empty secret | Empty secrets broke Keycloak and Postgres logins |
| 2026-10 | 0.3–0.5 | event-core and rules-engine runtime image: `eclipse-temurin:21-jre` (glibc) | Alpine (musl) broke the RocksDB and zstd native libraries |
| 2026-10 | 0.3–0.5 | `tests/http/conftest.py`: clears the Secure flag on cookies before the Keycloak form POST | Python's cookie jar doesn't send Secure cookies to http://localhost, browsers do |
| 2026-10 | 0.3–0.5 | nginx: `/actuator` returns 404; forwards X-Forwarded-Proto/Host/Port through maps | Hide internals; correct redirects behind the Codespaces proxy |
| 2026-10 | 0.3–0.5 | `application.yml`: `redirect-uri = ${PQT_PUBLIC_URL}/login/oauth2/code/{registrationId}` | Login redirect must match the public URL in Codespaces |
| 2026-10-03 | 0.1 | Devcontainer: Temurin 21, Maven 3.9.9, Docker CE docker-in-docker, git-lfs, toolchain gate | Docker had to be started by hand; Maven drifted to 3.10 |
| 2026-10-03 | 0.1 | `fix(bootstrap)` 88e1f44: realm `sslRequired` rendered from `PQT_KC_SSL_REQUIRED` | The earlier fix was lost; Codespaces URL mode needs `none` |
| 2026-10-09 | 0.5 | Second consecutive `make all` green, no `make clean` between | Proves reruns are idempotent |
| 2026-10-09 | 0.2 | Makefile: `dev-setup` and Docker-free `test-fast` | One fast gate for Claude Code |
| 2026-10-09 | — | Repository moved to `annsolomon/pqtone`, imported from the `3bd157c` archive (history stays in `trinamichelle29/pqtone`) | Claude can only reach repos attached to its session |
| 2026-10-09 | 0.6 | CI moved to the repo root (`working-directory: pqt`); every action pinned to a commit SHA | GitHub only runs root workflows; pinned SHAs cannot be moved by a hijacked tag |
| 2026-10-09 | 0.6 | trivy-action held at v0.35.0 (Trivy v0.69.3) | The releases verified clean after the March 2026 trivy-action tag hijack (CVE-2026-33634) |
| 2026-10-09 | 0.6 | CodeQL analyses without upload; `sarif-gate.py` fails on security-severity >= 7.0; reviewed exceptions in `.github/codeql-accepted.json` | Private repo has no Advanced Security, so code scanning cannot receive uploads |
| 2026-10-09 | 0.6 | Failure details (make tail, stack errors, Trivy and CodeQL findings) emitted as error annotations | Raw job logs are not reachable from the Claude session; annotations are |
| 2026-10-09 | 0.6 | event-core: Spring Boot 3.3.5 -> 3.5.16, Jackson 2.21.7, PostgreSQL driver 42.7.14 | HIGH/CRITICAL CVEs: Tomcat CVE-2025-24813, Kafka clients CVE-2026-35554, Micrometer CVE-2026-40984, spring-kafka, actuator, json-smart, pgjdbc |
| 2026-10-09 | 0.6 | rules-engine: Kafka 3.9.2, Jackson 2.21.7, Micrometer 1.15.12, slf4j 2.0.17, JUnit 5.12.2 | Same CVEs plus lz4-java CVE-2025-12183 (Kafka 3.9.2 uses the maintained at.yawk.lz4 fork) |
| 2026-10-09 | 0.6 | ops-console: react-router-dom 6.30.6, vite 8.3.4, vitest 5.0.3, plugin-react 6.1.2, TypeScript 5.9.3; vite.config uses `vitest/config` | HIGH @remix-run/router CVE-2026-22029; critical vitest/tinypool and high vite advisories in the dev toolchain |
| 2026-10-09 | 0.6 | ops-console Dockerfile states `USER 101` | Trivy DS-0002 (image user must not be root); the base image already runs as 101 |
| 2026-10-09 | 0.6 | Trivy fs scan drops the `secret` scanner | gitleaks (secret scan job) owns secret detection |
| 2026-10-09 | 0.6 | `scripts/summary.sh` hides generated passwords when `CI` is set | CI logs and annotations are readable by everyone with repo access |
| 2026-10-09 | 0.6 | CI frees runner disk before `make all` | Redpanda raised a storage alert at 6.7% free on the hosted runner |
| 2026-10-09 | 0.6 | rules-engine imports `jackson-bom`; both services pin `at.yawk.lz4:lz4-java` 1.11.4 | kafka-streams pulled an older jackson-annotations (NoClassDefFound `JsonSerializeAs` in the image build); lz4-java CVE-2026-106451 |
| 2026-10-09 | 0.6 | `.trivyignore`: CVE-2026-47884, CVE-2026-47890 until 2026-12-31 (ADR-026) | Spring Framework 6.2 is out of OSS support; neither CVE is reachable (no XsltView, no view-fragment SSE). Spring Boot 4 migration planned before the pilot |
| 2026-10-09 | 0.6 | event-core pins Tomcat 10.1.60, the newest on Maven Central (Boot 3.5.16 ships 10.1.55) | CRITICAL CVE-2026-65182, CVE-2026-65905, CVE-2026-68525, fixed in 10.1.58 |
| 2026-10-09 | 0.6 | Workflow `defaults.run.shell: bash` | Without it steps run as `bash -e` (no pipefail), so `make all \| tee` hid a failed image build |
| 2026-10-09 | C2 | `make help` regex accepts digits; `dev-setup` and `test-fast` documented | `e2e*` targets were missing from `make help` |
| 2026-10-09 | 0.6 | event-core `StoreController`: a layout is found by matching the store id against the files listed in the layouts folder; the path is never built from the request | CodeQL `java/path-injection` (7.5) failed every push to `main`; pull requests only report alerts on changed lines, so it never showed there |
