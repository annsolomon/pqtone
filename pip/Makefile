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
export DOCKER_BUILDKIT := 1
export COMPOSE_DOCKER_CLI_BUILD := 1

.PHONY: all help bootstrap build offline up e2e e2e-pipeline e2e-http e2e-db e2e-ui summary sim down clean logs ps

all: bootstrap build offline up e2e summary ## Build + unit tests, offline quality gate, start the stack, live end-to-end tests, summary

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

e2e: e2e-pipeline e2e-http e2e-db e2e-ui ## Live end-to-end tests against the running stack

e2e-pipeline: ## Simulate SCENARIO through Kafka and score the incidents stored in PostgreSQL
	rm -rf "$(WORK)/e2e" "$(WORK)/reports/e2e" && mkdir -p "$(WORK)/reports"
	$(RUN) tools store-sim run --seed $(SEED) --scenario sim/scenarios/$(SCENARIO).yaml --out /work/e2e --sink kafka
	$(RUN) tools pip-scorer e2e --case-dir /work/e2e --out /work/reports/e2e --timeout 600

e2e-http: ## Security and API tests through the gateway (auth, RBAC, CSRF, ingest contract, review flow)
	$(COMPOSE) run --rm e2e-http

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

.PHONY: dev-setup test-fast
dev-setup: ## One-time, no Docker: Python venv with sim + scorer, and web dependencies
	python3 -m venv .venv
	.venv/bin/pip install -q -e './sim[test]' -e './scorer[test]'
	cd web/ops-console && npm ci

test-fast: ## No Docker, under 3 min: Java unit tests, Python tests, web typecheck and tests
	cd services/event-core && mvn -B -q -ntp verify
	cd services/rules-engine && mvn -B -q -ntp verify
	.venv/bin/pytest -q sim/tests scorer/tests
	cd web/ops-console && npm run -s typecheck && npm test --silent
