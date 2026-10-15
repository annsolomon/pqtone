#!/usr/bin/env bash
# Tier 2, milestone A4: the cable test. Run against a stack that is up (make up).
#
#   1. Start a simulated RTSP camera (MediaMTX, synthetic test pattern) and the edge agent.
#   2. Cut the agent's link to the gateway for CUT_SECONDS (docker network disconnect).
#   3. During the cut: kill -9 the agent mid-stream, restart it; stop the camera for 15 s, restart it.
#   4. Reconnect, wait for the outbox to drain, stop the agent.
#   5. Check in PostgreSQL that every event the agent stored (every per-camera sequence, first to last)
#      is present exactly once, and that the camera came back within 10 s.
#
# X1 B8 rides along: a simulated trading day is exported as yesterday's billing-system CSVs, and the file
# connector imports them on the edge box into the same outbox while the link is down. After the drain the
# billing source must be complete too (0 missing, 0 duplicate), a second import of the same files must add
# nothing, the daily reconcile over what event-core stored must pass and match the simulator's truth, and the
# console's sales and payments for that day must show the same figures.
#
# X1 B5f rides along too: the connector service (`pqt-connectors run`) runs next to the agent with a push API and a
# POS database source. While the link is down the shop's POS posts bills to the push API and its database gets
# more; after the drain both sources must be complete (0 missing, 0 duplicate), a repeated post must add nothing,
# the service must report both sources healthy, and the agent's heartbeat must carry that health to the console.
# Those bills are dated the day before the B8 day, so the B8 figures stay exact.
#
# CUT_SECONDS defaults to 120 for CI; the plan's full test is `make chaos-edge CUT_SECONDS=1800`.
# A full disk is covered by the outbox unit tests (OutboxFull); a clock jump needs libfaketime and is not
# simulated here (A5 reports clock offset instead).
set -Eeuo pipefail
cd "$(dirname "$0")/.."

CUT=${CUT_SECONDS:-120}
WARM=${WARM_SECONDS:-40}
DRAIN=${DRAIN_TIMEOUT_SECONDS:-300}
COMPOSE=${COMPOSE:-docker compose --env-file .env -f deploy/compose/docker-compose.yml}
EDGE="$COMPOSE --profile edge"
W=.work/edge
log() { printf '[chaos-edge %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
# Any failing command names itself, so a red run says where it stopped.
trap 'rc=$?; log "FAILED (exit $rc) at line $LINENO: $BASH_COMMAND"' ERR
status() { cat "$W/state/status.json" 2>/dev/null || echo '{}'; }
jq_py() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

rm -rf "$W" && mkdir -p "$W/config" "$W/state" "$W/reports"
cp edge/chaos/agent.yaml edge/chaos/cam-01.yaml "$W/config/"
(
  umask 077
  grep '^PQT_EDGE_CLIENT_SECRET=' .env | cut -d= -f2- > "$W/config/client_secret"
  head -c 48 /dev/urandom | base64 | tr -d '\n' > "$W/config/track_secret"
  printf 'rtsp://cam-sim:8554/cam1' > "$W/config/cam-01.url"
)

# X1 B8: yesterday's trading day (India time) as billing-system exports, and the box's store key.
BILL_DAY=$(TZ=Asia/Kolkata date -d yesterday +%F)
log "billing: simulating a trading day and exporting it as the billing system's CSVs for $BILL_DAY"
$COMPOSE run --rm --no-deps tools store-sim run --seed "${BILLING_SEED:-42}" --scenario sim/scenarios/billing_day.yaml \
  --out /work/edge/billing-run >/dev/null
$COMPOSE run --rm --no-deps tools store-sim export-billing --run /work/edge/billing-run --out /work/edge/config/billing \
  --shift-to "$BILL_DAY"
connector() { $EDGE run --rm --no-deps -T --entrypoint pqt-connectors edge-agent "$@"; }
billing_import() {
  connector import --profile /app/profiles/generic-csv.yaml --store store-001 --key /var/lib/pqt-edge/billing.key \
    --state /var/lib/pqt-edge/connector-state.db --outbox /var/lib/pqt-edge/outbox.db --schemas /app/schemas \
    --bills /config/billing/bills.csv --payments /config/billing/payments.csv --movements /config/billing/movements.csv \
    --stock /config/billing/stock.csv --day-totals /config/billing/day.csv --report "/var/lib/pqt-edge/$1"
}

log "building and starting the simulated camera, the edge agent and the connector service"
$EDGE build edge-agent
connector init-key --key /var/lib/pqt-edge/billing.key
log "connectors: a push client for the shop's POS, and the POS database"
# X1 B5f: a push client for the shop's POS (its token stays in the state folder, mode 600) and the POS database.
SRC_DAY=$(TZ=Asia/Kolkata date -d '2 days ago' +%F)
cp edge/chaos/sources.yaml edge/chaos/chaos-db.yaml "$W/config/"
token=$(connector push-client add --clients /var/lib/pqt-edge/push-clients.yaml --id chaos-pos \
  --profile /app/profiles/canonical.yaml | tail -n 1)
( umask 077; printf '%s' "$token" > "$W/state/push-token" )
unset token
$COMPOSE run --rm --no-deps tools python scripts/chaos_pos.py db --path /work/edge/state/pos.db --day "$SRC_DAY" --count 10
pos_push() { $EDGE exec -T connectors python - push --url http://127.0.0.1:8787 --token-file /var/lib/pqt-edge/push-token \
  --day "$SRC_DAY" --count 30 < scripts/chaos_pos.py; }
$EDGE up -d cam-sim edge-agent connectors
agent=$($EDGE ps -q edge-agent)
net="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$agent" | tr ' ' '\n' | grep '_edge$')"
log "agent container ${agent:0:12} on network $net"

