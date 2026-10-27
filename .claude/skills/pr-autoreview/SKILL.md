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

## 2. Prepare every PR

Each PR is reviewed by its own Orca worker: a separate Orca worktree and a separate Claude session. Your own session is the **coordinator**. It only prepares, dispatches, waits, posts and cleans up. Never review a PR yourself, and never read a diff in your own context. Use Orca orchestration for the workers, not the Agent tool.

**Tooling rule (the run has no one to approve prompts):** use the Read, Write and Edit tools for files, and only the scripts and `orca` commands named in this skill for shell work. Don't write ad-hoc shell or Python (`B=...; python3 -c ...`, heredocs, `cat >> file`): anything not allowed is denied, and a denied step fails the PR.

For each PR in the queue, one after another (this is fast and avoids two clones racing), run:

```bash
bash .claude/skills/pr-autoreview/scripts/prepare-pr.sh <repo> <number>
```

It prints JSON with `worktree`, `bundle`, `fixedPoint`, `clone`. Keep one JSON per PR. If it fails for a PR, log that PR as failed and continue with the others.

## 3. Review all PRs in parallel with Orca workers

Create one Run, then start one worker per prepared PR **before waiting for any of them**:

```bash
orca status --json
orca orchestration run-create --objective "pr-autoreview <today>" --json
orca orchestration worker-start --spec "<spec for this PR>" --worktree new-child --name "review-<repo>-<number>" --agent claude --json
```

Use `--name review-<repo>-<number>` with `/` replaced by `-`. Start all workers first, then wait. Never run PRs one after another, and never give two PRs to one worker.

If `orca status` fails, or `worker-start` exits non-zero, do not retry or relaunch. Log that PR as failed with the error text and continue. If every `worker-start` fails, end the run with the summary in step 6.

Each worker's `--spec` is full sentences and self-contained. It names:

- **Target:** the repo, number and title, and the prepare JSON (`worktree`, `bundle`, `fixedPoint`). The worker stays inside its own `worktree` and `bundle`.
- **Change:** do steps 3a to 3d below and leave `<bundle>/verified.json` written and valid. Copy those steps into the spec.
- **Constraints:** do not run `post_review.py`, `write-context.py`, `cleanup-pr.sh` or `notify-summary.py`, and do not touch GitHub.
- **Acceptance:** `<bundle>/verified.json` exists and `merge-verified.py` exited 0. The `worker_done` summary says `ok, raised=<n>, verified=<n>`, or the worker sends `--outcome failed` with the reason. Findings stay in the files.

Then wait until every worker has settled:

