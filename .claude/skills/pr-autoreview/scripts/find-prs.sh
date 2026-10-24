#!/usr/bin/env bash
# Deterministic PR discovery. No AI, no tokens.
# Prints a JSON array of PRs to review and saves it to $STATE_DIR/queue.json.
# Exit 0 = work to do, exit 1 = nothing to do.
# Use it as the Orca precheck so empty runs never start Claude.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../config.env"

mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/state.json"
[[ -f "$STATE_FILE" ]] || echo '{}' > "$STATE_FILE"

query="is:pr is:open -is:draft review-requested:@me -reviewed-by:@me -author:app/dependabot"
for user in $SKIP_IF_REVIEWED_BY; do
  query+=" -reviewed-by:$user"
done
if [[ -n "${ORG:-}" ]]; then query+=" org:$ORG"; fi

me="$(gh api user --jq .login)"
candidates="$(gh search prs "$query" --limit 50 --json repository,number,title,url,author)"

queue="[]"
while IFS=$'\t' read -r repo number title url; do
  [[ -z "$repo" ]] && continue

  head_sha="$(gh pr view "$number" -R "$repo" --json headRefOid --jq .headRefOid)"

  # Already reviewed this exact commit (covers the window before you submit a pending review).
  seen_sha="$(jq -r --arg k "$repo#$number" '.[$k].sha // ""' "$STATE_FILE")"
  if [[ "$seen_sha" == "$head_sha" ]]; then continue; fi

  # GitHub allows one pending review per person per PR. If yours is still open, leave it alone.
  pending="$(gh api "repos/$repo/pulls/$number/reviews?per_page=100" \
    --jq "[.[] | select(.state == \"PENDING\" and .user.login == \"$me\")] | length")"
  if [[ "$pending" != "0" ]]; then continue; fi

  queue="$(jq --arg r "$repo" --argjson n "$number" --arg t "$title" --arg u "$url" --arg s "$head_sha" \
    '. + [{repo: $r, number: $n, title: $t, url: $u, headSha: $s}]' <<<"$queue")"
done < <(jq -r '.[]
  | select((.author.is_bot // false) | not)
  | select((.author.login // "") | test("\\[bot\\]$") | not)
  | [.repository.nameWithOwner, .number, .title, .url] | @tsv' <<<"$candidates")

queue="$(jq --argjson max "$MAX_PRS_PER_RUN" '.[:$max]' <<<"$queue")"
echo "$queue" > "$STATE_DIR/queue.json"
echo "$queue"
[[ "$(jq length <<<"$queue")" -gt 0 ]]
