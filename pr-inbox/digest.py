#!/usr/bin/env python3
"""pr-digest: one Telegram message listing everything that is waiting on me. No AI, no tokens.

Three groups, from the repos in REPOS (config.env). Only open PRs, never bots or my own comments:
  1. My PRs with comments waiting on my reply (same rules as check_my_prs.py).
  2. PRs I reviewed where someone replied in a thread I started and I haven't followed up.
     Skipped when my latest review is an approval.
  3. PRs where my review is requested and I haven't commented or reviewed yet.
Sends nothing when all three are empty. Always exits 1, so as an Orca precheck it never starts
an agent run. Test without sending:  digest.py --dry-run
"""
import argparse
import html
import json
import pathlib
import sys
import urllib.request
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_my_prs import collect, gh, load_config, telegram_lock  # noqa: E402

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes { isResolved comments(first: 50) { nodes { author { login } createdAt } } }
      }
    }
  }
}"""
CHUNK = 3500  # Telegram caps a message at 4096 characters


def is_ignored(author, bot_flag, ignore, include_bots):
    return author in ignore or ((bot_flag or author.endswith("[bot]") or author.startswith("app/")) and not include_bots)


def waiting_threads(repo, number, me):
    """Unresolved threads I took part in where someone else spoke last: (count, last author, last date)."""
    owner, name = repo.split("/")
    data = json.loads(gh(["api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"owner={owner}",
                          "-f", f"name={name}", "-F", f"number={number}"]))
    count, last = 0, ("", "")
    for thread in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        comments = thread["comments"]["nodes"]
        logins = [(c["author"] or {}).get("login", "ghost") for c in comments]
        if thread["isResolved"] or me not in logins or logins[-1] == me:
            continue
        count += 1
        last = max(last, (comments[-1]["createdAt"], logins[-1]))
    return count, last[1], last[0]


def scan(cfg, me):
    ignore = set(cfg.get("IGNORE_AUTHORS", "").split()) | {me}
    include_bots = cfg.get("INCLUDE_BOTS", "false") == "true"
    mine, replied, untouched = [], [], []
    for repo in cfg["REPOS"].split():
        prs = json.loads(gh(["pr", "list", "-R", repo, "--state", "open", "--limit", "100", "--json",
                             "number,title,url,author,isDraft,reviewRequests,reviews,comments"]))
        for pr in prs:
            author = (pr["author"] or {}).get("login", "ghost")
            ref = {"repo": repo, "number": pr["number"], "title": pr["title"], "url": pr["url"], "draft": pr["isDraft"]}
            if author == me:
                try:
                    items = [i for i in collect(repo, pr["number"], me)
                             if not is_ignored(i["author"], False, ignore, include_bots)]
                except RuntimeError as err:
                    print(f"skipping {repo}#{pr['number']}: {err}", file=sys.stderr)
                    continue
                if items:
                    mine.append((ref, items))
                continue
            if is_ignored(author, (pr["author"] or {}).get("is_bot"), ignore, include_bots):
                continue
            my_reviews = [r for r in pr["reviews"] if (r["author"] or {}).get("login") == me]
            my_comments = [c for c in pr["comments"] if (c["author"] or {}).get("login") == me]
            if not my_reviews and not my_comments:
                if any(rr.get("login") == me for rr in pr["reviewRequests"]):
                    untouched.append(ref)
            elif my_reviews and my_reviews[-1]["state"] != "APPROVED":
                count, who, when = waiting_threads(repo, pr["number"], me)
                if count:
                    replied.append((ref, count, who, when))
    return mine, replied, untouched


def link(ref, text=None):
    return f'<a href="{html.escape(ref["url"], quote=True)}">{html.escape(text or "#" + str(ref["number"]))}</a>'


def short(text, limit=48):
    text = " ".join(text.split())
    return html.escape(text if len(text) <= limit else text[: limit - 1] + "…")


def when(iso):
    return date.fromisoformat(iso[:10]).strftime("%b %-d")


def build(mine, replied, untouched):
    name = lambda ref: ref["repo"].split("/")[1]  # noqa: E731
    flag = lambda ref: " (draft)" if ref["draft"] else ""  # noqa: E731
    out = [f"<b>PR digest</b> · {date.today().strftime('%a %b %-d')}", ""]
    if mine:
        out.append("<b>Waiting on your reply, your PRs</b>")
        for ref, items in mine:
            who = ", ".join(sorted({i["author"] for i in items}))
            out.append(f"• {link(ref, name(ref) + ' #' + str(ref['number']))} {short(ref['title'])}: "
                       f"{len(items)} from {html.escape(who)}")
        out.append("")
    if replied:
        out.append("<b>You reviewed, they replied, you haven't followed up</b>")
        for ref, count, who, last in sorted(replied, key=lambda r: r[3], reverse=True):
            out.append(f"• {link(ref, name(ref) + ' #' + str(ref['number']))} {short(ref['title'])}{flag(ref)}: "
                       f"{count} thread(s), last from {html.escape(who)} on {when(last)}")
        out.append("")
    if untouched:
        out.append("<b>Review requested, not touched yet</b>")
        for repo in sorted({r["repo"] for r in untouched}):
            refs = sorted((r for r in untouched if r["repo"] == repo), key=lambda r: r["number"], reverse=True)
            out.append(f"• {html.escape(repo.split('/')[1])}: " +
                       ", ".join(f"{link(r)} {short(r['title'], 36)}{flag(r)}" for r in refs))
        out.append("")
    return "\n".join(out).strip()


def chunks(text):
    parts, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > CHUNK:
            parts.append(current)
            current = ""
        current += ("\n" if current else "") + line
    return parts + [current]


def send(token, chat_id, text):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                                  "disable_web_page_preview": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as err:  # don't print the URL: it contains the bot token
        sys.exit(f"telegram sendMessage failed: {type(err).__name__}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    args = ap.parse_args()
    cfg = load_config()
    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")
    if not cfg.get("REPOS"):
        sys.exit("digest needs REPOS in pr-inbox/config.env")
    if not args.dry_run and (not token or not chat_id):
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing (set TELEGRAM_BOT_TOKEN in ~/.zshenv; chat id is in pr-inbox/config.env)")

    mine, replied, untouched = scan(cfg, gh(["api", "user", "--jq", ".login"]).strip())
    if not (mine or replied or untouched):
        print(json.dumps({"sent": 0}))
        sys.exit(1)
    text = build(mine, replied, untouched)
    if args.dry_run:
        print(text)
    else:
        # A fix question may be waiting for my reply; don't send over it. Wait up to 10 min for it to finish.
        lock = telegram_lock(cfg["STATE_DIR"], wait_s=600)
        if not lock:
            sys.exit("telegram busy (waiting for a reply to another message), digest not sent")
        for part in chunks(text):
            send(token, chat_id, part)
    print(json.dumps({"sent": 0 if args.dry_run else 1, "mine": len(mine), "replied": len(replied),
                      "untouched": len(untouched)}))
    sys.exit(1)


if __name__ == "__main__":
    main()
