#!/usr/bin/env python3
"""Post verified findings as one GitHub review.

Usage: post_review.py <bundle_dir>

Reads <bundle>/verified.json (written by the pr-verifier agent) and <bundle>/meta.json.
Only findings with verdict "confirmed" or "downgraded" are posted. A finding whose line
is not part of the diff can't be an inline comment, so it goes into the review body.
Posting is code, not prompt: Claude decides what to say, this script decides what ships.
"""
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
SEVERITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


def load_config():
    cfg = HERE.parent / "config.env"
    out = subprocess.run(
        ["bash", "-c", f'set -a; source "{cfg}"; env -0'],
        capture_output=True, check=True,
    ).stdout.decode()
    return dict(item.split("=", 1) for item in out.split("\0") if "=" in item)


def commentable_lines(diff_text):
    """Map path -> set of new-file line numbers GitHub accepts for RIGHT-side comments."""
    valid, path, new_line, in_header = {}, None, None, False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path, new_line, in_header = None, None, True
            continue
        if in_header:
            if line.startswith("+++ "):
                target = line[4:]
                path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
                if path:
                    valid.setdefault(path, set())
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                new_line, in_header = int(hunk.group(1)), False
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if path is None or new_line is None:
            continue
        if line.startswith("+") or line.startswith(" ") or line == "":
            valid[path].add(new_line)
            new_line += 1
        # "-" lines and "\ No newline" don't advance the new-file counter.
    return valid


def gh(args, stdin=None):
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def comment_body(f, tag):
    return f"**{f['severity']} · {f['axis']}**: {f['title']}\n\n{f['body'].strip()}\n\n<sub>{tag}</sub>"


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: post_review.py <bundle_dir>")
    bundle = pathlib.Path(sys.argv[1])
    cfg = load_config()
    dry_run = cfg.get("DRY_RUN", "true").lower() == "true"
    auto_submit = cfg.get("AUTO_SUBMIT", "false").lower() == "true"
    max_nits = int(cfg.get("MAX_NITS", "3"))
    tag = cfg.get("COMMENT_TAG", "AI-assisted review")
    state_dir = pathlib.Path(cfg["STATE_DIR"])

    meta = json.loads((bundle / "meta.json").read_text())
    verified = json.loads((bundle / "verified.json").read_text())
    repo = meta["url"].split("github.com/")[1].split("/pull/")[0]
    number, head_sha = meta["number"], meta["headRefOid"]

    keep = [f for f in verified.get("findings", []) if f.get("verdict") in ("confirmed", "downgraded")]
    keep = [f for f in keep if f.get("severity") in SEVERITY_ORDER and f.get("title") and f.get("body")]
    keep.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
    nits = [f for f in keep if f["severity"] == "P3"][:max_nits]
    keep = [f for f in keep if f["severity"] != "P3"] + nits

    valid = commentable_lines((bundle / "pr.diff").read_text())
    inline, general = [], []
    for f in keep:
        path, line = f.get("path"), f.get("line")
        if path and isinstance(line, int) and line in valid.get(path, set()):
            inline.append({"path": path, "line": line, "side": "RIGHT", "body": comment_body(f, tag)})
        else:
            general.append(f)

    counts = {s: sum(1 for f in keep if f["severity"] == s) for s in SEVERITY_ORDER}
    result = {"pr": f"{repo}#{number}", "headSha": head_sha, "posted": 0, "counts": counts,
              "rejected": len(verified.get("rejected", [])), "dryRun": dry_run}

    if not keep:
        result["status"] = "clean, nothing to post"
    else:
        body = [f"{len(keep)} verified finding(s): {counts['P1']} must fix, "
                f"{counts['P2']} should fix, {counts['P3']} nit."]
        if general:
            body.append("\n**Not tied to a changed line:**")
            for f in general:
                where = f" (`{f['path']}:{f['line']}`)" if f.get("path") and f.get("line") else ""
                body.append(f"- **{f['severity']} · {f['axis']}**: {f['title']}{where}. {f['body'].strip()}")
        body.append(f"\n<sub>{tag}</sub>")
        payload = {"commit_id": head_sha, "body": "\n".join(body), "comments": inline}
        if auto_submit:
            payload["event"] = "COMMENT"  # no event = PENDING review that only you can see
        (bundle / "payload.json").write_text(json.dumps(payload, indent=2))

        if dry_run:
            result["status"] = f"dry run, payload written to {bundle / 'payload.json'}"
        else:
            me = gh(["api", "user", "--jq", ".login"]).strip()
            reviews = json.loads(gh(["api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"]))
            if any(r["state"] == "PENDING" and r["user"]["login"] == me for r in reviews):
                result["status"] = "skipped: you already have a pending review on this PR"
                print(json.dumps(result))
                return
            gh(["api", "-X", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", "-"],
               stdin=json.dumps(payload))
            result["posted"] = len(keep)
            result["status"] = "posted as submitted comment" if auto_submit else "posted as pending review"

    if not dry_run:
        state_file = state_dir / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state[f"{repo}#{number}"] = {"sha": head_sha, "at": datetime.now(timezone.utc).isoformat()}
        state_file.write_text(json.dumps(state, indent=2))

    with open(state_dir / "log.jsonl", "a") as log:
        log.write(json.dumps({**result, "at": datetime.now(timezone.utc).isoformat()}) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
