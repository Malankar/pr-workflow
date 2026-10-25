---
name: pr-autoreview
description: Unattended PR review run. Finds PRs where the user's review is requested, reviews each one (Matt Pocock's code-review skill for standards and spec, plus a bug hunter), sends every finding through an independent verifier, and posts only verified findings as a GitHub review under the user's account. Use whenever a scheduled automation or the user says "run pr-autoreview", "review my pending PRs" or "auto-review PRs".
---

# PR auto-review

This runs unattended, usually from a scheduled Orca automation. Nobody is watching.

- **Never ask the user a question.** If something is missing (spec, ticket, a tool), make the documented fallback choice below and note it in the run summary.
- **If one PR fails, log it and move on to the next.** Never stop the whole run for one PR.
- **Nothing reaches GitHub except through `post_review.py`.** Never call `gh` to comment, review or approve yourself.

Run every command from the root of the `claude-automations` repo.

## 1. Find work

The Orca precheck has already run `find-prs.sh` (finds the PRs) and `ask-prs.py` (asked the user on Telegram which ones to review, and waited up to 9 minutes, the most an Orca precheck allows). It left the picked PRs in `~/.pr-autoreview/queue.json`. That file is single-use: take it, and move it out of the way so the same picks can never be reviewed twice.

```bash
bash .claude/skills/pr-autoreview/scripts/take-queue.sh
```

It's a JSON array (repo, number, title, url, headSha). Review exactly those and no others. The filtering (bots, Dependabot, drafts, PRs already reviewed by you or the skip-list, commits already reviewed) is already done; don't second-guess it.

If it exits 1 (file missing or `[]`) there's no fresh pick, which means this isn't a precheck run (for example a manual "run pr-autoreview" in a normal session). Then run both scripts yourself, in order, and use the PRs the second one prints:

```bash
bash .claude/skills/pr-autoreview/scripts/find-prs.sh && python3 .claude/skills/pr-autoreview/scripts/ask-prs.py
```

If that exits 1 too, write "No PRs picked" and end the run. `ask-prs.py` is the only allowed way to ask the user anything.

## 2. For each PR, one at a time

Keep the main context small: process PRs sequentially, let sub-agents read diffs, and keep only a one-line result per PR in your own context.

### 2a. Prepare

```bash
bash .claude/skills/pr-autoreview/scripts/prepare-pr.sh <repo> <number>
```

It prints JSON with `worktree`, `bundle`, `fixedPoint`, `clone`. Run every git command for this PR inside `worktree`.

### 2b. Build the spec

Write `<bundle>/spec.md` containing:

1. The PR title and description from `<bundle>/meta.json`.
2. For each key in `<bundle>/tickets.txt`: if Jira/Atlassian tools are available, fetch the ticket and append its summary, description and acceptance criteria. Ignore keys that don't resolve to a ticket (things like `UTF-8`).

If the description is empty and no ticket resolves, write `NO SPEC` as the only line.

### 2c. Review: three axes in parallel

Use the **code-review** skill (from `mattpocock/skills`) with these unattended overrides:

- **Fixed point:** `fixedPoint` from 2a. Don't ask for one.
- **Spec:** `<bundle>/spec.md`. If it says `NO SPEC`, skip the Spec axis. Don't ask, and don't require `/setup-matt-pocock-skills` for this run.
- **In its step 4, launch three sub-agents in one parallel batch:** Standards, Spec, and the **pr-bug-hunter** agent. Matt's skill covers conventions and spec fit but doesn't hunt for bugs, so the bug hunter is the third axis.
- **Every sub-agent reports in caveman-review's one-line format,** one finding per line. It's short, so it costs fewer tokens, and it's easy to parse:

  ```
  <path>:L<line>: <tag>: <problem>. <fix>. ‖ "<exact quoted line>"
  ```

  Tags: `🔴 bug` (→ P1), `🟡 risk` (→ P2), `🔵 nit` (→ P3), `❓ q` (→ P3). A spec gap with no single line uses `spec:` instead of `<path>:L<line>:` and quotes the spec line. Write this format into each sub-agent's prompt yourself (the prompt itself in full sentences), and pass them the worktree path and `<bundle>/pr.diff`. Don't install or invoke the caveman-review skill: it auto-triggers on "review this PR" and would leak into normal chats.

### 2d. Normalize

Turn all three reports into `<bundle>/findings.json`, mapping each line's tag to a severity. Copy claims faithfully; don't judge them yet, that's the verifier's job.

```json
{
  "pr": "owner/repo#123",
  "findings": [
    {
      "id": "F1",
      "axis": "Bug | Standards | Spec",
      "severity": "P1 | P2 | P3",
      "path": "src/orders/checkout.ts",
      "line": 42,
      "title": "Short, specific title",
      "claim": "What is wrong and why, as the reviewer stated it",
      "quote": "the exact line(s) the reviewer pointed at"
    }
  ]
}
```

A finding with no specific location (for example "requirement X is missing") gets `"path": null, "line": null`. Baseline smells from Matt's skill are always `P3`.

### 2e. Verify

Launch the **pr-verifier** agent. Give it only:

- the path to `findings.json`;
- the worktree path;
- `<bundle>/pr.diff` and `<bundle>/spec.md`.

**Do not pass the reviewers' reports or your own opinion.** The verifier has to re-derive every finding from the code, not agree with the reviewers. With more than 10 findings, split them by file across up to 3 verifiers in parallel, then merge their outputs.

The verifier writes `<bundle>/verified.json`. Check it parses as JSON; if not, ask the verifier once to fix it, otherwise log the PR as failed.

### 2f. Post

```bash
python3 .claude/skills/pr-autoreview/scripts/post_review.py <bundle>
```

It posts only `confirmed` or `downgraded` findings, caps nits, moves comments for lines outside the diff into the review body, records state, and prints a one-line JSON result. With `DRY_RUN=true` in config it writes `payload.json` and posts nothing.

### 2g. Write the context

```bash
python3 .claude/skills/pr-autoreview/scripts/write-context.py <bundle>
```

It writes `<bundle>/context.md` (what the PR does, every verified finding, why the rest were rejected) and `<bundle>/brief.json`. Don't summarise the PR yourself: the script does it for free, and `context.md` is what the user opens later to re-review or answer a question.

### 2h. Clean up

```bash
git -C <clone> worktree remove --force <worktree>
```

## 3. Notify

After the last PR, send the Telegram summary. Pass the bundle of every PR that got a `brief.json`:

```bash
python3 .claude/skills/pr-autoreview/scripts/notify-summary.py <bundle> [<bundle> ...]
```

## 4. Run summary

Append to `$STATE_DIR/runs/<today>/summary.md` and print it. One line per PR:

- the PR and its title;
- what happened (posted / clean / dry run / skipped / failed);
- counts: findings raised → verified → posted;
- anything you had to fall back on (no spec, ticket tools unavailable).

The raised-vs-verified ratio tells the user how noisy the reviewers are, so always include it.
