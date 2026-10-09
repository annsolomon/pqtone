#!/usr/bin/env bash
# Waits for long-running services to be healthy and one-shot jobs to have exited 0.
# Prints the relevant logs and fails fast when something breaks.
set -euo pipefail
COMPOSE=${COMPOSE:?}
TIMEOUT=${TIMEOUT:-600}
ONESHOT=(certs flyway redpanda-init)
SERVICES=(postgres redpanda keycloak event-core rules-engine gateway)
deadline=$(( $(date +%s) + TIMEOUT ))

state() {
  local id; id=$($COMPOSE ps -a -q "$1" 2>/dev/null | head -n1)
  [ -z "$id" ] && { echo missing; return; }
  docker inspect -f '{{.State.Status}}|{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id"
}

fail() {
  echo "✗ $1" >&2
  $COMPOSE logs --no-color --tail=80 "$2" >&2 || true
  exit 1
}

for svc in "${ONESHOT[@]}"; do
  while :; do
    IFS='|' read -r status code _ <<<"$(state "$svc")"
    if [ "$status" = exited ]; then
      [ "$code" = 0 ] && { echo "✓ $svc completed"; break; } || fail "$svc exited with $code" "$svc"
    fi
    [ "$(date +%s)" -gt "$deadline" ] && fail "$svc did not finish in ${TIMEOUT}s" "$svc"
    sleep 2
  done
done

for svc in "${SERVICES[@]}"; do
  while :; do
    IFS='|' read -r status code health <<<"$(state "$svc")"
    [ "$health" = healthy ] && { echo "✓ $svc healthy"; break; }
    if [ "$status" = exited ] || [ "$status" = dead ]; then fail "$svc stopped (exit $code)" "$svc"; fi
    [ "$(date +%s)" -gt "$deadline" ] && fail "$svc not healthy after ${TIMEOUT}s (status $status, health $health)" "$svc"
    sleep 3
  done
done
