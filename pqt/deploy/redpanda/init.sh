#!/usr/bin/env bash
# Idempotent: SCRAM users, topics and least-privilege ACLs.
set -euo pipefail
: "${PQT_KAFKA_ADMIN_PASSWORD:?}" "${PQT_KAFKA_EVENTCORE_PASSWORD:?}" "${PQT_KAFKA_RULES_PASSWORD:?}" "${PQT_KAFKA_SIM_PASSWORD:?}"
RF="${PQT_KAFKA_REPLICATION:-1}"
ADMIN=(-X admin.hosts=redpanda:9644)
SU=(-X brokers=redpanda:9092 -X user=admin -X "pass=${PQT_KAFKA_ADMIN_PASSWORD}" -X sasl.mechanism=SCRAM-SHA-256)

until rpk cluster health "${ADMIN[@]}" 2>/dev/null | grep -Eq 'Healthy:\s+true'; do echo "waiting for redpanda"; sleep 2; done

user() {
  if ! rpk security user create "$1" -p "$2" --mechanism SCRAM-SHA-256 "${ADMIN[@]}" >/dev/null 2>&1; then
    rpk security user delete "$1" "${ADMIN[@]}" >/dev/null
    rpk security user create "$1" -p "$2" --mechanism SCRAM-SHA-256 "${ADMIN[@]}" >/dev/null
  fi
  echo "user $1 ready"
}
user admin        "$PQT_KAFKA_ADMIN_PASSWORD"
user event-core   "$PQT_KAFKA_EVENTCORE_PASSWORD"
user rules-engine "$PQT_KAFKA_RULES_PASSWORD"
user store-sim    "$PQT_KAFKA_SIM_PASSWORD"

# SCRAM credentials propagate asynchronously; wait until the superuser can authenticate.
until rpk cluster info "${SU[@]}" >/dev/null 2>&1; do echo "waiting for SASL"; sleep 1; done

topic() {
  local name=$1 parts=$2; shift 2
  if rpk topic describe "$name" "${SU[@]}" >/dev/null 2>&1; then
    echo "topic $name exists"
  else
    rpk topic create "$name" -p "$parts" -r "$RF" "$@" "${SU[@]}"
  fi
}
DAY=86400000
topic store.events.raw    12 -c retention.ms=$((7 * DAY))
topic store.events.v1     12 -c retention.ms=$((30 * DAY))
topic store.events.dlq     3 -c retention.ms=$((30 * DAY))
topic incidents.v1         6 -c retention.ms=$((90 * DAY))
topic incidents.shadow.v1  6 -c retention.ms=$((90 * DAY))
topic rules.heartbeat.v1   3 -c retention.ms=$DAY
# Milestone R4: the active rules document, one record per key; compaction keeps only the latest.
topic rules.config.v1      1 -c cleanup.policy=compact

acl() { rpk security acl create "$@" "${SU[@]}" >/dev/null; }

# store-sim: may only append to the raw topic.
acl --allow-principal User:store-sim --operation write,describe --topic store.events.raw
acl --allow-principal User:store-sim --operation idempotent_write --cluster

# event-core: the single gate. Reads raw + rules output, writes validated + DLQ.
acl --allow-principal User:event-core --operation read,describe \
    --topic store.events.raw --topic store.events.v1 --topic incidents.v1 --topic incidents.shadow.v1 --topic rules.heartbeat.v1
acl --allow-principal User:event-core --operation write,describe --topic store.events.v1 --topic store.events.dlq
acl --allow-principal User:event-core --operation read,describe --group event-core --resource-pattern-type prefixed
acl --allow-principal User:event-core --operation idempotent_write --cluster

# rules-engine: reads validated, writes incidents/heartbeat, owns its internal topics and transactions.
acl --allow-principal User:rules-engine --operation read,describe --topic store.events.v1 --topic rules.config.v1
acl --allow-principal User:rules-engine --operation write,describe --topic incidents.v1 --topic incidents.shadow.v1 --topic rules.heartbeat.v1
acl --allow-principal User:rules-engine --operation all --topic rules-engine --resource-pattern-type prefixed
acl --allow-principal User:rules-engine --operation all --group rules-engine --resource-pattern-type prefixed
acl --allow-principal User:rules-engine --operation all --transactional-id rules-engine --resource-pattern-type prefixed
acl --allow-principal User:rules-engine --operation idempotent_write,describe,describe_configs --cluster

echo "redpanda initialised"
