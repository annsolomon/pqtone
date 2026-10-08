#!/usr/bin/env bash
# Surface a report file as a GitHub error annotation, so a failure's details can be read
# from the PR's checks (and through the checks API) without downloading the raw job log.
# Usage: annotate.sh <title> <file> [max-lines]
set -euo pipefail
title=$1 file=$2 max=${3:-150}
if [ ! -s "$file" ]; then
  echo "annotate: $file is missing or empty"
  exit 0
fi
body=$(tail -n "$max" "$file" | cut -c1-400 | sed -e 's/\x1b\[[0-9;]*[A-Za-z]//g')
# Workflow-command escaping: % first, then CR and LF.
body=${body//'%'/'%25'}
body=${body//$'\r'/'%0D'}
body=${body//$'\n'/'%0A'}
title=${title//'%'/'%25'}
title=${title//','/'%2C'}
title=${title//':'/'%3A'}
echo "::error title=${title}::${body}"
