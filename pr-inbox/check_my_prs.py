#!/usr/bin/env python3
"""pr-inbox: tell me about new comments on my open PRs. No AI, no tokens.

Run it every 2 hours, as an Orca precheck or from cron. It looks at every open PR you
authored and collects comments you haven't been told about yet:
  - inline review comments in threads that are still unresolved,
  - conversation comments,
  - reviews that approve, request changes, or carry a summary.
Your own comments and bots are skipped. Then it notifies you and writes ~/.pr-inbox/inbox.md.

Exit code: 1 unless AUTO_BRIEF=true and something is new. As an Orca precheck, exit 1 means
"skip the agent run", so in the default notify-only mode this never spends a token.
"""
import json
import pathlib
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          path
          line
          comments(first: 50) { nodes { id author { login } body url } }
        }
      }
    }
  }
}"""


def load_config():
    cfg = HERE / "config.env"
    out = subprocess.run(["bash", "-c", f'set -a; source "{cfg}"; env -0'],
                         capture_output=True, check=True).stdout.decode()
    return dict(item.split("=", 1) for item in out.split("\0") if "=" in item)


def gh(args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {result.stderr.strip()}")
    return result.stdout


def one_line(text, limit=90):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def collect(repo, number):
    """Every comment on one PR that could deserve a notification."""
    owner, name = repo.split("/")
    items = []
    data = json.loads(gh(["api", "graphql", "-f", f"query={THREADS_QUERY}",
                          "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"]))
    for thread in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        if thread["isResolved"]:
            continue
        where = f"{thread['path']}:{thread['line'] or '?'}"
        for c in thread["comments"]["nodes"]:
            items.append({"id": c["id"], "author": (c["author"] or {}).get("login", "ghost"),
                          "kind": "inline", "where": where, "text": c["body"], "url": c["url"]})
    for c in json.loads(gh(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"])):
        items.append({"id": f"issue-comment-{c['id']}", "author": c["user"]["login"],
                      "kind": "comment", "where": "conversation", "text": c["body"], "url": c["html_url"]})
    for r in json.loads(gh(["api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"])):
        state, body = r["state"], (r.get("body") or "").strip()
        # A plain COMMENTED review with no body is just the wrapper around inline comments.
        if state == "PENDING" or (state == "COMMENTED" and not body):
            continue
        label = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested"}.get(state, "review")
        items.append({"id": f"review-{r['id']}", "author": r["user"]["login"], "kind": label,
                      "where": "review", "text": body, "url": r["html_url"]})
    return items


def notify(cfg, title, message):
    if cfg.get("NOTIFY_MACOS", "false") == "true" and sys.platform == "darwin":
        # Pass text as arguments, never inside the script, so comment text can't inject AppleScript.
        subprocess.run(["osascript", "-e", "on run argv",
                        "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                        "-e", "end run", title, message[:250]], check=False)
    if cfg.get("NOTIFY_LINUX", "false") == "true":
        subprocess.run(["notify-send", title, message[:250]], check=False)
    url = cfg.get("SLACK_WEBHOOK_URL", "")
    if url:
        req = urllib.request.Request(url, data=json.dumps({"text": f"*{title}*\n{message}"}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as err:  # a failed notification must not lose the state update
            print(f"slack notify failed: {err}", file=sys.stderr)
    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        # Plain text (no parse_mode) so comment text can't break Markdown parsing. Telegram caps at 4096.
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=json.dumps({"chat_id": chat_id,
                                                      "text": f"{title}\n{message}"[:4096]}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as err:  # don't print the URL: it contains the bot token
            print(f"telegram notify failed: {type(err).__name__}", file=sys.stderr)


def main():
    cfg = load_config()
    state_dir = pathlib.Path(cfg["STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    seen_file = state_dir / "seen.json"
    first_run = not seen_file.exists()
    seen = set(json.loads(seen_file.read_text())) if not first_run else set()

    me = gh(["api", "user", "--jq", ".login"]).strip()
    ignore = set(cfg.get("IGNORE_AUTHORS", "").split()) | {me}
    include_bots = cfg.get("INCLUDE_BOTS", "false") == "true"

    query = "is:pr is:open author:@me" + (f" org:{cfg['ORG']}" if cfg.get("ORG") else "")
    prs = json.loads(gh(["search", "prs", query, "--limit", "50", "--json", "repository,number,title,url"]))

    fresh = {}
    for pr in prs:
        repo, number = pr["repository"]["nameWithOwner"], pr["number"]
        try:
            items = collect(repo, number)
        except RuntimeError as err:
            print(f"skipping {repo}#{number}: {err}", file=sys.stderr)
            continue
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            author = it["author"]
            if author in ignore or (author.endswith("[bot]") and not include_bots):
                continue
            fresh.setdefault(f"{repo}#{number}", {"title": pr["title"], "url": pr["url"], "items": []})["items"].append(it)

    seen_file.write_text(json.dumps(sorted(seen)))
    total = sum(len(p["items"]) for p in fresh.values())

    if first_run:
        notify(cfg, "PR inbox is set up",
               f"Watching {len(prs)} open PR(s). {total} existing comment(s) marked as read; "
               "you'll hear about new ones from now on.")
        print(json.dumps({"firstRun": True, "prs": len(prs), "markedRead": total}))
        sys.exit(1)

    if not fresh:
        print(json.dumps({"new": 0}))
        sys.exit(1)

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"# PR inbox, {now}", ""]
    summary = []
    for ref, pr in fresh.items():
        who = sorted({i["author"] for i in pr["items"]})
        summary.append(f"{ref}: {len(pr['items'])} new from {', '.join(who)}")
        lines += [f"## [{ref}]({pr['url']}) {pr['title']}", ""]
        for i in pr["items"]:
            lines.append(f"- **{i['author']}** ({i['kind']}, `{i['where']}`): {one_line(i['text'])} [open]({i['url']})")
        lines += ["", f"Hand off: `/pr-brief {ref}`", ""]
    (state_dir / "inbox.md").write_text("\n".join(lines))
    with open(state_dir / "history.md", "a") as history:
        history.write("\n".join(lines) + "\n")

    notify(cfg, f"{total} new PR comment(s)",
           "\n".join(summary) + "\nDetails: ~/.pr-inbox/inbox.md · Hand off: /pr-brief <repo#number>")
    print(json.dumps({"new": total, "prs": list(fresh)}))

    if cfg.get("AUTO_BRIEF", "false") == "true":
        (state_dir / "brief-queue.json").write_text(json.dumps(list(fresh)))
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
