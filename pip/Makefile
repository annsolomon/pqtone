# PIP Tier 1. One command does everything:  make all
SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

COMPOSE  := docker compose --env-file .env -f deploy/compose/docker-compose.yml
RUN      := $(COMPOSE) run --rm --no-deps
WORK     := $(CURDIR)/.work
SCENARIO ?= register_delay
# Fixed seeds drive the offline quality gate (scorer/matrix.yaml); the live run uses a fresh
# seed each time so repeated runs exercise new data. Override with SEED=42 to reproduce one.
SEED     ?= $(shell date +%s)
# The browser demo always needs a fresh run (a repeated seed is deduplicated away), so it never uses SEED.
UI_SEED  ?= $(shell echo $$(( $$(date +%s) + 7919 )))
# Same for the HTTP sink run, which must not collide with the Kafka run's event ids.
HTTP_SEED ?= $(shell echo $$(( $$(date +%s) + 104729 )))
# The hot-reload run (R4) also needs ids of its own.
HOT_SEED ?= $(shell echo $$(( $$(date +%s) + 15485863 )))
export DOCKER_BUILDKIT := 1
export COMPOSE_DOCKER_CLI_BUILD := 1

.PHONY: all help bootstrap build offline up e2e e2e-pipeline e2e-http e2e-http-sim e2e-db e2e-ui e2e-hot-reload rules-publish rules-reset summary sim down clean logs ps

all: bootstrap build test-it offline up e2e summary ## Build + unit tests, integration tests, offline quality gate, start the stack, live end-to-end tests, summary

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Generate .env secrets and the Keycloak realm (idempotent)
	@./scripts/bootstrap.sh

build: bootstrap ## Build all images; Java, Python and web unit tests run inside the builds
	$(COMPOSE) --profile tools build

offline: bootstrap ## Rule-quality gate without Kafka: sim matrix -> rules engine -> scorer
	rm -rf "$(WORK)/matrix" "$(WORK)/reports/offline" && mkdir -p "$(WORK)/matrix" "$(WORK)/reports"
	$(RUN) tools store-sim matrix --matrix scorer/matrix.yaml --scenarios-dir sim/scenarios --out /work/matrix
	$(RUN) --user "$$(id -u):$$(id -g)" -v "$(WORK):/work" --entrypoint java rules-engine \
	  -cp /app/rules-engine.jar com.pip.rules.app.OfflineRunner --rules /config/rules.yaml --matrix /work/matrix
	$(RUN) tools pip-scorer offline --matrix-dir /work/matrix --thresholds scorer/thresholds.yaml \
	  --baseline scorer/baseline.json --out /work/reports/offline

up: bootstrap ## Start the stack and wait until it is healthy
	$(COMPOSE) up -d
	COMPOSE="$(COMPOSE)" ./scripts/wait-healthy.sh

e2e: e2e-pipeline e2e-http e2e-http-sim e2e-db e2e-ui e2e-hot-reload ## Live end-to-end tests against the running stack

e2e-pipeline: ## Simulate SCENARIO through Kafka and score the incidents stored in PostgreSQL
	rm -rf "$(WORK)/e2e" "$(WORK)/reports/e2e" && mkdir -p "$(WORK)/reports"
	$(RUN) tools store-sim run --seed $(SEED) --scenario sim/scenarios/$(SCENARIO).yaml --out /work/e2e --sink kafka
	$(RUN) tools pip-scorer e2e --case-dir /work/e2e --out /work/reports/e2e --timeout 600

e2e-http: ## Security and API tests through the gateway (auth, RBAC, CSRF, ingest contract, review flow)
	$(COMPOSE) run --rm e2e-http

e2e-http-sim: ## The simulator's HTTP sink through the gateway with OAuth client credentials; every event stored once
	rm -rf "$(WORK)/http-sim" && mkdir -p "$(WORK)/reports"
	$(COMPOSE) run --rm e2e-http sh -c 'PIP_CLIENT_SECRET="$$PIP_SIM_CLIENT_SECRET" \
	  PIP_CLIENT_ID=store-sim \
	  PIP_TOKEN_URL=http://localhost:8080/auth/realms/pip/protocol/openid-connect/token \
	  PIP_INGEST_URL=http://localhost:8080/v1/events \
	  store-sim run --seed $(HTTP_SEED) --scenario sim/scenarios/duplicates.yaml --out /work/http-sim --sink http'
	$(RUN) tools pip-scorer stored --case-dir /work/http-sim --timeout 180

openapi-check: ## Diff the running event-core's generated OpenAPI with services/event-core/openapi.yaml (E5)
	$(COMPOSE) run --rm e2e-http python -m pytest -q -p no:cacheprovider tests/http/test_openapi_contract.py

RULES ?= config/rules.yaml
rules-publish: ## R4: publish RULES (a file under config/ or .work/) to the running engines, no restart
	$(COMPOSE) run --rm rules-admin --rules $(patsubst .work/%,/work/%,$(patsubst config/%,/config/%,$(RULES)))

rules-reset: ## R4: drop the published rules; every engine goes back to config/rules.yaml
	$(COMPOSE) run --rm rules-admin --reset

