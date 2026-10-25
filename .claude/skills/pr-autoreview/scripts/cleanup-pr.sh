#!/usr/bin/env bash
# Remove a PR's worktree once its review is done. Usage: cleanup-pr.sh <clone> <worktree>
set -euo pipefail
git -C "$1" worktree remove --force "$2"
