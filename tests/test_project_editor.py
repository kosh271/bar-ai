import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.game_sources import GameSource
from app.installation import InstallResult
from app import profile_text
from app.project_editor import ProjectEditor
from app.settings import EditorState


class SourceSelectionTests(unittest.TestCase):
    def test_existing_mixed_endings_survive_insertion_among_repeated_lines(self):
        lines = ["same\r\n" if index % 2 == 0 else "same\n" for index in range(350)]
        original = "".join(lines) + "tail\r\n"
        edited = "same\n" * 199 + "new\n" + "same\n" * 151 + "changed\n"
        expected = "".join(lines[:199]) + "new\r\n" + "".join(lines[199:]) + "changed\r\n"
        self.assertEqual(profile_text.restore_newlines(original, edited), expected)

    def test_shared_save_rolls_back_if_second_replacement_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.as", root / "second.as"
            first.write_bytes(b"original one\r\n")
            second.write_bytes(b"original two\r\n")
            before = {path: path.read_bytes() for path in (first, second)}
            replace = os.replace

            def replace_or_fail(source, target):
                if target == second:
                    raise PermissionError("file is locked")
                replace(source, target)

            with patch("app.profile_text.os.replace", side_effect=replace_or_fail):
                with self.assertRaises(PermissionError):
                    profile_text.replace(root, [
                        (path, profile_text.digest(data), b"edited\r\n") for path, data in before.items()
                    ])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(set(root.iterdir()), {first, second})

    def test_legacy_save_retains_newlines_and_detects_another_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            relative = Path("script") / "main.as"
            path = Path(directory) / relative
            path.parent.mkdir()
            path.write_bytes(b"first\r\nsecond\n")
            editor = MagicMock(profile=Path(directory), current_file=None)
            editor._discard_changes.return_value = True
            self.assertTrue(ProjectEditor._open_file(editor, relative))
            editor.text.get.return_value = "changed\nsecond\n"
            ProjectEditor.save_file(editor)
            self.assertEqual(path.read_bytes(), b"changed\r\nsecond\n")

            path.write_bytes(b"external\r\nsecond\n")
            editor.text.get.return_value = "another\nsecond\n"
            ProjectEditor.save_file(editor)
            self.assertEqual(path.read_bytes(), b"external\r\nsecond\n")
            self.assertIn("changed outside", str(editor._show_error.call_args.args[0]))

    def test_suggested_source_shows_game_version_without_package_details(self):
        editor = MagicMock()
        source = GameSource(
            "Beyond All Reason test-42 (byar:test, abcdef01)",
            "Beyond All Reason test-42",
            Path("data"),
            "abcdef0123456789abcdef0123456789",
            tag="byar:test",
        )
        ProjectEditor._select_source(editor, source, suggested=True)
        editor.source_label.set.assert_called_once_with(source.label)
        editor.source_display.set.assert_called_once_with("BARbarIAn Hard - Beyond All Reason test-42")
        editor.source_display_label.configure.assert_called_once_with(style="TLabel")
        self.assertIn("last game log", editor.source_status.set.call_args.args[0])

    def test_missing_source_uses_warning_style(self):
        editor = MagicMock()
        editor.data = Path("data")
        editor.source_display_style = "MissingSource.TLabel"
        with patch("app.project_editor.list_game_sources", return_value=([], None)):
            ProjectEditor.set_installation(editor, Path("data"), None)
        editor.source_display_label.configure.assert_called_once_with(style="MissingSource.TLabel")

    def test_change_source_selects_only_confirmed_choice(self):
        editor = MagicMock()
        current = GameSource("Current", "Current game", Path("data"), "0" * 32)
        other = GameSource("Other", "Other game", Path("data"), "1" * 32)
        editor.source_choices = {current.label: current, other.label: other}
        editor.source_label.get.return_value = current.label
        with patch("app.project_editor.SourceDialog") as dialog:
            dialog.return_value.selected = other.label
            ProjectEditor.change_source(editor)
        editor._select_source.assert_called_once_with(other)
        editor._remember_selection.assert_called_once_with()

        editor._select_source.reset_mock()
        editor._remember_selection.reset_mock()
        with patch("app.project_editor.SourceDialog") as dialog:
            dialog.return_value.selected = None
            ProjectEditor.change_source(editor)
        editor._select_source.assert_not_called()
        editor._remember_selection.assert_not_called()

    def test_remember_selection_saves_source(self):
        editor = MagicMock()
        editor.state_path = Path("state.json")
        editor.project = None
        editor.profile = None
        editor.current_file = None
        editor.source_label.get.return_value = "Selected game"
        editor.source_suggested = False
        with patch("app.project_editor.save_editor_state") as save:
            ProjectEditor._remember_selection(editor)
        save.assert_called_once_with(
            editor.state_path, EditorState("", "", "", "Selected game", False)
        )

    def test_reinspection_preserves_manually_selected_source(self):
        editor = MagicMock()
        data = Path("data")
        source = GameSource("Other", "Other game", data, "1" * 32)
        editor.data = data
        editor.source_suggested = False
        editor.source_label.get.return_value = source.label
        with patch("app.project_editor.list_game_sources", return_value=([source], source)):
            ProjectEditor.set_installation(editor, data, None)
        editor._select_source.assert_called_once_with(source, suggested=False)

    def test_restores_saved_source_after_installation_inspection(self):
        editor = MagicMock()
        editor.state_path = Path("state.json")
        editor.project_choices = {}
        saved = EditorState("", "", "", "Other", True)
        with patch("app.project_editor.load_editor_state", return_value=saved):
            ProjectEditor._restore_selection(editor)

        source = GameSource("Other", "Other game", Path("data"), "1" * 32)
        editor.data = None
        editor.source_suggested = False
        editor.source_label.get.return_value = ""
        with patch("app.project_editor.list_game_sources", return_value=([source], None)):
            ProjectEditor.set_installation(editor, Path("data"), None)
        editor._select_source.assert_called_once_with(source, suggested=True)

    def test_changing_installation_does_not_keep_old_source(self):
        editor = MagicMock()
        editor.data = Path("old")
        editor.source_suggested = False
        editor.source_label.get.return_value = "Old"
        source = GameSource("New", "New game", Path("new"), "1" * 32)
        with patch("app.project_editor.list_game_sources", return_value=([source], source)):
            ProjectEditor.set_installation(editor, Path("new"), None)
        editor._select_source.assert_called_once_with(source, suggested=True)


