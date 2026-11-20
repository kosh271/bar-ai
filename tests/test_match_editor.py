import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.discovery import Installation
from app.headless import MatchProgress, Player
from app.match_editor import MatchEditor, MatchResult, WorkError


class MatchEditorLayoutTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self._destroy_root)
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1000x950+0+0")
        self.root.deiconify()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.editor = MatchEditor(self.root, Path(temporary.name), lambda: None, lambda: "")
        self.editor.pack(fill="both", expand=True)
        self.root.update()

    def _destroy_root(self):
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.editor.after_cancel(job)
        self.root.destroy()

    def test_status_uses_available_window_space_not_text_length(self):
        self.assertFalse(self.editor.log_frame.winfo_ismapped())
        self.editor._toggle_log()
        self.root.update()
        status = self.editor.status_text
        initial_height = status.winfo_height()
        self.assertGreater(initial_height, 100)

        self.editor.status.set("\n".join(f"Diagnostic {number}" for number in range(120)))
        self.root.update()
        self.assertEqual(status.winfo_height(), initial_height)
        self.assertLess(status.yview()[1], 1.0)

        self.root.geometry("1000x700+0+0")
        self.root.update()
        self.assertLess(status.winfo_height(), initial_height)
        self.assertGreater(status.winfo_height(), 1)
        initial_width = status.winfo_width()
        self.root.geometry("800x950+0+0")
        self.root.update()
        self.assertEqual(status.winfo_height(), initial_height)
        self.assertLess(status.winfo_width(), initial_width)
        self.assertTrue(status.get("1.0", "end-1c").startswith("Diagnostic 0"))

    def test_many_players_remain_scrollable_and_status_is_reachable(self):
        self.editor._toggle_log()
        self.root.update()
        for _ in range(25):
            self.editor.add_row(0)
        self.root.update()
        canvas = self.editor.log_frame.master.master.master
        self.assertLess(canvas.yview()[1], 1.0)
        canvas.yview_moveto(1.0)
        self.root.update()
        status = self.editor.status_text
        self.assertLessEqual(status.winfo_rooty() + status.winfo_height(),
                             canvas.winfo_rooty() + canvas.winfo_height())
        for row in list(self.editor.rows[0]):
            self.editor.remove_row(0, row)
        self.root.update()
        self.assertEqual(canvas.yview(), (0.0, 1.0))
        self.assertGreater(status.winfo_height(), 100)

    def test_summary_is_visible_with_collapsed_log(self):
        self.editor.busy = self.editor.running = True
        self.editor.events.put(MatchProgress("smoke", "Passed", 62, "Smoke details"))
        self.editor.events.put(MatchProgress("match", "Winner: side 2.", 125, "Match details"))
        self.editor.events.put(MatchResult("Smoke details\nMatch details", Path("match")))
        self.editor._poll()
        self.root.update()
        self.assertFalse(self.editor.log_frame.winfo_ismapped())
        self.assertEqual(self.editor.smoke_status.get(), "Smoke test: Passed (00:01:02 real time)")
        self.assertEqual(self.editor.match_status.get(), "Skirmish: Winner: side 2. (00:02:05 real time)")
        self.editor._toggle_log()
        self.root.update()
        self.assertIn("Match details", self.editor.status_text.get("1.0", "end-1c"))
        self.editor._toggle_log()
        self.root.update()
        self.assertFalse(self.editor.log_frame.winfo_ismapped())

    def test_failed_smoke_marks_skirmish_not_started(self):
        self.editor.busy = self.editor.running = True
        self.editor.events.put(MatchProgress("smoke", "Failed", 8, "Smoke failed"))
        self.editor.events.put(MatchResult("Smoke failed", Path("smoke")))
        self.editor._poll()
        self.assertEqual(self.editor.smoke_status.get(), "Smoke test: Failed (00:00:08 real time)")
        self.assertEqual(self.editor.match_status.get(), "Skirmish: Not started")

    def test_skip_checkbox_is_forwarded_to_runner(self):
        installation = Installation(Path("bar"), Path("bar-data"), ("engine",), None, "", None)
        self.editor.installation = lambda: installation
        self.editor.engine = lambda: "engine"
        self.editor.scan_key = (installation.root, "engine")
        self.editor.map_choices = {"Map": "Map"}
        self.editor.map.set("Map")
        self.editor.game_box["values"] = ("Game",)
        self.editor.game.set("Game")
        row = MagicMock()
        row.player.side_effect = lambda side: Player("AI", "1", "hard", "Armada", side, "AI")
        self.editor.rows = [[row], [row]]
        self.assertFalse(self.editor.skip_smoke.get())
        self.editor.skip_smoke.set(True)
        with patch.object(self.editor, "_work") as work, patch("app.match_editor.run_match") as run:
            run.return_value = ("Match done", Path("match"))
            self.editor.start()
            self.assertTrue(self.editor.skip_smoke_button.instate(["disabled"]))
            work.call_args.args[0]()
        self.assertTrue(run.call_args.kwargs["skip_smoke"])
        self.assertEqual(self.editor.smoke_status.get(), "Smoke test: Skipped")
        self.editor.events.put(MatchResult("Match done", Path("match")))
        self.editor._poll()
        self.assertFalse(self.editor.skip_smoke_button.instate(["disabled"]))

    def test_stopping_before_launch_is_not_reported_as_an_error(self):
        self.editor.busy = self.editor.running = True
        self.editor.smoke_status.set("Smoke test: Skipped")
        self.editor.match_status.set("Skirmish: Starting")
        self.editor.stop_event.set()
        self.editor.events.put(WorkError("Stopped before the match started."))
        with patch("app.match_editor.messagebox.showerror") as showerror:
            self.editor._poll()
        showerror.assert_not_called()
        self.assertEqual(self.editor.match_status.get(), "Skirmish: Stopped")


if __name__ == "__main__":
    unittest.main()
