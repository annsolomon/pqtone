#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
# CI logs are readable by everyone with repo access: never print the generated secrets there.
if [ -n "${CI:-}" ]; then
  for v in PQT_VIEWER_PASSWORD PQT_OPERATOR_PASSWORD PQT_REVIEWER_PASSWORD PQT_ADMIN_PASSWORD KC_ADMIN_PASSWORD GRAFANA_ADMIN_PASSWORD; do
    printf -v "$v" '%s' "(in .env)"
  done
fi
line() { printf '%s\n' "------------------------------------------------------------------------"; }
line
echo "PQT Tier 1 is running"
line
echo "Console         ${PQT_PUBLIC_URL}"
echo "  viewer        viewer   / ${PQT_VIEWER_PASSWORD}"
echo "  operator      operator / ${PQT_OPERATOR_PASSWORD}"
echo "  reviewer      reviewer / ${PQT_REVIEWER_PASSWORD}"
echo "  admin         admin    / ${PQT_ADMIN_PASSWORD}"
echo "Keycloak admin  ${PQT_PUBLIC_URL}/auth/admin   kcadmin / ${KC_ADMIN_PASSWORD}"
echo "Grafana         http://localhost:3000            admin / ${GRAFANA_ADMIN_PASSWORD}"
echo "Prometheus      http://localhost:9090"
echo "Jaeger          http://localhost:16686"
line
echo "Reports         .work/reports/   (offline/score.md, e2e/score.md, junit XML)"
for f in .work/reports/offline/score.md .work/reports/e2e/score.md; do
  [ -f "$f" ] && { echo; sed -n '1,4p' "$f"; }
done
line
echo "Watch it live:  make sim        Stop: make down        Wipe data: make clean"
