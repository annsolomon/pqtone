#!/usr/bin/env bash
# Runs once when the Codespace container is created.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v make >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq make
fi
git lfs install
bash .devcontainer/verify-toolchain.sh
