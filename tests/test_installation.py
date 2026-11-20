import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.installation import (
    BEGIN,
    LOBBY_FILE,
    InstallationError,
    _lobby_content,
    install,
    remove,
)
from app.projects import clone_profile, create_project


TEST_DIR = Path(__file__).resolve().parent
AI_INFO = """local infos = {
{ key = 'shortName', value = 'BARb' },
{ key = 'version', value = 'stable' },
{ key = 'name', value = 'BARbarIAn' },
}
return infos
"""
AI_OPTIONS = """local options = {
{key='profile', def='hard'},
{key='game_config', def=true},
}
return options
"""
LOBBY = """local subnameMap = { ['Existing 1'] = 'Existing' }
local function GetAiSimpleName(name)
    return subnameMap[name]
end
local simpleAiOrder = { ['Existing 1'] = 7 }
local aiTooltip = { ['Existing 1'] = 'Kept' }
return { GetAiSimpleName = GetAiSimpleName, simpleAiOrder = simpleAiOrder, aiTooltip = aiTooltip }
"""


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.engine = "engine1"
        self.stock = self.data / "engine" / self.engine / "AI" / "Skirmish" / "BARb" / "stable"
        self.stock.mkdir(parents=True)
        (self.stock / "AIInfo.lua").write_text(AI_INFO, encoding="utf-8")
        (self.stock / "AIOptions.lua").write_text(AI_OPTIONS, encoding="utf-8")
        (self.stock / "SkirmishAI.dll").write_bytes(b"stock DLL")
        source = root / "source"
        for directory in ("config/hard", "script/hard"):
            (source / directory).mkdir(parents=True)
        (source / "config" / "base.json").write_text("{}", encoding="utf-8")
        (source / "script" / "hard" / "init.as").write_text("init", encoding="utf-8")
        (source / "script" / "hard" / "main.as").write_text("main", encoding="utf-8")
        self.project = create_project(root / "projects", "My AI", source)
        self.target = self.stock.parent.parent / "MyAI" / "1"
        self.lobby = self.data / LOBBY_FILE

    def test_install_update_and_remove_keep_project_and_other_lobby_entries(self):
        self.lobby.parent.mkdir(parents=True)
        self.lobby.write_text(LOBBY, encoding="utf-8")
        clone_profile(self.project, "Fast", "hard")
        confirmations = []
        def confirm(message):
            confirmations.append(message)
            return True

        result = install(self.project, self.data, self.engine, confirm)
        self.assertEqual(result.path, self.target)
        self.assertTrue(result.changed)
        self.assertEqual(len(confirmations), 1)
        self.assertEqual((self.target / "SkirmishAI.dll").read_bytes(), b"stock DLL")
        info = (self.target / "AIInfo.lua").read_text(encoding="utf-8")
        self.assertIn("value = 'MyAI'", info)
        self.assertIn("value = '1'", info)
        options = (self.target / "AIOptions.lua").read_text(encoding="utf-8")
        self.assertIn("key = 'fast', name = 'Fast'", options)
        self.assertIn("option.def = false", options)
        self.assertTrue((self.target / "script" / "fast" / "main.as").is_file())
        self.assertIn("['Existing 1'] = 'Existing'", self.lobby.read_text())
        self.assertIn("subnameMap['MyAI 1'] = 'My AI'", self.lobby.read_text())
        self.assertEqual((self.stock / "AIInfo.lua").read_text(), AI_INFO)

        unchanged = install(
            self.project, self.data, self.engine,
            lambda _message: self.fail("unexpected confirmation"),
        )
        self.assertEqual(unchanged.path, self.target)
        self.assertFalse(unchanged.changed)

        (self.project / "profiles" / "hard" / "script" / "main.as").write_text("edited")
        updated = install(self.project, self.data, self.engine, lambda _message: self.fail("unexpected confirmation"))
        self.assertTrue(updated.changed)
        self.assertEqual((self.target / "script" / "hard" / "main.as").read_text(), "edited")
        remove(self.project, self.data, self.engine, lambda _message: self.fail("unexpected confirmation"))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.lobby.read_text(), LOBBY)
        self.assertTrue(self.project.is_dir())

    def test_new_lobby_override_is_removed_after_last_ai(self):
        install(self.project, self.data, self.engine, lambda _message: True)
        self.assertIn("VFS.ZIP", self.lobby.read_text())
        remove(self.project, self.data, self.engine, lambda _message: True)
        self.assertFalse(self.lobby.exists())

    def test_unmanaged_and_modified_ai_requires_confirmation(self):
        self.target.mkdir(parents=True)
        (self.target / "important").write_text("keep")
        with self.assertRaisesRegex(InstallationError, "cancelled"):
            install(self.project, self.data, self.engine, lambda _message: False)
        self.assertEqual((self.target / "important").read_text(), "keep")
        install(self.project, self.data, self.engine, lambda _message: True)
        (self.target / "script" / "hard" / "main.as").write_text("local change")
        with self.assertRaisesRegex(InstallationError, "cancelled"):
            remove(self.project, self.data, self.engine, lambda _message: False)
        self.assertEqual((self.target / "script" / "hard" / "main.as").read_text(), "local change")

    def test_unsupported_shared_file_is_never_replaced(self):
        self.lobby.parent.mkdir(parents=True)
        self.lobby.write_text("return { custom = true }\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallationError, "unsupported structure"):
            install(self.project, self.data, self.engine, lambda _message: True)
        self.assertEqual(self.lobby.read_text(), "return { custom = true }\n")
        self.assertFalse(self.target.exists())

    def test_existing_lobby_entry_cannot_be_taken_over(self):
        existing = LOBBY.replace("Existing 1", "MyAI 1")
        with self.assertRaisesRegex(InstallationError, "unmanaged entry"):
            _lobby_content(existing, "MyAI 1", "New")

    def test_lobby_write_failure_rolls_back_existing_ai(self):
        install(self.project, self.data, self.engine, lambda _message: True)
        (self.project / "profiles" / "hard" / "script" / "main.as").write_text("updated")
        original_info = (self.target / "AIInfo.lua").read_text()
        original_lobby = self.lobby.read_text()
        with patch("app.installation._write_lobby", side_effect=OSError("locked")):
            with self.assertRaisesRegex(OSError, "locked"):
                install(self.project, self.data, self.engine, lambda _message: True)
        self.assertEqual((self.target / "AIInfo.lua").read_text(), original_info)
        self.assertEqual(self.lobby.read_text(), original_lobby)
        self.assertEqual([path.name for path in self.target.parent.iterdir()], ["1"])

    def test_locked_ai_is_not_partially_replaced(self):
        install(self.project, self.data, self.engine, lambda _message: True)
        (self.project / "profiles" / "hard" / "script" / "main.as").write_text("updated")
        original_info = (self.target / "AIInfo.lua").read_text()
        original_lobby = self.lobby.read_text()
        with patch.object(Path, "rename", side_effect=PermissionError("DLL locked")):
            with self.assertRaisesRegex(PermissionError, "DLL locked"):
                install(self.project, self.data, self.engine, lambda _message: True)
        self.assertEqual((self.target / "AIInfo.lua").read_text(), original_info)
        self.assertEqual(self.lobby.read_text(), original_lobby)
        self.assertEqual([path.name for path in self.target.parent.iterdir()], ["1"])

    def test_failed_rollback_retains_the_previous_installation(self):
        install(self.project, self.data, self.engine, lambda _message: True)
        (self.project / "profiles" / "hard" / "script" / "main.as").write_text("updated")
        original_rename = Path.rename
        calls = 0

        def fail_rollback(path, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("rollback locked")
            return original_rename(path, destination)

        with patch("app.installation._write_lobby", side_effect=OSError("lobby locked")):
            with patch.object(Path, "rename", fail_rollback):
                with self.assertRaisesRegex(InstallationError, "retained for recovery"):
                    install(self.project, self.data, self.engine, lambda _message: True)
        staging = list(self.target.parent.glob(".bar-ai-stage-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual((staging[0] / "previous" / "SkirmishAI.dll").read_bytes(), b"stock DLL")

    def test_concurrent_change_is_not_overwritten(self):
        self.target.mkdir(parents=True)
        important = self.target / "important"
        important.write_text("before")

        def confirm(_message):
            important.write_text("changed by another process")
            return True

        with self.assertRaisesRegex(InstallationError, "changed during installation"):
            install(self.project, self.data, self.engine, confirm)
        self.assertEqual(important.read_text(), "changed by another process")
        self.assertFalse(self.lobby.exists())

    def test_lobby_entry_remains_while_another_engine_has_this_ai(self):
        other = self.data / "engine" / "engine2" / "AI" / "Skirmish" / "BARb" / "stable"
        other.mkdir(parents=True)
        for name in ("AIInfo.lua", "AIOptions.lua", "SkirmishAI.dll"):
            (other / name).write_bytes((self.stock / name).read_bytes())
        install(self.project, self.data, self.engine, lambda _message: True)
        install(self.project, self.data, "engine2", lambda _message: True)
        remove(self.project, self.data, self.engine, lambda _message: True)
        self.assertIn("MyAI 1", self.lobby.read_text())
        remove(self.project, self.data, "engine2", lambda _message: True)
        self.assertFalse(self.lobby.exists())

    def test_damaged_editor_entries_are_not_overwritten(self):
        install(self.project, self.data, self.engine, lambda _message: True)
        content = self.lobby.read_text().replace(BEGIN, BEGIN + " modified")
        self.lobby.write_text(content, encoding="utf-8")
        with self.assertRaisesRegex(InstallationError, "modified"):
            install(self.project, self.data, self.engine, lambda _message: True)
        self.assertEqual(self.lobby.read_text(), content)

    def test_other_version_or_interrupted_transaction_blocks_changes(self):
        self.target.parent.mkdir(parents=True)
        (self.target.parent / "other").mkdir()
        with self.assertRaisesRegex(InstallationError, "Another version"):
            install(self.project, self.data, self.engine, lambda _message: True)
        (self.target.parent / "other").rmdir()
        (self.target.parent / ".bar-ai-stage-recovery").mkdir()
        with self.assertRaisesRegex(InstallationError, "interrupted"):
            install(self.project, self.data, self.engine, lambda _message: True)


if __name__ == "__main__":
    unittest.main()
