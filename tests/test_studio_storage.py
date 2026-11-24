import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.headless import MatchError
from app.studio_matches import MatchService


class StudioStorageTests(unittest.TestCase):
    def test_storage_paginating_and_pins_protect_selected_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = MatchService(Path(temporary))
            folder = service.local_data / "matches"
            for name in ("match-a", "match-b", "smoke-c"):
                run = folder / name
                run.mkdir(parents=True)
                (run / "infolog.txt").write_bytes(b"12345")
            demos = folder / "match-b" / "demos"
            demos.mkdir()
            (demos / "winner.sdfz").write_bytes(b"abc")
            service.pin_run("match-a", True)

            page = service.storage(limit=2)
            self.assertEqual(page["total_bytes"], 18)
            self.assertEqual(page["total_runs"], 3)
            self.assertEqual(page["protected_runs"], 2)
            self.assertEqual(page["next_offset"], 2)
            self.assertEqual(len(service.storage(offset=2, limit=2)["runs"]), 1)
            with self.assertRaises(MatchError):
                service.cleanup_runs(["match-a", "smoke-c"])
            with self.assertRaises(MatchError):
                service.cleanup_runs(["match-b"])
            self.assertTrue((folder / "smoke-c").is_dir())
            self.assertEqual(service.cleanup_runs(["smoke-c"])["removed"], ["smoke-c"])
            self.assertFalse((folder / "smoke-c").exists())
            self.assertTrue((demos / "winner.sdfz").is_file())

    def test_legacy_match_summaries_are_reused_between_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = MatchService(Path(temporary))
            for name in ("match-a", "match-b"):
                run = service.local_data / "matches" / name
                run.mkdir(parents=True)
                (run / "result.json").write_text('{"result":"Draw."}', encoding="utf-8")
            first = service.history(offset=0, limit=1)
            with patch.object(service, "_legacy_entry_from_dir", side_effect=AssertionError("hydrated again")):
                second = service.history(offset=first["next_offset"], limit=1)
            self.assertEqual(len(first["history"]), 1)
            self.assertEqual(len(second["history"]), 1)
            self.assertNotEqual(first["history"][0]["id"], second["history"][0]["id"])
            self.assertIsNone(second["next_offset"])

    def test_experiment_list_pages_use_index_and_details_still_have_games(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = MatchService(Path(temporary))
            for month in (1, 2):
                identifier = str(month) * 32
                workspace = service._experiment_workspace(identifier)
                workspace.mkdir(parents=True)
                service._save_experiment(workspace, {"id": identifier, "mode": "sweep", "state": "completed",
                    "started_at": f"2026-0{month}-01", "games": [{"map": "Example"}]}, None)
            with patch.object(service, "_read_experiment", side_effect=AssertionError("loaded full experiment")):
                first = service.experiment_history_page(offset=0, limit=1)
                second = service.experiment_history_page(offset=1, limit=1)
            self.assertEqual(first["experiments"][0]["id"], "2" * 32)
            self.assertNotIn("games", first["experiments"][0])
            self.assertEqual(first["next_offset"], 1)
            self.assertEqual(second["experiments"][0]["id"], "1" * 32)
            self.assertEqual(service.experiment_details("2" * 32)["games"], [{"map": "Example"}])
