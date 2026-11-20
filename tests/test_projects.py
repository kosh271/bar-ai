import tempfile
import unittest
from pathlib import Path

from app.projects import (
    ProjectError,
    clone_profile,
    create_profile_from_hard,
    create_project,
    list_profiles,
    list_projects,
    profile_files,
)

TEST_DIR = Path(__file__).resolve().parent


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stock = self.root / "stock"
        self.projects = self.root / "projects"
        for directory in (Path("config") / "hard", Path("script") / "hard" / "manager"):
            (self.stock / directory).mkdir(parents=True)
        (self.stock / "config" / "base.json").write_text("// shared JSON", encoding="utf-8")
        (self.stock / "config" / "unit.json").write_text("shared", encoding="utf-8")
        (self.stock / "config" / "hard" / "unit.json").write_text("hard", encoding="utf-8")
        (self.stock / "script" / "common.as").write_text('#include "unit.as"\n', encoding="utf-8")
        (self.stock / "script" / "unit.as").write_text("shared unit\n", encoding="utf-8")
        (self.stock / "script" / "hard" / "init.as").write_text(
            '#include "../common.as"\n', encoding="utf-8"
        )
        (self.stock / "script" / "hard" / "main.as").write_text(
            '#include "manager/work.as"\n', encoding="utf-8"
        )
        (self.stock / "script" / "hard" / "manager" / "work.as").write_text(
            '#include "../../unit.as"\n', encoding="utf-8"
        )

    def test_create_clones_shared_files_and_rewrites_includes(self):
        project = create_project(self.projects, "My AI", self.stock)
        profile = project / "profiles" / "hard"
        self.assertEqual(list_projects(self.projects), [("MyAI", "My AI")])
        self.assertEqual(list_profiles(project), [("hard", "Hard")])
        self.assertEqual((profile / "config" / "base.json").read_text(), "// shared JSON")
        self.assertEqual((profile / "config" / "unit.json").read_text(), "hard")
        self.assertEqual((profile / "script" / "init.as").read_text(), '#include "__shared__/common.as"\n')
        self.assertEqual((profile / "script" / "manager" / "work.as").read_text(),
                         '#include "../__shared__/unit.as"\n')
        self.assertEqual((profile / "script" / "__shared__" / "common.as").read_text(),
                         '#include "unit.as"\n')
        self.assertIn(Path("script") / "__shared__" / "unit.as", profile_files(profile))

    def test_profile_copies_are_independent(self):
        project = create_project(self.projects, "My AI", self.stock)
        source = project / "profiles" / "hard"
        cloned = clone_profile(project, "My Custom Profile", "hard")
        fresh = create_profile_from_hard(project, "Fresh Hard", self.stock)
        (source / "script" / "__shared__" / "unit.as").write_text("changed", encoding="utf-8")
        self.assertEqual((cloned / "script" / "__shared__" / "unit.as").read_text(), "shared unit\n")
        self.assertEqual((fresh / "script" / "__shared__" / "unit.as").read_text(), "shared unit\n")
        self.assertIn(("my-custom-profile", "My Custom Profile"), list_profiles(project))

    def test_missing_include_fails_without_partial_project(self):
        (self.stock / "script" / "hard" / "init.as").write_text(
            '#include "../missing.as"\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(ProjectError, "Script include not found"):
            create_project(self.projects, "My AI", self.stock)
        self.assertEqual(list(self.projects.iterdir()), [])

    def test_include_outside_stock_script_tree_is_rejected(self):
        (self.stock / "outside.as").write_text("outside", encoding="utf-8")
        (self.stock / "script" / "hard" / "init.as").write_text(
            '#include "../../outside.as"\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(ProjectError, "Script include not found"):
            create_project(self.projects, "My AI", self.stock)
        self.assertEqual(list(self.projects.iterdir()), [])

    def test_duplicate_and_reserved_names_are_rejected(self):
        create_project(self.projects, "My AI", self.stock)
        with self.assertRaisesRegex(ProjectError, "already exists"):
            create_project(self.projects, "my ai", self.stock)
        with self.assertRaisesRegex(ProjectError, "reserved Windows"):
            create_project(self.projects, "CON", self.stock)


if __name__ == "__main__":
    unittest.main()
