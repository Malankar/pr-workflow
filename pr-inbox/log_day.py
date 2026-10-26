#!/usr/bin/env python3
"""log-day: appends what I did today to ~/.pr-inbox/daily-log/YYYY-MM-DD.md. No AI, no tokens, files only.

For every PR with a brief in STATE_DIR/briefs it records:
  - my replies/comments dated today (GitHub gives comment dates),
  - threads I resolved since the last run (GitHub gives no resolve date, so each run diffs against
    STATE_DIR/resolved-snapshot.json; the first run lists everything already resolved by me as "date unknown"),
  - findings I picked in the Telegram fix picker today (askedAt is today).
Run it whenever; each run re-renders today's file. Test without writing:  log_day.py --dry-run
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_my_prs import gh, load_config  # noqa: E402

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes { id isResolved resolvedBy { login } comments(first: 100) { nodes { author { login } createdAt } } }
      }
    }
  }
}"""


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def scan_pr(pr, me, today):
    """(replies from me today, ids of threads I resolved) for one PR."""
    repo, number = pr.rsplit("#", 1)
    owner, name = repo.split("/")
    data = json.loads(gh(["api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"owner={owner}",
                          "-f", f"name={name}", "-F", f"number={number}"]))
    replies, resolved = 0, []
    for t in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        replies += sum(1 for c in t["comments"]["nodes"]
                       if (c["author"] or {}).get("login") == me
                       and datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")).astimezone().date() == today)
        if t["isResolved"] and (t["resolvedBy"] or {}).get("login") == me:
            resolved.append(t["id"])
    return replies, resolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    state = pathlib.Path(cfg["STATE_DIR"])
    today = datetime.now().astimezone().date()
    me = gh(["api", "user", "--jq", ".login"]).strip()

    snap_path = state / "resolved-snapshot.json"
    snap = read_json(snap_path, None)
    first_run = snap is None
    snap = snap or {}
    day_path = state / "daily-log" / f"{today}.json"
    day = read_json(day_path, {"prs": {}})

    for path in sorted((state / "briefs").glob("*/*/findings.json")):
        data = read_json(path, {})
        pr = data.get("pr")
        if not pr:
            continue
        entry = day["prs"].setdefault(pr, {"title": data.get("title", ""), "url": data.get("url", ""),
                                           "replies": 0, "resolved": 0, "resolvedUnknownDate": 0, "picked": 0})
        picked = [f for f in data.get("findings", []) if f.get("state") == "queued"
                  and datetime.fromtimestamp(float(f.get("askedAt", 0))).astimezone().date() == today]
        entry["picked"] = len(picked)
        try:
            replies, resolved = scan_pr(pr, me, today)
        except Exception as err:  # keep what was logged earlier today
            print(f"{pr}: GitHub check failed ({err})", file=sys.stderr)
            continue
        entry["replies"] = replies
        new = [t for t in resolved if t not in snap.get(pr, [])]
        entry["resolvedUnknownDate" if first_run else "resolved"] += len(new)
        snap[pr] = resolved

    lines = [f"# {today}", ""]
    for pr, e in day["prs"].items():
        if not (e["replies"] or e["resolved"] or e["resolvedUnknownDate"] or e["picked"]):
            continue
        parts = [f"{e['replies']} replies" if e["replies"] else "",
                 f"{e['resolved']} threads resolved" if e["resolved"] else "",
                 f"{e['resolvedUnknownDate']} threads resolved (date unknown, first run)" if e["resolvedUnknownDate"] else "",
                 f"{e['picked']} findings picked for fixing" if e["picked"] else ""]
        lines.append(f"- {pr} {e['title']}: {', '.join(p for p in parts if p)}")
    if len(lines) == 2:
        lines.append("Nothing recorded.")
    text = "\n".join(lines) + "\n"

    if args.dry_run:
        print(text)
        return
    day_path.parent.mkdir(parents=True, exist_ok=True)
    day_path.write_text(json.dumps(day, indent=1))
    day_path.with_suffix(".md").write_text(text)
    snap_path.write_text(json.dumps(snap))
    print(text)


if __name__ == "__main__":
    main()
