#!/usr/bin/env bash
# Switch .env between the two ways of opening the console, then re-render the Keycloak realm.
#   codespaces: open the console at the Codespace's forwarded URL (browser-login e2e tests skip)
#   localhost:  what CI uses; needed for the Playwright test (C2) and the browser-login e2e tests
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-}
[ -f .env ] || ./scripts/bootstrap.sh
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
case "$mode" in
  codespaces)
    : "${CODESPACE_NAME:?not running inside a Codespace}"
    set_env PIP_PUBLIC_URL "https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
    set_env PIP_COOKIE_SECURE true
    set_env PIP_KC_SSL_REQUIRED none ;;
  localhost)
    set_env PIP_PUBLIC_URL http://localhost:8080
    set_env PIP_COOKIE_SECURE false
    set_env PIP_KC_SSL_REQUIRED external ;;
  *) echo "usage: scripts/mode.sh codespaces|localhost" >&2; exit 2 ;;
esac
rm -rf deploy/keycloak/generated
"${BOOTSTRAP:-./scripts/bootstrap.sh}"
grep -E '^(PIP_PUBLIC_URL|PIP_COOKIE_SECURE|PIP_KC_SSL_REQUIRED)=' .env
echo "Mode set to $mode. Restart the stack: make down && make up"