camera_logs() { log "--- cam-sim logs ---"; $EDGE logs --no-color --tail 60 cam-sim | sed 's/^/[cam-sim] /'; }
probe_camera() {
  $EDGE exec -T cam-sim ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height \
    -of csv=p=0 rtsp://localhost:8554/cam1 2>/dev/null || true
}

# A1: the simulated camera answers as RTSP with decodable frames (a non-zero size needs a decoded JPEG,
# so a publisher whose packets never arrive does not pass).
probe=""
for _ in $(seq 1 20); do
  probe=$(probe_camera)
  [[ "$probe" =~ ^[a-z0-9]+,[1-9][0-9]*,[1-9][0-9]*$ ]] && break
  sleep 1
done
[[ "$probe" =~ ^[a-z0-9]+,[1-9][0-9]*,[1-9][0-9]*$ ]] || {
  log "ffprobe could not read frames from rtsp://cam-sim:8554/cam1 (got '${probe}')"; camera_logs; exit 1; }
log "ffprobe rtsp://cam-sim:8554/cam1 -> $probe"

sleep "$WARM"
sent0=$(status | jq_py 'd.get("uplink", {}).get("sent", 0)')
frames0=$(status | jq_py 'd.get("cameras", [{}])[0].get("frames", 0)')
log "warm-up: $frames0 frames, $sent0 events sent"
[ "$frames0" -gt 0 ] || {
  log "no frames from the camera (ffprobe now: '$(probe_camera)')"; camera_logs
  log "--- edge-agent logs ---"; $EDGE logs --no-color --tail 40 edge-agent; exit 1; }
[ "$sent0" -gt 0 ] || { log "nothing reached event-core before the cut"; $EDGE logs --tail 80 edge-agent; exit 1; }

log "CUT: disconnecting the agent from the gateway for ${CUT}s"
docker network disconnect "$net" "$agent"
t0=$(date +%s)

sleep $((CUT / 3))
log "kill -9 the agent mid-stream, then restart it (still cut off)"
docker kill -s KILL "$agent" >/dev/null
$EDGE start edge-agent >/dev/null
sleep 5

log "billing: the file connector imports yesterday's exports into the outbox while the link is down"
billing_import billing-import.json
log "billing: $(jq_py 'str(d["events"]) + " events, " + str(d["bills"]["new"]) + " bills, " + str(len(d["errors"])) + " rows reported"' < "$W/state/billing-import.json")"
log "billing: the shop's POS posts 30 bills to the push API and its database gets 10 more (link still down)"
pushed=$(pos_push)
log "billing: push API answered $pushed"
[ "$(echo "$pushed" | jq_py 'd["events"] > 0 and d["failed"] == 0 and d["errors"] == 0')" = "True" ] || {
  log "the push API did not take the POS's bills"; $EDGE logs --no-color --tail 40 connectors; exit 1; }
$COMPOSE run --rm --no-deps tools python scripts/chaos_pos.py db --path /work/edge/state/pos.db --day "$SRC_DAY" --count 20 >/dev/null

