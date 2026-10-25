#!/usr/bin/env bash
# Single-use read of the picked PRs. Prints queue.json and moves it aside so the same
# picks can never be reviewed twice. Exit 1 = no fresh picks (missing or empty).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../config.env"

queue="$STATE_DIR/queue.json"
[[ -s "$queue" ]] || exit 1
mv "$queue" "$STATE_DIR/queue.used.json"
cat "$STATE_DIR/queue.used.json"
[[ "$(jq length "$STATE_DIR/queue.used.json")" != "0" ]]
