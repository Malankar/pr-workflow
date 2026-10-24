#!/usr/bin/env bash
# One-time setup. Run from inside the claude-automations folder.
set -euo pipefail
cd "$(dirname "$0")"

for tool in gh jq python3 git npx; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool"; exit 1; }
done
gh auth status >/dev/null 2>&1 || { echo "Run: gh auth login"; exit 1; }

# This folder is its own repo: Orca automations point at it, and its CLAUDE.md,
# settings and skills apply only to sessions started here, never to your normal chats.
[[ -d .git ]] || git init --quiet

chmod +x .claude/skills/pr-autoreview/scripts/* pr-inbox/check_my_prs.py global/pr-brief/scripts/*

# pr-brief is interactive, so it's the one piece installed globally.
mkdir -p "$HOME/.claude/skills"
rm -rf "$HOME/.claude/skills/pr-brief"
cp -R global/pr-brief "$HOME/.claude/skills/pr-brief"

echo "Done. Two installs left, both need you to pick options:"
echo
echo "  1. Matt Pocock's skills (global, you'll want code-review in normal sessions too):"
echo "       npx skills add mattpocock/skills -g"
echo
echo "  2. Caveman, in THIS repo only (no -g), pick the caveman skill and Claude Code:"
echo "       npx skills add JuliusBrussee/caveman"
echo "     Don't use 'npx @caveman-ai/cli': it caveman's every claude session on your machine."
echo
echo "Then: bash .claude/skills/pr-autoreview/scripts/find-prs.sh   (check the review queue)"
echo "      python3 pr-inbox/check_my_prs.py                       (first run marks old comments read)"
