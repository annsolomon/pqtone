# Stage 0 log

What had to change to make Tier 1 run for real, and why.

| Date | Milestone | What changed | Why |
| --- | --- | --- | --- |
| 2026-10 | 0.3–0.5 | Makefile: one `export` per line (`DOCKER_BUILDKIT`, `COMPOSE_DOCKER_CLI_BUILD`) | Combined exports broke the build environment |
| 2026-10 | 0.3–0.5 | `scripts/bootstrap.sh`: secrets from `openssl rand -hex 24`, fails on any empty secret | Empty secrets broke Keycloak and Postgres logins |
| 2026-10 | 0.3–0.5 | event-core and rules-engine runtime image: `eclipse-temurin:21-jre` (glibc) | Alpine (musl) broke the RocksDB and zstd native libraries |
| 2026-10 | 0.3–0.5 | `tests/http/conftest.py`: clears the Secure flag on cookies before the Keycloak form POST | Python's cookie jar doesn't send Secure cookies to http://localhost, browsers do |
| 2026-10 | 0.3–0.5 | nginx: `/actuator` returns 404; forwards X-Forwarded-Proto/Host/Port through maps | Hide internals; correct redirects behind the Codespaces proxy |
| 2026-10 | 0.3–0.5 | `application.yml`: `redirect-uri = ${PIP_PUBLIC_URL}/login/oauth2/code/{registrationId}` | Login redirect must match the public URL in Codespaces |
| 2026-10-03 | 0.1 | Devcontainer: Temurin 21, Maven 3.9.9, Docker CE docker-in-docker, git-lfs, toolchain gate | Docker had to be started by hand; Maven drifted to 3.10 |
| 2026-10-03 | 0.1 | `fix(bootstrap)` 88e1f44: realm `sslRequired` rendered from `PIP_KC_SSL_REQUIRED` | The earlier fix was lost; Codespaces URL mode needs `none` |
| 2026-10-09 | 0.5 | Second consecutive `make all` green, no `make clean` between | Proves reruns are idempotent |
| 2026-10-09 | 0.2 | Makefile: `dev-setup` and Docker-free `test-fast` | One fast gate for Claude Code |
| 2026-10-09 | — | Repository moved to `annsolomon/pqtone`, imported from the `3bd157c` archive (history stays in `trinamichelle29/pqtone`) | Claude can only reach repos attached to its session |
