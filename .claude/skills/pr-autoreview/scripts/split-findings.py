#!/usr/bin/env python3
"""Split a PR's findings across up to 3 verifiers, grouped by file. No AI, no tokens.

Usage: split-findings.py <bundle>

Prints the findings files to hand to verifiers, one path per line. With 10 findings or fewer
that's just findings.json; with more it writes findings.part0..2.json and prints those.
Each verifier writes its answer next to its input as verified.partN.json (see merge-verified.py).
"""
import json
import pathlib
import sys

MAX_ONE_VERIFIER = 10
MAX_PARTS = 3


def main():
    bundle = pathlib.Path(sys.argv[1])
    data = json.loads((bundle / "findings.json").read_text())
    findings = data["findings"]
    if len(findings) <= MAX_ONE_VERIFIER:
        print(bundle / "findings.json")
        return
    by_file = {}
    for f in findings:
        by_file.setdefault(f.get("path"), []).append(f)
    parts = [[] for _ in range(MAX_PARTS)]
    for group in sorted(by_file.values(), key=len, reverse=True):  # biggest file first, into the emptiest part
        min(parts, key=len).extend(group)
    for i, part in enumerate(p for p in parts if p):
        path = bundle / f"findings.part{i}.json"
        path.write_text(json.dumps({**data, "findings": part}, indent=2))
        print(path)


if __name__ == "__main__":
    main()
