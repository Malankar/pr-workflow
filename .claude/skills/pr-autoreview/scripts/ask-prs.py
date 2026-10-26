#!/usr/bin/env python3
"""Ask me on Telegram which of the queued PRs to review. No AI, no tokens.

Reads $STATE_DIR/queue.json (written by find-prs.sh), sends the PRs as a numbered list to my
Telegram chat and waits for a reply like "1 3", "all" or "none". Only my chat is listened to.
It rewrites queue.json with just the PRs I picked and prints it.

Exit 0 = at least one PR picked, exit 1 = nothing to review (empty queue, "none", or no reply
within the timeout). Use it as the Orca precheck right after find-prs.sh.

PRs I was already asked about at the same commit are not asked again. A timeout asks again next run.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TIMEOUT_MIN = int(os.environ.get("ASK_TIMEOUT_MIN", "9"))


def load_config():
    # pr-inbox first (bot token, chat id), then ours so STATE_DIR is the pr-autoreview one.
    files = [ROOT / "pr-inbox" / "config.env", HERE.parent / "config.env"]
    sources = "; ".join(f'source "{f}"' for f in files)
    out = subprocess.run(["bash", "-c", f"set -a; {sources}; env -0"],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split("\0") if "=" in line)


def tg(token, method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=params.get("timeout", 0) + 15) as resp:
            return json.load(resp)["result"]
    except urllib.error.HTTPError as err:  # don't print the URL: it contains the bot token
        hint = " (webhook set on this bot, or another getUpdates is running?)" if err.code == 409 else ""
        sys.exit(f"telegram {method} failed: HTTP {err.code}{hint}")
    except Exception as err:
        sys.exit(f"telegram {method} failed: {type(err).__name__}")


def brief(pr):
    """Author, size and the start of the description, from gh. A failure just means no brief."""
    try:
        out = subprocess.run(["gh", "pr", "view", str(pr["number"]), "-R", pr["repo"], "--json",
                              "author,additions,deletions,changedFiles,body"],
                             capture_output=True, text=True, check=True, timeout=30).stdout
        info = json.loads(out)
    except Exception:
        return ""
    body = [ln.strip() for ln in (info.get("body") or "").splitlines()]
    body = " ".join(ln for ln in body if ln and not ln.startswith("#"))
    body = body if len(body) <= 200 else body[:200].rstrip() + "…"
    head = (f"by {info['author']['login']} · {info['changedFiles']} files "
            f"+{info['additions']}/-{info['deletions']}")
    return f"\n   {head}" + (f"\n   {body}" if body else "")


def parse_reply(text, count):
    text = text.strip().lower()
    if text in ("none", "no", "0", "skip"):
        return []
    if text == "all":
        return list(range(1, count + 1))
    picks = [int(n) for n in re.findall(r"\d+", text)]
    if picks and all(1 <= n <= count for n in picks):
        return sorted(set(picks))
    return None


def main():
    cfg = load_config()
    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing (set TELEGRAM_BOT_TOKEN in ~/.zshenv; chat id is in pr-inbox/config.env)")

    state_dir = pathlib.Path(cfg["STATE_DIR"])
    queue_file = state_dir / "queue.json"
    asked_file = state_dir / "asked.json"
    queue = json.loads(queue_file.read_text()) if queue_file.exists() else []
    asked = json.loads(asked_file.read_text()) if asked_file.exists() else {}

    queue = [pr for pr in queue if asked.get(f"{pr['repo']}#{pr['number']}") != pr["headSha"]]
    if not queue:
        queue_file.write_text("[]")
        print("[]")
        sys.exit(1)

    # Skip anything I sent before this run, so an old "1 2" can't answer a new question.
    latest = tg(token, "getUpdates", offset=-1, timeout=0)
    offset = latest[-1]["update_id"] + 1 if latest else 0

    lines = [f"{i}. {pr['repo']}#{pr['number']} {pr['title']}{brief(pr)}\n   {pr['url']}"
             for i, pr in enumerate(queue, 1)]
    sent = tg(token, "sendMessage", chat_id=chat_id, disable_web_page_preview=True,
              text=("PRs waiting for your review. Reply with numbers (e.g. 1 3), all, or none.\n\n"
                    + "\n".join(lines))[:4096])

    deadline = time.time() + TIMEOUT_MIN * 60
    hinted = False
    while time.time() < deadline:
        wait = min(25, int(deadline - time.time()))
        for update in tg(token, "getUpdates", offset=offset, timeout=max(wait, 1), allowed_updates=["message"]):
            offset = update["update_id"] + 1
            msg = update.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(chat_id) or msg.get("date", 0) < sent["date"]:
                continue
            picks = parse_reply(msg.get("text", ""), len(queue))
            if picks is None:
                if not hinted:
                    hinted = True
                    tg(token, "sendMessage", chat_id=chat_id,
                       text=f"Didn't get that. Reply with numbers 1-{len(queue)}, all, or none.")
                continue
            chosen = [queue[n - 1] for n in picks]
            for pr in queue:
                asked[f"{pr['repo']}#{pr['number']}"] = pr["headSha"]
            asked_file.write_text(json.dumps(asked, indent=2))
            queue_file.write_text(json.dumps(chosen, indent=2))
            tg(token, "getUpdates", offset=offset, timeout=0)  # confirm the reply so it isn't re-read
            summary = ", ".join(f"#{pr['number']}" for pr in chosen) or "nothing"
            tg(token, "sendMessage", chat_id=chat_id, text=f"Got it. Reviewing: {summary}")
            print(json.dumps(chosen, indent=2))
            sys.exit(0 if chosen else 1)

    tg(token, "sendMessage", chat_id=chat_id,
       text=f"No reply in {TIMEOUT_MIN} min. Skipping this run; I'll ask again next time.")
    queue_file.write_text("[]")
    print("[]")
    sys.exit(1)


if __name__ == "__main__":
    main()
