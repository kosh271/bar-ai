import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import project_history as history


@unittest.skipUnless(shutil.which("git"), "Git is required for project history")
class ProjectHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "ExampleAI"
        self.project.mkdir()
        self.file = self.project / "project.json"
        self.file.write_text("original\r\n", encoding="utf-8", newline="")

    def test_baseline_checkpoint_and_safe_restore(self):
        history.ensure(self.project)
        baseline = history.list_checkpoints(self.project)[0]
        self.assertEqual(baseline["description"], "Initial project baseline")
        self.file.write_text("changed", encoding="utf-8")
        added = self.project / "new.json"
        added.write_text("new", encoding="utf-8")
        commit = history.checkpoint(self.project, "Adjust strategy")
        self.assertEqual(history.list_checkpoints(self.project)[0]["id"], commit)
        self.file.write_text("uncheckpointed", encoding="utf-8")
        with self.assertRaisesRegex(history.HistoryError, "uncheckpointed"):
            history.restore(self.project, baseline["id"])
        self.file.write_text("changed", encoding="utf-8")
        history.restore(self.project, baseline["id"])
        self.assertEqual(self.file.read_bytes(), b"original\r\n")
        self.assertFalse(added.exists())
        with self.assertRaisesRegex(history.HistoryError, "uncheckpointed"):
            history.restore(self.project, commit)
        history.checkpoint(self.project, "Restore baseline")
        self.assertEqual(len(history.list_checkpoints(self.project)), 3)

    def test_existing_repository_and_staged_changes_are_preserved(self):
        subprocess.run(["git", "init", "--quiet", str(self.project)], check=True)
        with self.assertRaisesRegex(history.HistoryError, "not managed"):
            history.ensure(self.project)
        self.assertFalse((self.project / ".git" / "bar-ai-studio-history").exists())

    def test_staged_changes_are_not_overwritten(self):
        history.ensure(self.project)
        baseline = history.list_checkpoints(self.project)[0]["id"]
        self.file.write_text("staged", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "project.json"], check=True)
        with self.assertRaisesRegex(history.HistoryError, "staged changes"):
            history.checkpoint(self.project, "checkpoint")
        result = subprocess.run(["git", "-C", str(self.project), "diff", "--cached", "--name-only"],
                                check=True, capture_output=True, text=True)
        self.assertIn("project.json", result.stdout)
        with self.assertRaisesRegex(history.HistoryError, "uncheckpointed"):
            history.restore(self.project, baseline)

    def test_ignored_untracked_files_block_restore(self):
        history.ensure(self.project)
        baseline = history.list_checkpoints(self.project)[0]["id"]
        (self.project / ".git" / "info" / "exclude").write_text("private.txt\n", encoding="utf-8")
        (self.project / "private.txt").write_text("do not overwrite", encoding="utf-8")
        with self.assertRaisesRegex(history.HistoryError, "ignored"):
            history.restore(self.project, baseline)
        self.assertEqual((self.project / "private.txt").read_text(encoding="utf-8"), "do not overwrite")

    def test_skip_worktree_file_blocks_checkpoint_and_restore(self):
        history.ensure(self.project)
        baseline = history.list_checkpoints(self.project)[0]["id"]
        subprocess.run(["git", "-C", str(self.project), "update-index", "--skip-worktree", "project.json"], check=True)
        self.file.write_text("external edit", encoding="utf-8")
        with self.assertRaisesRegex(history.HistoryError, "skip-worktree"):
            history.checkpoint(self.project, "checkpoint")
        with self.assertRaisesRegex(history.HistoryError, "skip-worktree"):
            history.restore(self.project, baseline)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "external edit")

    def test_restores_deleted_file_without_attribute_line_ending_conversion(self):
        (self.project / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")
        history.ensure(self.project)
        baseline = history.list_checkpoints(self.project)[0]["id"]
        self.file.unlink()
        history.checkpoint(self.project, "Remove file")
        history.restore(self.project, baseline)
        self.assertEqual(self.file.read_bytes(), b"original\r\n")


if __name__ == "__main__":
    unittest.main()