e2e-hot-reload: ## R4: change a threshold in the running stack without a restart; old incidents keep their version
	rm -rf "$(WORK)/hot-reload" && mkdir -p "$(WORK)/hot-reload" "$(WORK)/reports"
	@started=$$(docker inspect -f '{{.State.StartedAt}}' $$($(COMPOSE) ps -q rules-engine)); \
	  $(RUN) tools python tests/hot_reload/check.py make-rules --out /work/hot-reload/rules.yaml --threshold 4 --version 1.1.0 && \
	  $(RUN) tools python tests/hot_reload/check.py snapshot --out /work/hot-reload/before.json && \
	  $(MAKE) --no-print-directory rules-publish RULES=.work/hot-reload/rules.yaml && \
	  $(RUN) tools python tests/hot_reload/check.py wait-rules --version 1.1.0 --source topic && \
	  $(RUN) tools store-sim run --seed $(HOT_SEED) --scenario sim/scenarios/register_delay.yaml \
	    --rules /work/hot-reload/rules.yaml --out /work/hot-reload/sim --sink kafka && \
	  $(RUN) tools python tests/hot_reload/check.py verify --case-dir /work/hot-reload/sim \
	    --rules /work/hot-reload/rules.yaml --snapshot /work/hot-reload/before.json; rc=$$?; \
	  $(MAKE) --no-print-directory rules-reset && \
	  $(RUN) tools python tests/hot_reload/check.py wait-rules --version 1.0.0 --source file || rc=1; \
	  now=$$(docker inspect -f '{{.State.StartedAt}}' $$($(COMPOSE) ps -q rules-engine)); \
	  if [ "$$started" != "$$now" ]; then echo "rules-engine restarted during the test ($$started -> $$now)"; rc=1; fi; \
	  exit $$rc

e2e-db: ## Database guarantees (append-only, least privilege, TLS-only)
	$(RUN) tools python -m pytest -q -p no:cacheprovider tests/db --junitxml=/work/reports/e2e-db.xml

e2e-ui: ## Browser demo (Playwright): queue builds up, alert shows, reviewer confirms; video in .work/reports/ui
	rm -rf "$(WORK)/reports/ui" "$(WORK)/ui-sim" && mkdir -p "$(WORK)/reports/ui"
	@ui=$$($(COMPOSE) run -d e2e-ui); \
	  echo "browser test container $$ui"; \
	  for _ in $$(seq 1 120); do \
	    [ -f "$(WORK)/reports/ui/ready" ] && break; \
	    [ "$$(docker inspect -f '{{.State.Running}}' $$ui)" = true ] || break; \
	    sleep 1; \
	  done; \
	  sim=""; \
	  if [ -f "$(WORK)/reports/ui/ready" ]; then \
	    echo "signed in; streaming register_delay (seed $(UI_SEED)) at 20x"; \
	    sim=$$($(RUN) -d tools store-sim run --seed $(UI_SEED) --scenario sim/scenarios/register_delay.yaml --out /work/ui-sim --sink kafka --speed 20); \
	  fi; \
	  rc=$$(docker wait $$ui); \
	  docker logs $$ui; \
	  docker rm -f $$ui $$sim >/dev/null 2>&1 || true; \
	  echo "e2e-ui exit code $$rc"; exit $$rc

summary: ## Print URLs, generated credentials and report locations
	@./scripts/summary.sh

sim: ## Stream a scenario at 20x real time to watch the console (SCENARIO=rush_hour make sim)
	$(RUN) tools store-sim run --seed $(SEED) --scenario sim/scenarios/$(SCENARIO).yaml --out /work/live --sink kafka --speed 20

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps -a

down: ## Stop the stack (keeps data)
	$(COMPOSE) --profile tools down --remove-orphans

clean: ## Stop and delete all data, generated secrets and reports
	-$(COMPOSE) --profile tools down -v --remove-orphans
	rm -rf .work deploy/keycloak/generated .env

.PHONY: openapi-check dev-setup test-fast schema-compat test-it baseline-accept
dev-setup: ## One-time, no Docker: Python venv with sim + scorer, and web dependencies
	python3 -m venv .venv
	.venv/bin/pip install -q -e './sim[test]' -e './scorer[test]'
	cd web/ops-console && npm ci

test-it: ## Integration tests (Testcontainers: PostgreSQL 16, Redpanda); needs Docker and JDK 21
	cd services/event-core && mvn -B -ntp -DskipUTs verify

baseline-accept: ## Open a PR proposing the latest green main run's quality baseline (needs gh; RB-09)
	./scripts/baseline-accept.sh

schema-compat: ## Fail if a schema version would reject data an older version accepted
	.venv/bin/python scripts/schema-compat.py

test-fast: ## No Docker, under 3 min: Java unit tests, Python tests, web typecheck and tests
	.venv/bin/python scripts/schema-compat.py
	cd services/event-core && mvn -B -q -ntp -DskipITs verify
	cd services/rules-engine && mvn -B -q -ntp verify
	.venv/bin/pytest -q sim/tests scorer/tests scripts/tests
	cd web/ops-console && npm run -s typecheck && npm test --silent