class InstallationActionTests(unittest.TestCase):
    def test_install_rejects_unsaved_edits(self):
        editor = MagicMock()
        editor._deployment_selection.return_value = (Path("project"), Path("data"), "engine")
        editor.text.edit_modified.return_value = True
        with patch("app.project_editor.install") as install:
            ProjectEditor.install_ai(editor)
        install.assert_not_called()
        self.assertIn("Save", str(editor._show_error.call_args.args[0]))

    def test_install_uses_the_currently_selected_engine(self):
        editor = MagicMock()
        selection = (Path("project"), Path("data"), "engine")
        editor._deployment_selection.return_value = selection
        editor.text.edit_modified.return_value = False
        with patch("app.project_editor.install", return_value=InstallResult(Path("installed"), True)) as install:
            ProjectEditor.install_ai(editor)
        install.assert_called_once_with(*selection, editor._confirm_deployment)
        self.assertIn("Restart BAR", editor.status.set.call_args.args[0])

    def test_install_reports_when_ai_is_already_up_to_date(self):
        editor = MagicMock()
        editor._deployment_selection.return_value = (Path("project"), Path("data"), "engine")
        editor.text.edit_modified.return_value = False
        with patch("app.project_editor.install", return_value=InstallResult(Path("installed"), False)):
            ProjectEditor.install_ai(editor)
        self.assertIn("already up to date", editor.status.set.call_args.args[0])

    def test_remove_requires_confirmation(self):
        editor = MagicMock()
        editor._deployment_selection.return_value = (Path("project"), Path("data"), "engine")
        editor._confirm_deployment.return_value = False
        with patch("app.project_editor.remove") as remove:
            ProjectEditor.remove_ai(editor)
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
