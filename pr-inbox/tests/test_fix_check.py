"""Run: python3 -m unittest discover -s pr-inbox/tests"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fix_check import check_finding, classify, hunks  # noqa: E402

DIFF = "diff --git a/x b/x\n@@ -10,2 +10,3 @@ def f():\n+new\n@@ -40 +41 @@\n-old\n+new\n"


class ClassifyTest(unittest.TestCase):
    def test_hunk_ranges_read_old_file_lines(self):
        self.assertEqual(hunks(DIFF), [(10, 11), (40, 40)])

    def test_change_near_the_commented_line(self):
        self.assertEqual(classify(DIFF, 13), "touched near line 13")

    def test_change_elsewhere_in_the_file(self):
        self.assertEqual(classify(DIFF, 100), "file changed, but not near line 100")

    def test_empty_diff_means_not_touched(self):
        self.assertEqual(classify("", 13), "not touched")


class CheckFindingTest(unittest.TestCase):
    def test_uncommitted_edit_in_a_real_repo(self):
        with tempfile.TemporaryDirectory() as repo:
            git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout
            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            f = pathlib.Path(repo, "a.txt")
            f.write_text("\n".join(f"line{i}" for i in range(1, 101)) + "\n")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            sha = git("rev-parse", "HEAD").strip()
            self.assertEqual(check_finding(repo, sha, "a.txt:50"), "not touched")
            f.write_text(f.read_text().replace("line52", "changed"))
            self.assertEqual(check_finding(repo, sha, "a.txt:50"), "touched near line 50")
            self.assertEqual(check_finding(repo, sha, "a.txt:90"), "file changed, but not near line 90")
            self.assertIn("can't compare", check_finding(repo, "0" * 40, "a.txt:50"))
            self.assertEqual(check_finding(repo, sha, "review summary"), "no file:line to check")


if __name__ == "__main__":
    unittest.main()
