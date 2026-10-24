#!/usr/bin/env bash
# Usage: prepare-pr.sh <owner/repo> <number>
# Checks the PR out into its own worktree and writes a context bundle.
# Prints one JSON object with every path the skill needs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../config.env"

repo="$1"
number="$2"
slug="${repo//\//__}"
clone="$STATE_DIR/repos/$slug"
wt="$STATE_DIR/worktrees/${slug}__$number"
bundle="$STATE_DIR/runs/$(date +%Y-%m-%d)/${slug}__$number"
mkdir -p "$STATE_DIR/repos" "$STATE_DIR/worktrees" "$bundle"

# Blobless clone once per repo: fast, and file contents download only when needed.
if [[ ! -d "$clone/.git" ]]; then
  gh repo clone "$repo" "$clone" -- --filter=blob:none --quiet
fi

gh pr view "$number" -R "$repo" \
  --json number,title,body,url,author,baseRefName,headRefName,headRefOid,files > "$bundle/meta.json"
base="$(jq -r .baseRefName "$bundle/meta.json")"
head_sha="$(jq -r .headRefOid "$bundle/meta.json")"

git -C "$clone" fetch --quiet origin \
  "+refs/heads/$base:refs/remotes/origin/$base" \
  "+refs/pull/$number/head:refs/remotes/pr/$number"

if [[ -d "$wt" ]]; then git -C "$clone" worktree remove --force "$wt"; fi
git -C "$clone" worktree prune
git -C "$clone" worktree add --detach --quiet "$wt" "$head_sha"

# Three-dot diff = only what this PR changed, compared to its merge-base.
git -C "$wt" diff "origin/$base...HEAD" > "$bundle/pr.diff"
git -C "$wt" log --oneline "origin/$base..HEAD" > "$bundle/commits.txt"

# Ticket keys like ABC-123 from the title, branch name and description, used to fetch the spec.
jq -r '[.title, .headRefName, (.body // "")] | join("\n")' "$bundle/meta.json" \
  | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | sort -u > "$bundle/tickets.txt" || true

jq -n \
  --arg repo "$repo" --argjson number "$number" \
  --arg clone "$clone" --arg worktree "$wt" --arg bundle "$bundle" \
  --arg base "$base" --arg headSha "$head_sha" \
  '{repo: $repo, number: $number, clone: $clone, worktree: $worktree, bundle: $bundle,
    base: $base, fixedPoint: ("origin/" + $base), headSha: $headSha}'
