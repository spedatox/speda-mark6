# SPDX-FileCopyrightText: 2026 SPEDA
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise the actual replay against isolated Git repositories in OS temp."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("replay.sh").resolve()
BASH = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"


class ReplayTests(unittest.TestCase):
    def git(self, repo, *args, check=True):
        result = subprocess.run(["git", "-C", str(repo), *args],
                                text=True, capture_output=True)
        if check and result.returncode:
            self.fail(f"git {args}: {result.stderr}")
        return result

    def write(self, repo, path, content):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, repo, subject):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", subject)
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="speda-sync-")
        self.addCleanup(self.temp.cleanup)
        self.full = Path(self.temp.name) / "full"
        self.core = Path(self.temp.name) / "core"
        self.git(Path(self.temp.name), "init", "-b", "main", str(self.full))
        self.git(self.full, "config", "user.name", "fixture")
        self.git(self.full, "config", "user.email", "fixture@example.invalid")
        self.git(self.full, "config", "core.autocrlf", "false")
        self.write(self.full, "shared.txt", "a\nb\nc\n")
        self.write(self.full, "README.md", "Full documentation\n")
        self.write(self.full, "multi/deleted.txt", "Full component\n")
        self.base = self.commit(self.full, "baseline")
        self.git(Path(self.temp.name), "-c", "core.autocrlf=false", "clone", "-q",
                 str(self.full), str(self.core))
        self.git(self.core, "config", "user.name", "fixture")
        self.git(self.core, "config", "user.email", "fixture@example.invalid")
        self.git(self.core, "config", "core.autocrlf", "false")
        self.git(self.core, "remote", "add", "full", str(self.full))
        self.git(self.core, "rm", "-r", "multi")
        self.write(self.core, "README.md", "Core documentation\n")
        self.write(self.core, ".github/sync-exclude", "README.md\nmulti\n.github\n")
        self.write(self.core, ".github/sync-state", self.base + "\n")
        self.commit(self.core, "fork")

    def replay(self, **overrides):
        self.git(self.core, "fetch", "-q", "full", "main")
        output = Path(self.temp.name) / "outputs"
        output.write_text("", encoding="utf-8")
        env = dict(os.environ, DRY_RUN="1", PYTHON_BIN=sys.executable,
                   PYTHONDONTWRITEBYTECODE="1", GITHUB_OUTPUT=str(output))
        # A live CI environment's manual input must not leak into fixtures.
        env.pop("SINCE_INPUT", None)
        env.update(overrides)
        result = subprocess.run([BASH, SCRIPT.as_posix()], cwd=self.core,
                                env=env, text=True, capture_output=True)
        self.outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
        return result

    def report(self):
        return json.loads((self.core / ".github/sync-report.json").read_text())

    def test_mixed_change_preserves_core_documents_and_deleted_components(self):
        self.write(self.full, "README.md", "Changed full documentation\n")
        self.write(self.full, "multi/new.txt", "Must stay absent\n")
        self.write(self.full, "shared.txt", "new\nb\nc\n")
        sha = self.commit(self.full, 'mixed "quoted" subject')
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.core / "README.md").read_text(), "Core documentation\n")
        self.assertFalse((self.core / "multi").exists())
        self.assertEqual((self.core / "shared.txt").read_text(), "new\nb\nc\n")
        self.assertEqual(self.report()["last_ok"], sha)
        self.assertEqual(self.report()["carried"], 1)
        self.assertIn("Carried-From:", self.git(self.core, "log", "--format=%B").stdout)

    def test_excluded_only_change_publishes_cursor_advance(self):
        self.write(self.full, "multi/deleted.txt", "Changed deleted file\n")
        sha = self.commit(self.full, "excluded only")
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()["carried"], 0)
        self.assertEqual(self.report()["skipped"], 1)
        self.assertEqual(self.report()["last_ok"], sha)
        self.assertEqual(self.outputs["should_pr"], "true")

    def test_conflict_keeps_cursor_and_rolls_back_partial_application(self):
        self.write(self.core, "shared.txt", "Core\nb\nc\n")
        self.commit(self.core, "Core edit")
        self.write(self.full, "shared.txt", "Full\nb\nc\n")
        self.write(self.full, "new.txt", "Partial application\n")
        blocked = self.commit(self.full, "conflicting change")
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()["blocked"], blocked)
        self.assertEqual(self.report()["last_ok"], self.base)
        self.assertFalse((self.core / "new.txt").exists())
        self.assertEqual((self.core / "shared.txt").read_text(), "Core\nb\nc\n")
        self.assertEqual(self.git(self.core, "status", "--porcelain").stdout, "")

    def test_non_conflict_apply_failure_does_not_advance_cursor(self):
        self.git(self.core, "rm", "shared.txt")
        self.commit(self.core, "Core removed shared path")
        self.write(self.full, "shared.txt", "new\nb\nc\n")
        blocked = self.commit(self.full, "modify missing file")
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()["blocked"], blocked)
        self.assertEqual(self.report()["last_ok"], self.base)
        self.assertFalse((self.core / "shared.txt").exists())

    def test_merge_resolution_is_carried_once(self):
        self.git(self.full, "switch", "-qc", "feature")
        self.write(self.full, "shared.txt", "feature\nb\nc\n")
        self.commit(self.full, "feature edit")
        self.git(self.full, "switch", "-q", "main")
        self.write(self.full, "shared.txt", "main\nb\nc\n")
        self.commit(self.full, "main edit")
        merge = self.git(self.full, "merge", "--no-ff", "feature", check=False)
        self.assertNotEqual(merge.returncode, 0)
        self.write(self.full, "shared.txt", "resolved\nb\nc\n")
        sha = self.commit(self.full, "resolve merge")
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()["carried"], 2)
        self.assertEqual(self.report()["last_ok"], sha)
        self.assertEqual((self.core / "shared.txt").read_text(), "resolved\nb\nc\n")

    def test_invalid_cursor_and_dirty_checkout_are_rejected(self):
        result = self.replay(SINCE_INPUT="does-not-exist")
        self.assertNotEqual(result.returncode, 0)
        self.write(self.core, "scratch.txt", "owner work\n")
        result = self.replay()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.core / "scratch.txt").exists())

    def test_no_new_commits_is_a_real_noop(self):
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs["should_pr"], "false")
        self.assertFalse((self.core / ".github/sync-report.json").exists())


if __name__ == "__main__":
    unittest.main()