sleep $((CUT / 3))
log "camera outage: stopping the simulated camera for 15 s"
$EDGE stop cam-sim >/dev/null
sleep 15
before=$(status | jq_py 'd.get("cameras", [{}])[0].get("frames", 0)')
$EDGE start cam-sim >/dev/null
started=$(date +%s.%N)
recovery=""
for _ in $(seq 1 60); do
  sleep 0.5
  now=$(status | jq_py 'd.get("cameras", [{}])[0].get("frames", 0)')
  if [ "$now" -gt "$before" ]; then recovery=$(python3 -c "import time; print(round(time.time() - $started, 1))"); break; fi
done
log "camera back after ${recovery:-more than 30}s"

left=$((CUT - ($(date +%s) - t0)))
[ "$left" -gt 0 ] && sleep "$left"
pending=$(status | jq_py 'd.get("outbox", {}).get("pending", 0)')
log "RECONNECT after $(( $(date +%s) - t0 ))s; $pending events waiting in the outbox"
docker network connect "$net" "$agent"

deadline=$(( $(date +%s) + DRAIN ))
while :; do
  pending=$(status | jq_py 'd.get("outbox", {}).get("pending", -1)')
  [ "$pending" = "0" ] && break
  if [ "$(date +%s)" -gt "$deadline" ]; then
    log "outbox did not drain ($pending pending); agent status: $(status | jq_py 'json.dumps({k: d.get(k) for k in ("uplink", "outbox", "clockOffsetMs", "alarms")})')"
    $EDGE logs --no-color --tail 200 edge-agent | grep -v '"msg":"status ' | tail -n 25 | sed 's/^/[chaos-edge agent] /'
    break
  fi
  sleep 2
done
log "billing: importing the same files again (must add nothing)"
billing_import billing-reimport.json
log "billing: second import $(jq_py 'str(d["events"]) + " events, " + str(d["bills"]["unchanged"]) + " bills unchanged"' < "$W/state/billing-reimport.json")"
log "billing: the POS posts the same bills again (must add nothing)"
again=$(pos_push)
log "billing: push API answered $again"
[ "$(echo "$again" | jq_py 'd["events"] == 0 and d["failed"] == 0')" = "True" ] || { log "a repeated post added events"; exit 1; }
health=$(cat "$W/state/connectors-status.json" 2>/dev/null || echo '{}')
log "billing: connector service health $(echo "$health" | jq_py 'json.dumps([{k: s.get(k) for k in ("id", "ok", "failures", "events")} for s in d.get("sources", [])])')"
[ "$(echo "$health" | jq_py 'len(d.get("sources", [])) == 2 and all(s["ok"] and s["events"] > 0 for s in d["sources"])')" = "True" ] || {
  log "a connector source is not healthy"; $EDGE logs --no-color --tail 60 connectors; exit 1; }
log "stopping the agent, then the connector service"
$EDGE stop -t 35 edge-agent >/dev/null  # cameras stop, then up to 10 s of draining (drainOnStopSeconds)
$EDGE stop -t 15 connectors >/dev/null
status > "$W/reports/status-final.json"
python3 - "$W/reports/run.json" "$CUT" "${recovery:-null}" <<'PY'
import json, sys
json.dump({"cutSeconds": int(sys.argv[2]), "killedMidStream": True, "cameraRecoverySeconds": None if sys.argv[3] == "null" else float(sys.argv[3])},
          open(sys.argv[1], "w"))
PY
$COMPOSE run --rm --no-deps tools python scripts/chaos_edge_check.py --outbox /work/edge/state/outbox.db \
  --run /work/edge/reports/run.json --out /work/edge/reports/chaos-edge.json --require-source urn:pqt:edge:store-001:pos-file \
  --require-source urn:pqt:edge:store-001:pos-custom --require-source urn:pqt:edge:store-001:pos-chaos-db

log "billing: reconcile and ground truth over what event-core stored"
$COMPOSE run --rm --no-deps tools python scripts/billing_e2e_check.py --run /work/edge/billing-run --day "$BILL_DAY" \
  --source urn:pqt:edge:store-001:pos-file --import-report /work/edge/state/billing-import.json \
  --reimport-report /work/edge/state/billing-reimport.json --out /work/edge/reports/billing-e2e.json
log "billing: the console's sales and payments for $BILL_DAY"
$COMPOSE run --rm -e PQT_BILLING_E2E=/work/edge/reports/billing-e2e.json e2e-http \
  python -m pytest -q -p no:cacheprovider tests/http/test_billing_pipeline.py
