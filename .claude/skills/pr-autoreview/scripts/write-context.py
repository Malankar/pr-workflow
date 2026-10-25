#!/usr/bin/env python3
"""Write a readable one-pager for a reviewed PR. No AI, no tokens.

Usage: write-context.py <bundle>

Reads meta.json, findings.json, verified.json and (if present) payload.json from the PR's bundle
and writes two files next to them:
  context.md  what the PR does, every finding that survived verification, and why the rest were rejected.
              Open it to re-review the PR or to answer a question about it later.
  brief.json  the description start, counts and findings that notify-summary.py sends to Telegram.
"""
import json
import pathlib
import re
import sys

BODY_LINES = 15
ABOUT_CHARS = 300
WHY_CHARS = 240


def load(bundle, name):
    path = bundle / name
    return json.loads(path.read_text()) if path.exists() else None


def where(finding):
    return f"{finding['path']}:{finding['line']}" if finding.get("path") else "(whole PR)"


def description(body):
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
    return "\n".join(lines[:BODY_LINES]) or "(empty description)"


def about(body):
    """The first few lines of real prose from the description, for the Telegram brief."""
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    keep = [ln.strip() for ln in body.splitlines()
            if ln.strip() and not re.match(r"\s*(#|---|jira issue|[-*] \[|\d+\. #)", ln, re.I)]
    text = " ".join(keep)
    if len(text) <= ABOUT_CHARS:
        return text
    cut = text[:ABOUT_CHARS]
    end = cut.rfind(". ")  # stop at a sentence end if there is one, so it doesn't trail off mid-thought
    return cut[:end + 1] if end > 60 else cut.rstrip() + "…"


def why(body):
    """The opening sentences of a verified finding (up to WHY_CHARS): what is wrong, in the reviewer's words."""
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s", body.strip()):
        if out and len(out) + len(sentence) + 1 > WHY_CHARS:
            break
        out = f"{out} {sentence}".strip()
    return out if len(out) <= WHY_CHARS else out[:WHY_CHARS].rstrip() + "…"


def main():
    bundle = pathlib.Path(sys.argv[1])
    meta, raised, verified = (load(bundle, n) for n in ("meta.json", "findings.json", "verified.json"))
    payload = load(bundle, "payload.json")
    if not (meta and raised and verified):
        sys.exit(f"{bundle}: meta.json, findings.json or verified.json missing")

    repo = bundle.name.rsplit("__", 1)[0].replace("__", "/")
    pr = f"{repo}#{meta['number']}"
    kept = sorted(verified["findings"], key=lambda f: f["severity"])
    titles = {f["id"]: f["title"] for f in raised["findings"]}
    posting = len(payload["comments"]) if payload else None

    out = [f"# {pr} {meta['title']}", "",
           f"{meta['url']}  ",
           f"By {meta['author']['login']} · `{meta['headRefName']}` into `{meta['baseRefName']}` · "
           f"head `{meta['headRefOid'][:7]}` · {len(meta['files'])} files", "",
           "## What the PR says it does", "", description(meta.get("body")), "",
           f"## Findings that survived verification ({len(raised['findings'])} raised → {len(kept)} verified)", ""]
    for f in kept:
        out += [f"### {f['id']} · {f['severity']} · {f['axis']} · {f['verdict']} · `{where(f)}`",
                f"**{f['title']}**", "", f["body"], "",
                f"Evidence: {f['evidence']}" if f.get("evidence") else "", ""]
    out += ["## Rejected", ""]
    out += [f"- {r['id']} {titles.get(r['id'], '')}: {r['reason']}" for r in verified["rejected"]] or ["- none"]
    (bundle / "context.md").write_text("\n".join(out).rstrip() + "\n")

    counts = {sev: sum(f["severity"] == sev for f in kept) for sev in ("P1", "P2", "P3")}
    brief = {"pr": pr, "number": meta["number"], "title": meta["title"], "url": meta["url"],
             "author": meta["author"]["login"], "about": about(meta.get("body")),
             "raised": len(raised["findings"]), "verified": len(kept), "posting": posting, "counts": counts,
             "findings": [{"severity": f["severity"], "where": where(f), "title": f["title"],
                           "why": why(f["body"])} for f in kept]}
    (bundle / "brief.json").write_text(json.dumps(brief, indent=2))
    print(f"wrote {bundle / 'context.md'}")


if __name__ == "__main__":
    main()
