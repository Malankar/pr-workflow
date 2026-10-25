#!/usr/bin/env python3
"""Send the user a Telegram summary of a pr-autoreview run. No AI, no tokens.

Usage: notify-summary.py <bundle> [<bundle> ...]

Each bundle needs the brief.json that write-context.py wrote. Sends one header message, then per
PR: what the PR does, the finding counts, every P1 and P2 with its title, location and a one-line
reason, and the P3s as titles only. A long PR is split over several messages. The full detail is
in each bundle's context.md.
"""
import json
import pathlib
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def load_config():
    files = [ROOT / "pr-inbox" / "config.env", HERE.parent / "config.env"]
    sources = "; ".join(f'source "{f}"' for f in files)
    out = subprocess.run(["bash", "-c", f"set -a; {sources}; env -0"],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split("\0") if "=" in line)


def send(token, chat_id, text):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps({"chat_id": chat_id, "text": text[:4096],
                                                  "disable_web_page_preview": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as err:  # don't print the URL: it contains the bot token
        print(f"telegram send failed: {type(err).__name__}", file=sys.stderr)


def short_path(where):
    path, _, line = where.rpartition(":")
    return "/".join(path.split("/")[-2:]) + (f":{line}" if line else "") if path else where


def pr_messages(b):
    """Telegram caps a message at 4096 characters, so the findings go out in chunks."""
    c = b["counts"]
    head = [f"#{b['number']} {b['title']} (by {b['author']})", b["url"],
            f"About: {b['about'] or 'no description on the PR'}",
            f"{b['raised']} raised → {b['verified']} verified"
            + (f" → {b['posting']} to post" if b["posting"] is not None else "")
            + f" ({c['P1']} P1, {c['P2']} P2, {c['P3']} P3)"]
    blocks = []
    for f in b["findings"]:
        if f["severity"] == "P3":
            blocks.append(f"⚪ P3 {short_path(f['where'])}: {f['title']}")
        else:
            icon = "🔴" if f["severity"] == "P1" else "🟡"
            blocks.append(f"{icon} {f['severity']} {short_path(f['where'])}\n{f['title']}\nWhy: {f['why']}")
    messages, current = [], "\n".join(head)
    for block in blocks:
        if len(current) + len(block) + 2 > 3900:
            messages.append(current)
            current = f"#{b['number']} (continued)"
        current += "\n\n" + block
    return messages + [current]


def main():
    briefs = [json.loads((pathlib.Path(p) / "brief.json").read_text()) for p in sys.argv[1:]]
    cfg = load_config()
    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id and briefs):
        sys.exit("nothing to send, or TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing")
    mode = "dry run, nothing posted to GitHub" if cfg.get("DRY_RUN") == "true" else "pending reviews created on GitHub"
    send(token, chat_id, f"PR auto-review finished: {len(briefs)} PR(s) ({mode})")
    for b in briefs:
        for message in pr_messages(b):
            send(token, chat_id, message)


if __name__ == "__main__":
    main()
