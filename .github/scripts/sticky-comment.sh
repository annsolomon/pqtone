#!/usr/bin/env bash
# Create or update one pull-request comment identified by a marker on its first line.
# Usage: sticky-comment.sh <pr-number> <body-file>
# Needs GH_TOKEN with pull-requests: write and GITHUB_REPOSITORY (both set in Actions).
set -euo pipefail

pr="$1"
body_file="$2"
marker="$(head -n 1 "$body_file")"
case "$marker" in
  "<!--"*"-->") ;;
  *) echo "the first line of $body_file must be an HTML comment marker" >&2; exit 2 ;;
esac

payload="$(python3 -c 'import json, sys; print(json.dumps({"body": open(sys.argv[1], encoding="utf-8").read()}))' "$body_file")"

# Only our own comments: the Actions bot, starting with the marker. Last one wins if there are several.
id="$(gh api --paginate "repos/$GITHUB_REPOSITORY/issues/$pr/comments" \
  --jq ".[] | select(.user.login == \"github-actions[bot]\" and (.body | startswith(\"$marker\"))) | .id" \
  | tail -n 1)"

if [ -n "$id" ]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$id" --input - <<<"$payload" --jq '.html_url'
  echo "updated comment $id"
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$pr/comments" --input - <<<"$payload" --jq '.html_url'
  echo "created a new comment"
fi
