"""Run: python3 -m unittest discover -s .claude/skills/pr-autoreview/tests"""
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from prune_state import prune  # noqa: E402

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=30)).isoformat()
RECENT = (NOW - timedelta(days=1)).isoformat()


class PruneTest(unittest.TestCase):
    def run_prune(self, state, states):
        looked_up = []

        def get_state(key):
            looked_up.append(key)
            return states.get(key)

        return prune(state, get_state, NOW), looked_up

    def test_old_merged_and_closed_are_dropped_open_is_kept(self):
        state = {"a/b#1": {"sha": "x", "at": OLD}, "a/b#2": {"sha": "y", "at": OLD},
                 "a/b#3": {"sha": "z", "at": OLD}}
        kept, _ = self.run_prune(state, {"a/b#1": "MERGED", "a/b#2": "CLOSED", "a/b#3": "OPEN"})
        self.assertEqual(list(kept), ["a/b#3"])

    def test_recent_entries_are_kept_without_a_lookup(self):
        kept, looked_up = self.run_prune({"a/b#1": {"sha": "x", "at": RECENT}}, {"a/b#1": "MERGED"})
        self.assertIn("a/b#1", kept)
        self.assertEqual(looked_up, [])

    def test_failed_lookup_or_bad_entry_is_kept(self):
        state = {"a/b#1": {"sha": "x", "at": OLD}, "a/b#2": {"sha": "y"}}
        kept, _ = self.run_prune(state, {})
        self.assertEqual(set(kept), {"a/b#1", "a/b#2"})


if __name__ == "__main__":
    unittest.main()
