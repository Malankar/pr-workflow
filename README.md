# claude-automations

Your unattended Claude workflows, in one repo. Orca automations point at this repo, so its `CLAUDE.md`, model setting, skills and token-saving rules apply only here. Your normal chats never see them.

```
claude-automations/
├── CLAUDE.md                       unattended rules + ultracave voice (this repo only)
├── .claude/settings.json           Sonnet by default; GitHub writes blocked for Claude
├── .claude/skills/pr-autoreview/   reviews PRs waiting on you (3×/day)
├── .claude/agents/                 pr-bug-hunter, pr-verifier
├── pr-inbox/check_my_prs.py        new comments on YOUR PRs (every 2h, no AI)
└── global/pr-brief/                interactive hand-off skill, installed to ~/.claude
```

## Setup

```bash
bash setup.sh
```

Then follow its two printed steps. Add this folder to Orca as a project.

## Workflow 1: auto-review PRs waiting on you

```bash
orca automations create \
  --name "PR auto-review" \
  --trigger "0 10,14,18 * * 1-5" \
  --timezone Asia/Kolkata \
  --prompt "Run the pr-autoreview skill." \
  --provider claude \
  --repo claude-automations
```

Set the automation's **precheck** to `bash .claude/skills/pr-autoreview/scripts/find-prs.sh`. Settings are in `.claude/skills/pr-autoreview/config.env`; it starts with `DRY_RUN=true`.

## Workflow 2: comments on your own PRs

Every 2 hours, `check_my_prs.py` looks at your open PRs. For any comment you haven't been told about yet, it sends a notification (macOS, Linux and/or Slack) and writes `~/.pr-inbox/inbox.md`.

When you want help, hand off from any session:

```
/pr-brief acme/shop#42
```

You get what the PR does, what changed, what each reviewer is flagging, and a verdict on each point: valid, partly, not valid, or your call. Nothing is fixed, replied to or resolved until you say so.

The notifier needs no AI, so run it as a precheck that never starts the agent:

```bash
orca automations create \
  --name "PR inbox" \
  --trigger "0 9-21/2 * * 1-5" \
  --timezone Asia/Kolkata \
  --prompt "For each PR in ~/.pr-inbox/brief-queue.json, run /pr-brief and save the brief to ~/.pr-inbox/briefs/<pr>/brief.md. Don't fix, reply or resolve anything." \
  --provider claude \
  --repo claude-automations
```

Set the precheck to `python3 pr-inbox/check_my_prs.py`.

- **Default, `AUTO_BRIEF=false`:** the precheck always exits 1, so you get notifications and spend zero tokens.
- **`AUTO_BRIEF=true`:** when something's new, the agent pre-writes the briefs so they're waiting for you. This costs tokens even for comments you'd have handled yourself.

The first run marks existing comments as read, so you aren't flooded.

## Where the token savings come from (biggest first)

1. **Prechecks.** No work means no agent run at all. The inbox in notify-only mode is free.
2. **Scripts do every yes/no step:** filtering, state, diff checks, posting. Claude only does judgement.
3. **This repo's scope.** A short `CLAUDE.md` and Sonnet by default; only the verifier uses Opus. The automation skills don't load into your normal chats.
4. **Terse internal traffic.** Reviewer reports use caveman-review's one-line format, and the orchestrator talks in `/ultracave`. Human-facing text stays in normal English.

Honest note on #4: the caveman voice only shortens output. Agent runs are mostly input (code, diffs, tool results), and its rules add about 1,160 input tokens. Expect a small win, and check `/usage` after a week.

Considered and left out:

- **`--bare`:** skips loading hooks, skills, plugins, MCP servers and CLAUDE.md, but it requires an API key, and OAuth and keychain logins are disabled. With a Pro/Max login in Orca it won't run.
- **caveman's full CLI:** it compresses what the agent reads too, but it caveman's every `claude` session on your machine.
