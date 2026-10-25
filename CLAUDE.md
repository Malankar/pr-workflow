# Unattended automations

Everything in this repo runs from scheduled Orca automations. Nobody is watching the session, so every token here is overhead.

## Voice

Use the `/ultracave` voice (caveman skill, installed in this repo only) for:

- your own narration and tool-call chatter;
- reports that sub-agents send back.

Write normal, complete English for:

- anything a human reads: PR comment text (`title` and `body` in `verified.json`), run summaries, notifications;
- prompts you write for sub-agents. Compressed prompts make answers worse, so give sub-agents clear, full instructions.

## Rules

- Never ask a question. Take the documented fallback and note it in the run summary. The one exception: `ask-prs.py` asks which PRs to review over Telegram.
- Never comment, review, approve, merge or push yourself. Only the scripts in this repo talk to GitHub.
- Read less: start with `git diff --stat`, then read only the hunks and line ranges you need. Don't read whole large files.
