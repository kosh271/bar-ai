import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.__main__ import Editor
from app.discovery import DiscoveryError, discover, engine_readiness
from app.headless import _diagnostics
from app.settings import (
    EditorState, Settings, SettingsError, load_editor_state, load_settings, load_studio_preferences,
    save_editor_state, save_settings, save_studio_preferences,
)

TEST_DIR = Path(__file__).resolve().parent


class StudioPreferenceTests(unittest.TestCase):
    def test_sweep_draft_persists_map_pool_and_rejects_invalid_maps(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temporary:
            path = Path(temporary) / "studio-preferences.json"
            sweep = {"game": "BAR", "maps": ["Map v1", "Map v2"], "minutes": 30,
                     "game_limit": 10, "seconds_limit": 71, "baseline": ["OtherAI", "stable", ""],
                     "values": "2, 4", "seed": 0}
            save_studio_preferences(path, {"sweep": sweep})
            with self.assertRaises(SettingsError):
                save_studio_preferences(path, {"sweep": {**sweep, "maps": ["Map v1", "Map v1"]}})
            self.assertEqual(load_studio_preferences(path), {"sweep": sweep})

    def test_preferences_merge_and_reject_invalid_replacements(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temporary:
            path = Path(temporary) / "studio-preferences.json"
            workspace = {"project": "ExampleAI", "profile": "my-profile"}
            match = {
                "game": "BAR", "map": "A map", "first": ["BARb", "stable", "hard"],
                "second": ["OtherAI", "1", ""], "faction1": "Armada", "faction2": "Legion",
                "minutes": 55, "skip_smoke": True,
            }
            self.assertEqual(load_studio_preferences(path), {})
            save_studio_preferences(path, {"workspace": workspace})
            save_studio_preferences(path, {"match": match})
            self.assertEqual(load_studio_preferences(path), {"workspace": workspace, "match": match})
            for update in (
                {"match": {**match, "skip_smoke": 1}},
                {"match": {**match, "minutes": True}},
                {"match": {**match, "first": ["BARb", "stable"]}},
                {"workspace": {**workspace, "project": "../not-a-project"}},
            ):
                with self.subTest(update=update), self.assertRaises(SettingsError):
                    save_studio_preferences(path, update)
            self.assertEqual(load_studio_preferences(path), {"workspace": workspace, "match": match})
            path.write_text("incomplete JSON", encoding="utf-8")
            self.assertEqual(load_studio_preferences(path), {})


    def test_tournament_draft_merges_with_match_and_workspace(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temporary:
            path = Path(temporary) / "studio-preferences.json"
            workspace = {"project": "ExampleAI", "profile": "hard"}
            tournament = {
                "game": "BAR", "maps": ["Map v1", "Map2"], "minutes": 30,
                "game_limit": 12, "seconds_limit": 1000,
                "players": [
                    {"ai": ["ExampleAI", "1", "hard"], "faction": "Rotate"},
                    {"ai": ["OtherAI", "stable", ""], "faction": "Random"},
                ],
            }
            save_studio_preferences(path, {"workspace": workspace})
            save_studio_preferences(path, {"tournament": tournament})
            with self.assertRaises(SettingsError):
                save_studio_preferences(path, {"tournament": {**tournament, "game_limit": 101}})
            self.assertEqual(load_studio_preferences(path), {"workspace": workspace, "tournament": tournament})
            draft = {**tournament, "maps": [], "players": []}
            save_studio_preferences(path, {"tournament": draft})
            self.assertEqual(load_studio_preferences(path)["tournament"], draft)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.engine = self.data / "engine" / "recoil_1"
        self.engine.mkdir(parents=True)

    def write_json(self, name, contents):
        (self.data / name).write_text(json.dumps(contents), encoding="utf-8")

    def test_detects_launcher_active_engine_and_readiness(self):
        self.write_json("launcher_cfg.json", {"config": "manual-win"})
        self.write_json("config.json", {"setups": [
            {"package": {"id": "manual-win"}, "launch": {"engine": "recoil_1"}}
        ]})
        (self.engine / "spring-headless.exe").touch()
        dll = self.engine / "AI" / "Skirmish" / "BARb" / "stable" / "SkirmishAI.dll"
        dll.parent.mkdir(parents=True)
        dll.touch()
        result = discover(self.root)
        self.assertEqual(result.active_engine, "recoil_1")
        self.assertEqual(engine_readiness(result, "recoil_1"), ())

    def test_does_not_guess_active_engine_without_launcher_selection(self):
        self.write_json("launcher_cfg.json", {"checkForUpdates": True})
        result = discover(self.root)
        self.assertIsNone(result.active_engine)
        self.assertIn("verify the selected engine", result.active_engine_message)
        self.assertEqual(result.engines, ("recoil_1",))
        self.assertEqual(len(engine_readiness(result, "recoil_1")), 2)

    def test_inspect_selects_latest_version_not_launcher_active_engine(self):
        (self.data / "engine" / "recoil_9").mkdir()
        (self.data / "engine" / "recoil_10").mkdir()
        self.write_json("launcher_cfg.json", {"config": "manual-win"})
        self.write_json("config.json", {"setups": [
            {"package": {"id": "manual-win"}, "launch": {"engine": "recoil_1"}}
        ]})
        editor = MagicMock()
        editor.root_path.get.return_value = str(self.root)
        editor.engine_box = {}
        Editor.inspect(editor)
        editor.engine.set.assert_called_with("recoil_10")
        self.assertEqual(editor.installation.active_engine, "recoil_1")

    def test_startup_keeps_saved_engine_when_available(self):
        (self.data / "engine" / "recoil_10").mkdir()
        editor = MagicMock()
        editor.root_path.get.return_value = str(self.root)
        editor.engine_box = {}
        Editor.inspect(editor, "recoil_1")
        editor.engine.set.assert_called_with("recoil_1")

    def test_editing_installation_path_invalidates_project_source(self):
        editor = MagicMock()
        editor.installation = discover(self.root)
        editor.root_path.get.return_value = str(self.root / "other")
        editor.engine_box = {}
        Editor._root_changed(editor)
        self.assertIsNone(editor.installation)
        editor.project_editor.set_installation.assert_called_once_with(None, None)

    def test_active_engine_must_exist(self):
        self.write_json("launcher_cfg.json", {"config": "manual-win"})
        self.write_json("config.json", {"setups": [
            {"package": {"id": "manual-win"}, "launch": {"engine": "not-installed"}}
        ]})
        with self.assertRaisesRegex(DiscoveryError, "not installed"):
            discover(self.root)

    def test_invalid_launcher_json_is_reported(self):
        (self.data / "launcher_cfg.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(DiscoveryError, "Cannot read"):
            discover(self.root)


class SettingsTests(unittest.TestCase):
    def test_save_and_load_settings(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as directory:
            path = Path(directory) / "data" / "settings.json"
            self.assertIsNone(load_settings(path))
            expected = Settings("C:\\BAR", "recoil_1")
            save_settings(path, expected)
            self.assertEqual(load_settings(path), expected)

    def test_invalid_settings_are_reported(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"bar_root": "C:\\\\BAR"}', encoding="utf-8")
            with self.assertRaisesRegex(SettingsError, "engine are required"):
                load_settings(path)

    def test_editor_state_persists_hard_source_and_reads_older_state(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as directory:
            path = Path(directory) / "editor-state.json"
            expected = EditorState("ai", "profile", "config.json", "Game (byar:stable)", True)
            save_editor_state(path, expected)
            self.assertEqual(load_editor_state(path), expected)

            path.write_text('{"project": "", "profile": "", "file": ""}', encoding="utf-8")
            self.assertEqual(load_editor_state(path), EditorState("", "", "", "", False))


class DiagnosticTests(unittest.TestCase):
    def test_suppresses_known_game_messages_from_both_logs(self):
        messages = [
            "Error: [StoreSkirmishAILibInfo] required AI Interface for Skirmish AI   not found",
            'Error: [SetConfigInt] key "AdvSky" is deprecated',
            "[weapondefs.lua] Error: removed ceg_test_projectile_unit_ceg_test_projectile weaponDef, missing model",
            "Error: Could not finalize groundFX texture atlas. Use fewer/smaller textures.",
            "ERROR: PBR enabler: gl.CreateShader is nil",
            'Error in Initialize(): [string "luaui/Widgets/gui_pip/gui_pip.lua"]:9371: '
            "attempt to call field 'CreateShader' (a nil value)",
            "LuaShader: [AoE Napalm Shader] shader error(s):",
            "LuaShader: [Reclaim Field GL4] shader error(s):",
            "Error in Shutdown()",
            'Error in Shutdown(): [string "LuaUI/Widgets/gui_sensor_ranges_jammer_preview.lua"]:1581: '
            "attempt to index field 'sheetShader' (a nil value)",
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_brown_c_xs"',
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_green_a_xxl"',
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_brown_c_l"',
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_green_a_xl"',
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_brown_b_xl"',
            'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_ad0_green_c_m"',
        ]
        log = "\n".join(f"[t=00:00:02.326696][f=-000001] {message}" for message in messages)
        output = "\n".join(f"[t=00:01:07.5][f=000120] {message}" for message in messages)
        self.assertEqual(_diagnostics(log, output), [])

    def test_retains_unrelated_diagnostics_and_deduplication(self):
        missing = 'Error: [GetFeatureDef] could not find FeatureDef "other_feature"'
        similar_feature = 'Error: [GetFeatureDef] could not find FeatureDef "allpinesb_other_green_a_xl"'
        similar = 'Error: [SetConfigInt] key "OtherKey" is deprecated'
        other_shader = "LuaShader: [Other Shader] shader error(s):"
        other_pip_error = (
            'Error in Initialize(): [string "luaui/Widgets/gui_pip/gui_pip.lua"]:9371: '
            "attempt to call field 'OtherFunction' (a nil value)"
        )
        other = "Error: Could not finalize another texture atlas."
        log = (f"[t=1][f=1] {missing}\n[t=1][f=1] {similar_feature}\n"
               f"[t=1][f=1] {similar}\n[t=1][f=1] {similar}\n"
               f"[t=1][f=1] {other_shader}\n[t=1][f=1] {other_pip_error}")
        output = f"[t=2][f=1] {missing}\n[t=2][f=1] {similar}\n[t=2][f=1] {other}"
        self.assertEqual(_diagnostics(log, output), [
            f"[t=1][f=1] {missing}", f"[t=1][f=1] {similar_feature}", f"[t=1][f=1] {similar}",
            f"[t=1][f=1] {similar}", f"[t=1][f=1] {other_shader}",
            f"[t=1][f=1] {other_pip_error}", f"[t=2][f=1] {other}",
        ])

    def test_retains_unrelated_shutdown_headings(self):
        heading = "[t=1][f=1] Error in Shutdown()"
        detail = "[t=2][f=1] Error in Shutdown(): another widget failed"
        self.assertEqual(_diagnostics(f"{heading}\n{detail}\n{heading}", ""), [heading, detail, heading])

    def test_retains_shutdown_heading_separated_from_known_error(self):
        heading = "[t=1][f=1] Error in Shutdown()"
        detail = ('[t=2][f=1] Error in Shutdown(): '
                  '[string "LuaUI/Widgets/gui_sensor_ranges_jammer_preview.lua"]:1581: '
                  "attempt to index field 'sheetShader' (a nil value)")
        self.assertEqual(_diagnostics(f"{heading}\nUnrelated output\n{detail}", ""), [heading])


class PortabilityTests(unittest.TestCase):
    def test_application_root_tracks_moved_directory(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as directory:
            destination = Path(directory) / "moved"
            shutil.copytree(TEST_DIR.parent / "app", destination / "app")
            result = subprocess.run(
                [sys.executable, "-B", "-c", "from app.__main__ import APP_ROOT; print(APP_ROOT)"],
                cwd=destination,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(Path(result.stdout.strip()), destination.resolve())


if __name__ == "__main__":
    unittest.main()
