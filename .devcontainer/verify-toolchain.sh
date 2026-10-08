#!/usr/bin/env bash
# Milestone 0.1 gate: exits non-zero unless every tool the repo needs is present at the right version.
set -uo pipefail
fail=0

check() { # check <name> <command> <regex the output must match>
  local name=$1 cmd=$2 want=$3 out
  if ! out=$(bash -c "$cmd" 2>&1); then
    printf 'FAIL  %-16s command failed: %s\n' "$name" "$cmd"; fail=1; return
  fi
  if grep -Eq -- "$want" <<<"$out"; then
    printf 'ok    %-16s %s\n' "$name" "$(grep -Em1 -- "$want" <<<"$out")"
  else
    printf 'FAIL  %-16s want /%s/, got: %s\n' "$name" "$want" "$(head -n1 <<<"$out")"; fail=1
  fi
}

# The docker-in-docker entrypoint starts dockerd asynchronously; give it up to 60 s.
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done

check java            'java -version'                               'version "21\.'
check maven           'mvn -v'                                      'Apache Maven 3\.9\.'
check maven-jdk       'mvn -v'                                      'Java version: 21\..*vendor: Eclipse Adoptium'
check node            'node -v'                                     '^v22\.'
check npm             'npm -v'                                      '^[0-9]+\.'
check python          'python3 -V'                                  'Python 3\.12\.'
check docker-daemon   'docker info --format "{{.ServerVersion}}"'   '^[0-9]+\.'
check docker-compose  'docker compose version'                      'v2\.'
check make            'make --version'                              'GNU Make'
check openssl         'openssl version'                             'OpenSSL 3\.'
check git-lfs         'git lfs version'                             'git-lfs/3\.'
check claude-code     'claude --version'                            '[0-9]+\.[0-9]+\.[0-9]+'
# Minimum proven: make all is green on a 2-core / 8 GB Codespace (the kernel reports ~7 GiB).
check cpus            'nproc'                                       '^([2-9]|[1-9][0-9]+)$'
check memory          "awk '/MemTotal/ {print int(\$2/1024/1024) \" GiB\"}' /proc/meminfo" '^([7-9]|[1-9][0-9]+) GiB'

if (( fail )); then echo "toolchain: FAIL"; exit 1; fi
echo "toolchain: OK"
