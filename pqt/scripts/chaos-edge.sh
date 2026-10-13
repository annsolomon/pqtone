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
# CUT_SECONDS defaults to 120 for CI; the plan's full test is `make chaos-edge CUT_SECONDS=1800`.
# A full disk is covered by the outbox unit tests (OutboxFull); a clock jump needs libfaketime and is not
# simulated here (A5 reports clock offset instead).
set -euo pipefail
cd "$(dirname "$0")/.."

CUT=${CUT_SECONDS:-120}
WARM=${WARM_SECONDS:-40}
DRAIN=${DRAIN_TIMEOUT_SECONDS:-300}
COMPOSE=${COMPOSE:-docker compose --env-file .env -f deploy/compose/docker-compose.yml}
EDGE="$COMPOSE --profile edge"
W=.work/edge
log() { printf '[chaos-edge %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
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

log "building and starting the simulated camera and the edge agent"
$EDGE build edge-agent
connector init-key --key /var/lib/pqt-edge/billing.key
$EDGE up -d cam-sim edge-agent
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
log "stopping the agent"
$EDGE stop -t 20 edge-agent >/dev/null
status > "$W/reports/status-final.json"
python3 - "$W/reports/run.json" "$CUT" "${recovery:-null}" <<'PY'
import json, sys
json.dump({"cutSeconds": int(sys.argv[2]), "killedMidStream": True, "cameraRecoverySeconds": None if sys.argv[3] == "null" else float(sys.argv[3])},
          open(sys.argv[1], "w"))
PY
$COMPOSE run --rm --no-deps tools python scripts/chaos_edge_check.py --outbox /work/edge/state/outbox.db \
  --run /work/edge/reports/run.json --out /work/edge/reports/chaos-edge.json --require-source urn:pqt:edge:store-001:pos-file

log "billing: reconcile and ground truth over what event-core stored"
$COMPOSE run --rm --no-deps tools python scripts/billing_e2e_check.py --run /work/edge/billing-run --day "$BILL_DAY" \
  --source urn:pqt:edge:store-001:pos-file --import-report /work/edge/state/billing-import.json \
  --reimport-report /work/edge/state/billing-reimport.json --out /work/edge/reports/billing-e2e.json
log "billing: the console's sales and payments for $BILL_DAY"
$COMPOSE run --rm -e PQT_BILLING_E2E=/work/edge/reports/billing-e2e.json e2e-http \
  python -m pytest -q -p no:cacheprovider tests/http/test_billing_pipeline.py
