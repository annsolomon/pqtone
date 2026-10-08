#!/usr/bin/env bash
# Same as `make all`, for machines without make.
set -euo pipefail
cd "$(dirname "$0")"
command -v make >/dev/null 2>&1 || { echo "install make (e.g. sudo apt-get install -y make)" >&2; exit 1; }
exec make all "$@"