```bash
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

Process every message. A `question` from a worker: reply with `orca orchestration reply --id <message_id> --body "<answer>" --json`. Never ask the user; if the question can't be answered from this skill, answer with the documented fallback. Validate each `worker_done` against its PR, record `succeeded` or `failed`, then acknowledge it by passing `--ack <delivery_id>` on the next `check`. A timeout or empty result is a checkpoint, not a failure: keep waiting. After three empty waits in a row, run `orca orchestration worker-list --run <run_id> --json` and follow each row's `projection.nextAction`. Don't stop or retry a worker without positive proof it exited.

After a worker settles, release it: `orca orchestration worker-release --dispatch <dispatch_id> --json`. Before leaving this step, `orca orchestration worker-list --run <run_id> --terminal-state reclaimable --json` must return no rows.

### 3a. Build the spec

Read `<bundle>/meta.json` and `<bundle>/tickets.txt` with the Read tool, then create `<bundle>/spec.md` with the Write tool, containing:

1. The PR title and description from `<bundle>/meta.json`.
2. For each key in `<bundle>/tickets.txt`: if Jira/Atlassian tools are available, fetch the ticket and append its summary, description and acceptance criteria. Ignore keys that don't resolve to a ticket (things like `UTF-8`).

If the description is empty and no ticket resolves, write `NO SPEC` as the only line.

### 3b. Review: three axes in parallel

Use the **code-review** skill (from `mattpocock/skills`) with these unattended overrides:

- **Fixed point:** `fixedPoint` from the prepare step. Don't ask for one.
- **Spec:** `<bundle>/spec.md`. If it says `NO SPEC`, skip the Spec axis. Don't ask, and don't require `/setup-matt-pocock-skills` for this run.
- **In its step 4, launch three sub-agents in one parallel batch:** Standards, Spec, and the **pr-bug-hunter** agent. Matt's skill covers conventions and spec fit but doesn't hunt for bugs, so the bug hunter is the third axis.
- **Every sub-agent reports in caveman-review's one-line format,** one finding per line. It's short, so it costs fewer tokens, and it's easy to parse:

  ```
  <path>:L<line>: <tag>: <problem>. <fix>. ‖ "<exact quoted line>"
  ```

  Tags: `🔴 bug` (→ P1), `🟡 risk` (→ P2), `🔵 nit` (→ P3), `❓ q` (→ P3). A spec gap with no single line uses `spec:` instead of `<path>:L<line>:` and quotes the spec line. Write this format into each sub-agent's prompt yourself (the prompt itself in full sentences), and pass them the worktree path and `<bundle>/pr.diff`. Don't install or invoke the caveman-review skill: it auto-triggers on "review this PR" and would leak into normal chats.

### 3c. Normalize

Turn all three reports into `<bundle>/findings.json` (create it with the Write tool), mapping each line's tag to a severity. Copy claims faithfully; don't judge them yet, that's the verifier's job.

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

### 3d. Verify

Launch the **pr-verifier** agent. Give it only:

- a findings file;
- the worktree path;
- `<bundle>/pr.diff` and `<bundle>/spec.md`.

**Do not pass the reviewers' reports or your own opinion.** The verifier has to re-derive every finding from the code, not agree with the reviewers.

First split the findings:

```bash
python3 .claude/skills/pr-autoreview/scripts/split-findings.py <bundle>
```

It prints one findings file per line: `findings.json` itself (10 findings or fewer), or `findings.part0.json` to `findings.part2.json` split by file. Launch one **pr-verifier** per printed file, all in one parallel batch. A verifier writes its answer next to its input: `verified.json` for `findings.json`, `verified.partN.json` for `findings.partN.json`. Then:

```bash
python3 .claude/skills/pr-autoreview/scripts/merge-verified.py <bundle>
```

It merges the parts into `verified.json` and checks it parses. If it exits 1, ask the verifier named in the message once to fix its file and run it again; otherwise log the PR as failed.

## 4. Post, one PR at a time

Only after every worker has settled, post. Posting is serial and done by you, so GitHub calls never overlap. For each PR whose worker succeeded, run these two commands in order, using that PR's `<bundle>`:

```bash
python3 .claude/skills/pr-autoreview/scripts/post_review.py <bundle>
python3 .claude/skills/pr-autoreview/scripts/write-context.py <bundle>
```

`post_review.py` posts only `confirmed` or `downgraded` findings, caps nits, moves comments for lines outside the diff into the review body, records state, and prints a one-line JSON result. With `DRY_RUN=true` in config it writes `payload.json` and posts nothing. `write-context.py` writes `<bundle>/context.md` and `<bundle>/brief.json`; don't summarise the PR yourself.

Then, for every PR (including failed ones), remove its git worktree:

```bash
bash .claude/skills/pr-autoreview/scripts/cleanup-pr.sh <clone> <worktree>
```

If a command fails for one PR, log it and continue with the next. The Orca worker worktrees (`review-*`) stay for you to inspect; do not delete them.

## 5. Notify

After the last PR, send the Telegram summary. Pass the bundle of every PR that got a `brief.json`:

```bash
python3 .claude/skills/pr-autoreview/scripts/notify-summary.py <bundle> [<bundle> ...]
```

## 6. Run summary

Write `$STATE_DIR/runs/<today>/summary.md` with the Write tool (one write, after the last PR) and print it. One line per PR:

- the PR and its title;
- what happened (posted / clean / dry run / skipped / failed);
- counts: findings raised → verified → posted;
- anything you had to fall back on (no spec, ticket tools unavailable).

The raised-vs-verified ratio tells the user how noisy the reviewers are, so always include it.
