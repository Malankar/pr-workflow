#!/usr/bin/env python3
"""fix-check: did each fix session touch the code a reviewer comment pointed at? Read-only, no AI, no tokens.

For every finding in state "queued" (picked for fixing), diffs that file in the PR's fix worktree against the
commit the comment was judged at (committed and uncommitted changes both count). Per finding it says
"touched near line N", "file changed, but not near line N" or "not touched". It can't say whether the change
is correct: you still read the diff.
Run:  fix_check.py [owner/repo#123]
"""
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ask_fixes import find_orca_repo, find_worktree  # noqa: E402
from check_my_prs import load_config  # noqa: E402

NEAR = 5  # lines of slack around the commented line


def hunks(diff):
    """Old-file (start, end) line ranges of every hunk in a -U0 diff."""
    return [(int(m[1]), int(m[1]) + max(int(m[2] or 1), 1) - 1)
            for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M)]


def classify(diff, line):
    if not diff.strip():
        return "not touched"
    if any(start <= line + NEAR and end >= line - NEAR for start, end in hunks(diff)):
        return f"touched near line {line}"
    return f"file changed, but not near line {line}"


def check_finding(worktree, head_sha, where):
    m = re.match(r"(.+?):(\d+)", where or "")
    if not m:
        return "no file:line to check"
    result = subprocess.run(["git", "-C", worktree, "diff", "-U0", head_sha, "--", m[1]],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return "can't compare (the commented commit isn't in the worktree)"
    return classify(result.stdout, int(m[2]))


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    state = pathlib.Path(load_config()["STATE_DIR"])
    for path in sorted((state / "briefs").glob("*/*/findings.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        queued = [f for f in data.get("findings", []) if f.get("state") == "queued"]
        if not queued or (only and data.get("pr") != only):
            continue
        repo_name, number = data["pr"].split("#")[0].split("/")[1], data["pr"].split("#")[1]
        name = f"fix-{repo_name}-{number}"
        print(f"{data['pr']}  (worktree {name})")
        try:
            repo = find_orca_repo(repo_name)
            worktree = find_worktree(repo["id"], name) if repo else None
        except Exception as err:
            print(f"  couldn't ask Orca: {type(err).__name__}")
            continue
        if not worktree:
            print("  no fix worktree yet")
            continue
        for f in queued:
            comment = (f.get("comment") or "")[:60]
            print(f"  {f.get('where')}: {check_finding(worktree['path'], data['headSha'], f.get('where'))}"
                  f'  ("{comment}")')


if __name__ == "__main__":
    main()
