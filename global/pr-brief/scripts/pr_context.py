#!/usr/bin/env python3
"""Gather everything needed to brief on review comments for one PR.

Usage: pr_context.py <owner/repo#123 | PR URL | 123 (repo of the current directory)>

Writes a bundle to ~/.pr-inbox/briefs/<owner>__<repo>__<n>/:
  meta.json          title, description, branches, size, files
  pr.diff            the full diff
  threads.json       unresolved review threads, with ids for replying and resolving
  conversation.json  conversation comments and review summaries from other people
Makes sure a local clone has the PR head fetched, so code can be read with `git show`.
Prints one JSON object with the paths and the numbers that matter.
"""
import json
import pathlib
import re
import subprocess
import sys

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first: 50) { nodes { databaseId author { login } body createdAt url diffHunk } }
        }
      }
    }
  }
}"""


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and result.returncode != 0:
        sys.exit(f"{' '.join(cmd[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def parse_ref(ref):
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([\w.-]+/[\w.-]+)#(\d+)$", ref)
    if m:
        return m.group(1), int(m.group(2))
    if ref.isdigit():
        repo = run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()
        return repo, int(ref)
    sys.exit("usage: pr_context.py <owner/repo#123 | PR URL | 123>")


def find_clone(repo, slug):
    """Prefer the repo you're in, then pr-autoreview's cache, else a fresh blobless clone."""
    remote = run(["git", "remote", "get-url", "origin"], check=False).strip().lower()
    if remote and repo.lower() in remote.removesuffix(".git"):
        return pathlib.Path(run(["git", "rev-parse", "--show-toplevel"]).strip())
    for candidate in (pathlib.Path.home() / ".pr-autoreview/repos" / slug,
                      pathlib.Path.home() / ".pr-inbox/repos" / slug):
        if (candidate / ".git").exists():
            return candidate
    target = pathlib.Path.home() / ".pr-inbox/repos" / slug
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["gh", "repo", "clone", repo, str(target), "--", "--filter=blob:none", "--quiet"])
    return target


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: pr_context.py <owner/repo#123 | PR URL | 123>")
    repo, number = parse_ref(sys.argv[1])
    owner, name = repo.split("/")
    slug = repo.replace("/", "__")
    bundle = pathlib.Path.home() / ".pr-inbox/briefs" / f"{slug}__{number}"
    bundle.mkdir(parents=True, exist_ok=True)

    meta = json.loads(run(["gh", "pr", "view", str(number), "-R", repo, "--json",
                           "number,title,body,url,state,isDraft,author,baseRefName,headRefName,"
                           "headRefOid,additions,deletions,changedFiles,files,reviewDecision"]))
    (bundle / "meta.json").write_text(json.dumps(meta, indent=2))
    (bundle / "pr.diff").write_text(run(["gh", "pr", "diff", str(number), "-R", repo]))

    data = json.loads(run(["gh", "api", "graphql", "-f", f"query={THREADS_QUERY}",
                           "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"]))
    threads = [t for t in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
               if not t["isResolved"]]
    (bundle / "threads.json").write_text(json.dumps(threads, indent=2))

    me = run(["gh", "api", "user", "--jq", ".login"]).strip()
    conversation = []
    for c in json.loads(run(["gh", "api", f"repos/{repo}/issues/{number}/comments?per_page=100"])):
        if c["user"]["login"] != me:
            conversation.append({"kind": "comment", "author": c["user"]["login"],
                                 "body": c["body"], "url": c["html_url"], "at": c["created_at"]})
    for r in json.loads(run(["gh", "api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"])):
        if r["user"]["login"] != me and r["state"] != "PENDING" and (r.get("body") or r["state"] != "COMMENTED"):
            conversation.append({"kind": r["state"].lower(), "author": r["user"]["login"],
                                 "body": r.get("body") or "", "url": r["html_url"], "at": r.get("submitted_at")})
    (bundle / "conversation.json").write_text(json.dumps(conversation, indent=2))

    clone = find_clone(repo, slug)
    run(["git", "-C", str(clone), "fetch", "--quiet", "origin",
         f"+refs/pull/{number}/head:refs/remotes/pr/{number}",
         f"+refs/heads/{meta['baseRefName']}:refs/remotes/origin/{meta['baseRefName']}"])

    print(json.dumps({
        "pr": f"{repo}#{number}", "bundle": str(bundle), "clone": str(clone),
        "headSha": meta["headRefOid"], "base": meta["baseRefName"],
        "size": f"+{meta['additions']} -{meta['deletions']} in {meta['changedFiles']} files",
        "unresolvedThreads": len(threads), "conversationItems": len(conversation),
        "reviewDecision": meta.get("reviewDecision"),
        "readCode": f"git -C {clone} show {meta['headRefOid']}:<path>",
    }, indent=2))


if __name__ == "__main__":
    main()
