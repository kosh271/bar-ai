import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import project_archive, projects

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

    def test_project_import_refuses_undeployable_identifiers(self):
        for short_name, profile_key in (("Example", "Hard"), ("Example", "hard--fast"), ("BARb", "hard")):
            with self.subTest(short_name=short_name, profile_key=profile_key):
                bundle = io.BytesIO()
                with zipfile.ZipFile(bundle, "w") as archive:
                    archive.writestr("project.json", json.dumps({
                        "name": "Example", "short_name": short_name, "default_profile": profile_key,
                    }))
                    prefix = f"profiles/{profile_key}"
                    archive.writestr(f"{prefix}/profile.json", '{"name":"Hard"}')
                    for name, data in (("config/settings.json", "{}"), ("script/init.as", ""), ("script/main.as", "")):
                        archive.writestr(f"{prefix}/{name}", data)
                with self.assertRaises(ProjectError):
                    project_archive.load(self.projects, bundle)
                self.assertFalse((self.projects / short_name).exists())

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

    def test_zip_export_omits_history_and_staging(self):
        project = create_project(self.projects, "My AI", self.stock)
        (project / ".git").mkdir()
        (project / ".git" / "config").touch()
        (project / ".staging-old").mkdir()
        (project / ".staging-old" / "private.txt").touch()
        (project / "save.tmp").touch()
        output = io.BytesIO()
        project_archive.write(project, output)
        with zipfile.ZipFile(output) as archive:
            self.assertIn("project.json", archive.namelist())
            self.assertTrue(all(not name.startswith((".git", ".staging-")) for name in archive.namelist()))
            self.assertNotIn("save.tmp", archive.namelist())
        with self.assertRaisesRegex(ProjectError, "already exists"):
            project_archive.load(self.projects, output)

    def test_zip_import_refuses_unsafe_members_and_cleans_staging(self):
        link = zipfile.ZipInfo("profiles/current")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entries in (
            ["../escaped.txt"], ["C:/outside.txt"], ["payload/NUL.txt"], [".git/config"],
            ["Profiles/file", "profiles/other"], ["file", "file/child"], [link],
        ):
            with self.subTest(entries=entries):
                blob = io.BytesIO()
                with zipfile.ZipFile(blob, "w") as archive:
                    for name in entries:
                        archive.writestr(name, "data")
                with self.assertRaises(ProjectError):
                    project_archive.load(self.projects, blob)
                self.assertEqual(list(self.projects.iterdir()), [])
                self.assertFalse((self.projects / "escaped.txt").exists())
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("large", "0123456789")
        with patch.object(projects, "MAX_WORKSPACE_FILE_BYTES", 5):
            with self.assertRaisesRegex(ProjectError, "size limit"):
                project_archive.load(self.projects, blob)
        self.assertEqual(list(self.projects.iterdir()), [])

    def test_zip_import_refuses_invalid_unicode_before_publication(self):
        files = {
            "project.json": '{"name":"My AI","short_name":"MyAI","default_profile":"hard"}',
            "profiles/hard/profile.json": '{"name":"Hard"}',
            "profiles/hard/config/test.json": "{}",
            "profiles/hard/script/init.as": "// init",
            "profiles/hard/script/main.as": "// main",
        }
        for path, bad in (
            ("project.json", r'{"name":"My AI\ud800","short_name":"MyAI","default_profile":"hard"}'),
            ("profiles/hard/profile.json", r'{"name":"Hard\ud800"}'),
            ("profiles/hard/script/main.as", b"\xff"),
        ):
            with self.subTest(path=path):
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w") as output:
                    for name, content in {**files, path: bad}.items():
                        output.writestr(name, content)
                with self.assertRaisesRegex(ProjectError, "Unicode|UTF-8"):
                    project_archive.load(self.projects, archive)
                self.assertEqual(list(self.projects.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
