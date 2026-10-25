#!/usr/bin/env python3
"""Merge the verifiers' outputs into verified.json and check it parses. No AI, no tokens.

Usage: merge-verified.py <bundle>

Merges verified.part*.json if there are any, otherwise just checks verified.json.
Exit 1 and a message naming the file when something is missing or isn't valid JSON.
"""
import json
import pathlib
import sys


def read(path):
    try:
        data = json.loads(path.read_text())
        data["findings"], data["rejected"]
        return data
    except Exception as err:
        sys.exit(f"{path}: {type(err).__name__} {err}")


def main():
    bundle = pathlib.Path(sys.argv[1])
    parts = sorted(bundle.glob("verified.part*.json"))
    if parts:
        merged = {"findings": [], "rejected": []}
        for path in parts:
            data = read(path)
            merged["findings"] += data["findings"]
            merged["rejected"] += data["rejected"]
        (bundle / "verified.json").write_text(json.dumps(merged, indent=2))
    else:
        merged = read(bundle / "verified.json")
    print(f"{len(merged['findings'])} verified, {len(merged['rejected'])} rejected")


if __name__ == "__main__":
    main()
