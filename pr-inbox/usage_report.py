#!/usr/bin/env python3
"""usage-report: tokens used by the Orca automation runs, per automation and model. Read-only, no AI, no network.

Reads the Claude Code transcripts of every run in ~/.claude/projects/*orca-workspaces-claude-automations-*,
subagents included. Cost is not calculated because prices change; compare token counts instead.
To see what a change saved, run it for the period before and after:
  usage_report.py --until 2026-10-26     before the change (that day excluded)
  usage_report.py --since 2026-10-26     after it
Run:  usage_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""
import argparse
import collections
import json
import pathlib
import re

RUN_DIR = re.compile(r"claude-automations-(?P<name>.+)-run-\d+-(?P<day>\d{8})T\d{4}$")


def collect(since, until):
    """{(automation, model): Counter} and {automation: number of runs}."""
    totals, runs = collections.defaultdict(collections.Counter), collections.defaultdict(int)
    for run_dir in (pathlib.Path.home() / ".claude/projects").glob("*orca-workspaces-claude-automations-*"):
        m = RUN_DIR.search(run_dir.name)
        if not m:
            continue
        day = f"{m['day'][:4]}-{m['day'][4:6]}-{m['day'][6:]}"
        if (since and day < since) or (until and day >= until):
            continue
        runs[m["name"]] += 1
        calls = {}  # message id -> (model, usage); streaming repeats a message, the last copy is the final one
        for path in run_dir.rglob("*.jsonl"):
            for line in path.open():
                try:
                    msg = json.loads(line).get("message") or {}
                except ValueError:
                    continue
                if msg.get("id") and msg.get("usage") and msg.get("model") != "<synthetic>":
                    calls[msg["id"]] = (msg["model"], msg["usage"])
        for model, usage in calls.values():
            t = totals[(m["name"], model)]
            t["calls"] += 1
            t["new input"] += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            t["cached input"] += usage.get("cache_read_input_tokens", 0)
            t["output"] += usage.get("output_tokens", 0)
    return totals, runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since")
    parser.add_argument("--until")
    args = parser.parse_args()
    totals, runs = collect(args.since, args.until)
    if not totals:
        print("No automation runs found for that period.")
        return
    print(f"{'automation':<28}{'model':<22}{'runs':>5}{'calls':>7}{'new input':>13}{'cached input':>14}{'output':>11}")
    for (name, model), t in sorted(totals.items()):
        print(f"{name:<28}{model:<22}{runs[name]:>5}{t['calls']:>7,}{t['new input']:>13,}"
              f"{t['cached input']:>14,}{t['output']:>11,}")


if __name__ == "__main__":
    main()
