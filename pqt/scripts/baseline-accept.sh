#!/usr/bin/env bash
# Milestone Q3: propose the latest green main run's quality baseline as a pull request.
# Never commits to main and never merges: a maintainer reviews the PR (docs/runbooks/RB-09-baseline-update.md).
#
# Usage (from the project folder): make baseline-accept [RUN=<run id>]
# Needs: gh logged in with repo access, a clean working tree.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required" >&2; exit 2; }
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree not clean; commit or stash first" >&2
  exit 2
fi

run="${RUN:-}"
if [ -z "$run" ]; then
  run="$(gh run list --workflow ci.yml --branch main --event push --status success -L 1 \
          --json databaseId -q '.[0].databaseId')"
fi
[ -n "$run" ] || { echo "no successful ci run on main found" >&2; exit 2; }
sha="$(gh run view "$run" --json headSha -q '.headSha')"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
gh run download "$run" -n baseline-candidate -D "$tmp"
candidate="$tmp/baseline.candidate.json"
[ -f "$candidate" ] || { echo "run $run has no baseline-candidate artifact" >&2; exit 2; }

git fetch -q origin main
set +e
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
prefix="$(git rev-parse --show-prefix)"   # project folder inside the repo, e.g. "pqt/"
git show "origin/main:${prefix}scorer/baseline.json" > "$tmp/main-baseline.json"
PYTHONPATH=scorer "$PY" -m pqt_scorer.baseline_diff "$tmp/main-baseline.json" \
  "$candidate" --source "ci run $run on main@${sha:0:7}" --markdown "$tmp/body.md"
rc=$?
set -e
case "$rc" in
  0) echo "nothing to propose"; exit 0 ;;
  3) ;;
  *) echo "could not compare the baselines" >&2; exit "$rc" ;;
esac

branch="chore/baseline-${run}"
git switch -c "$branch" origin/main
# Same layout the scorer writes (indent 2, sorted keys), so the diff shows only real changes.
"$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); open(sys.argv[2], "w").write(json.dumps(d, indent=2, sort_keys=True) + "\n")' \
  "$candidate" scorer/baseline.json
git add scorer/baseline.json
git commit -m "chore(scorer): propose quality baseline from ci run $run (main@${sha:0:7})"
git push -u origin "$branch"
gh pr create --base main --head "$branch" \
  --title "chore(scorer): quality baseline from ci run $run" --body-file "$tmp/body.md"
echo "Opened a pull request. Review the table, then merge it yourself if the changes are intended."
