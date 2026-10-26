#!/usr/bin/env python3
"""pr-status: where every PR stands, plus the latest run of each Orca automation. Read-only, no AI, no network except `orca`.

Per PR (from STATE_DIR/briefs/*/*/findings.json) it counts findings by state:
open (waiting for the Telegram picker), queued (picked for fixing), declined, answered (replied or resolved), informed.
Run:  status.py
"""
import collections
import json
import pathlib
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_my_prs import load_config  # noqa: E402


def latest_runs():
    """{automation name: (status, started at, error)} from Orca's run history, newest run per automation."""
    try:
        run = lambda *a: json.loads(subprocess.run(["orca", "automations", *a, "--json"], capture_output=True,
                                                   text=True, check=True, timeout=60).stdout)["result"]
        names = {a["id"]: a["name"] for a in run("list")["automations"]}
        latest = {}
        for r in run("runs", "--limit", "100")["runs"]:  # newest first
            latest.setdefault(names.get(r["automationId"], r["automationId"][:8]), r)
        return {n: (r["status"], datetime.fromtimestamp(r["scheduledFor"] / 1000).strftime("%m-%d %H:%M"),
                    r.get("error") or "") for n, r in latest.items()}
    except Exception as err:
        print(f"couldn't read Orca runs: {type(err).__name__}", file=sys.stderr)
        return {}


def main():
    state = pathlib.Path(load_config()["STATE_DIR"])
    print("PRs")
    for path in sorted((state / "briefs").glob("*/*/findings.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        counts = collections.Counter(f.get("state", "?") for f in data.get("findings", []))
        print(f"  {data.get('pr', path.parent.name)}  {', '.join(f'{n} {s}' for s, n in sorted(counts.items())) or 'no findings'}")
    print("\nAutomations (latest run; skipped_precheck with exit 1 is normal, timeouts are not)")
    for name, (status, when, err) in sorted(latest_runs().items()):
        print(f"  {name}: {status} {when} {err}".rstrip())


if __name__ == "__main__":
    main()
