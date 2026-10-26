#!/usr/bin/env python3
"""Drop state.json entries for PRs that are merged or closed. No AI, no tokens.

Usage: prune_state.py <state.json>

Only entries older than MIN_AGE_DAYS are looked up, so a run makes few gh calls. If a lookup
fails the entry stays: a leftover entry costs nothing, a wrong delete means a repeat review.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MIN_AGE_DAYS = 7
DONE_STATES = {"MERGED", "CLOSED"}


def pr_state(key):
    repo, number = key.rsplit("#", 1)
    try:
        result = subprocess.run(
            ["gh", "pr", "view", number, "-R", repo, "--json", "state", "--jq", ".state"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def prune(state, get_state, now):
    cutoff = now - timedelta(days=MIN_AGE_DAYS)
    kept = {}
    for key, entry in state.items():
        try:
            old = datetime.fromisoformat(entry["at"]) < cutoff
        except (KeyError, ValueError, TypeError):
            old = False
        if old and get_state(key) in DONE_STATES:
            continue
        kept[key] = entry
    return kept


def main():
    path = pathlib.Path(sys.argv[1])
    state = json.loads(path.read_text())
    kept = prune(state, pr_state, datetime.now(timezone.utc))
    if kept != state:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(kept, indent=2))
        os.replace(tmp, path)


if __name__ == "__main__":
    main()
