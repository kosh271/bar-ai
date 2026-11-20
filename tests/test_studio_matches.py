import ctypes
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.discovery import Installation
from app.headless import MatchError, MatchProgress
from app.settings import SettingsError
from app.studio_matches import MatchService
from app.unit_catalog import _RANDOM_SCRIPT, scan, valid_catalog


CONTENT = {
    "maps": [{"name": "Map", "width": 512, "height": 512}],
    "games": ["Game"],
    "ais": [{"short_name": "AI", "version": "1", "name": "Display",
             "profiles": [{"key": "hard", "name": "Hard"}]}],
    "warnings": [],
}
PLAYERS = [
    {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
    {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Cortex", "ally": 1},
]


@unittest.skipUnless(os.name == "nt" and os.environ.get("BAR_AI_TEST_UNITSYNC"),
                     "Set BAR_AI_TEST_UNITSYNC to an engine folder to test its Lua parser.")
class CatalogLuaTests(unittest.TestCase):
    def test_randomized_definitions_use_repeatable_valid_samples(self):
        engine = Path(os.environ["BAR_AI_TEST_UNITSYNC"])
        with os.add_dll_directory(str(engine)):
            lib = ctypes.CDLL(str(engine / "unitsync.dll"))
            lib.lpOpenSource.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            lib.lpOpenSource.restype = lib.lpExecute.restype = ctypes.c_int
            lib.lpErrorLog.restype = ctypes.c_char_p
            source = _RANDOM_SCRIPT + b"""
local function generate()
    math.randomseed(42)
    local models = {'arm', 'cor'}
    local options = {'a', 'b', 'c'}
    local result = {}
    for i = 1, 100 do
        local model = models[math.random(1, 2)]
        local unit = options[math.random(#options)]
        assert(model and unit)
        local real = math.random()
        local negative = math.random(-3, -1)
        assert(real >= 0 and real < 1 and negative >= -3 and negative <= -1)
        result[i] = model .. unit .. negative
    end
    return table.concat(result, ',')
end
assert(generate() == generate())
assert(not pcall(math.random, 0))
assert(not pcall(math.random, 2, 1))
assert(not pcall(math.random, 1, 2, 3))
return {}
"""
            try:
                self.assertTrue(lib.lpOpenSource(source, b"Mmeb"))
                self.assertTrue(lib.lpExecute(), (lib.lpErrorLog() or b"").decode("utf-8", "replace"))
            finally:
                lib.lpClose()


class MatchServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "scan").mkdir()
        self.installation = Installation(self.root / "bar", self.root / "bar" / "data",
                                         ("engine",), "engine", "", None)
        self.service = MatchService(self.root)
        selected = patch.object(self.service, "_selected", return_value=(self.installation, self.root / "engine"))
        storage = patch.object(self.service, "_prepare_storage", return_value=self.root / "cache")
        temp = patch("app.studio_matches.tempfile.mkdtemp", return_value=str(self.root / "scan"))
        ready = patch("app.studio_matches.discovery.engine_readiness", return_value=())
        for mock in (selected, storage, temp, ready):
            mock.start()
            self.addCleanup(mock.stop)

    def wait(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.service.status()
            if status["state"] not in ("running", "cancelling"):
                return status
            time.sleep(0.01)
        self.fail("worker did not finish")

    def scan(self):
        with patch("app.studio_matches.headless.scan_content", return_value=CONTENT):
            self.service.scan("bar", "engine")
            return self.wait()

    def test_scan_and_match_report_serializable_progress_and_logs(self):
        self.assertEqual(self.scan()["result"]["content"], CONTENT)

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *, skip_smoke, process_started):
            self.assertTrue(skip_smoke)
            self.assertEqual(players[0].name, "Display")
            progress(MatchProgress("smoke", "Skipped"))
            progress(MatchProgress("match", "Winner: side 1.", 4, "Done"))
            return "Smoke skipped\nWinner: side 1.", self.root / "match"

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30, True)
            status = self.wait()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["phase"], "match")
        self.assertEqual(status["progress"][-1]["seconds"], 4)
        self.assertEqual(status["logs"]["directory"], str(self.root / "match"))
        json.dumps(status)
        status["progress"].clear()
        self.assertEqual(len(self.service.status()["progress"]), 2)

    def test_completed_scan_is_restored_by_a_new_service(self):
        self.assertEqual(self.scan()["state"], "completed")
        restored = MatchService(self.root)
        self.assertEqual(restored.cached_content(self.installation.root, "engine"), CONTENT)

    def test_scan_cache_write_failure_keeps_successful_result_and_reports_warning(self):
        with patch("app.studio_matches.settings.save_content_cache",
                   side_effect=SettingsError("disk unavailable")):
            status = self.scan()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["result"]["content"], CONTENT)
        self.assertIn("could not be saved for next run: disk unavailable", status["message"])

    def test_cancelled_scan_does_not_write_content_cache(self):
        entered, release = threading.Event(), threading.Event()

        def scan_content(*args, **kwargs):
            entered.set()
            release.wait(2)
            return CONTENT

        with patch("app.studio_matches.headless.scan_content", side_effect=scan_content):
            self.service.scan("bar", "engine")
            self.assertTrue(entered.wait(2))
            self.service.cancel()
            release.set()
            self.assertEqual(self.wait()["state"], "cancelled")
        self.assertIsNone(MatchService(self.root).cached_content(self.installation.root, "engine"))

    def test_match_status_checks_each_stage_process_and_reports_exit(self):
        self.scan()
        smoke_ready, match_ready, advance, finish = (threading.Event() for _ in range(4))
        smoke, match = MagicMock(), MagicMock()
        smoke.poll.return_value = None
        match.poll.return_value = None

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *, skip_smoke, process_started):
            process_started(smoke)
            smoke_ready.set()
            advance.wait(2)
            progress(MatchProgress("smoke", "Passed", 1))
            progress(MatchProgress("match", "Running"))
            process_started(match)
            match_ready.set()
            finish.wait(2)
            progress(MatchProgress("match", "Failed", 1, "Engine failed"))
            return "Engine failed", self.root / "match"

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            try:
                self.assertTrue(smoke_ready.wait(2))
                time.sleep(0.05)
                status = self.service.status()
                self.assertEqual((status["phase"], status["engine"]["state"]), ("smoke", "running"))
                self.assertGreater(status["phase_elapsed_seconds"], 0)
                smoke_started = self.service._state["_phase_started"]
                smoke.poll.return_value = 7
                self.assertEqual(self.service.status()["engine"], {"state": "exited", "exit_code": 7})
                advance.set()
                self.assertTrue(match_ready.wait(2))
                status = self.service.status()
                self.assertEqual((status["phase"], status["engine"]["state"]), ("match", "running"))
                self.assertGreater(self.service._state["_phase_started"], smoke_started)
                match.poll.return_value = 9
                self.assertEqual(self.service.status()["engine"], {"state": "exited", "exit_code": 9})
            finally:
                advance.set()
                finish.set()
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIsNone(status["engine"])

    def test_unexpected_worker_exit_is_not_left_running(self):
        self.scan()
        with patch("app.studio_matches.headless.run_match", side_effect=SystemExit):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("worker stopped unexpectedly", status["error"])

    def test_cancel_and_no_overlapping_operations(self):
        entered = threading.Event()

        def scan(engine, data, write, stop, **kwargs):
            entered.set()
            stop.wait(2)
            raise MatchError("Content scan cancelled.")

        with patch("app.studio_matches.headless.scan_content", side_effect=scan):
            self.service.scan("bar", "engine")
            self.assertTrue(entered.wait(2))
            with self.assertRaises(MatchError):
                self.service.scan("bar", "engine")
            other = MatchService(self.root)
            with self.assertRaises(MatchError):
                other.scan("bar", "engine")
            self.service.cancel()
            status = self.wait()
        self.assertEqual(status["state"], "cancelled")
        self.assertTrue(status["cancel_requested"])
        self.assertIn("cancelled", status["message"])
        self.assertIn("output", status["logs"])

    def test_invalid_selection_and_smoke_failure_are_visible(self):
        self.scan()
        invalid = [dict(player) for player in PLAYERS]
        invalid[0]["profile"] = "unknown"
        self.service.start("bar", "engine", "Game", "Map", invalid, 30)
        status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("Profile", status["error"])

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *, skip_smoke, process_started):
            progress(MatchProgress("smoke", "Failed", 1, "Smoke logs: smoke"))
            return "Smoke test failed", self.root / "smoke"

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "Smoke test failed")
        self.assertEqual(status["logs"]["smoke_directory"], "smoke")

    def test_failed_rescan_invalidates_old_choices(self):
        self.scan()
        with patch("app.studio_matches.headless.scan_content", side_effect=MatchError("scan failure")):
            self.service.scan("bar", "engine")
            self.assertIn("scan failure", self.wait()["error"])
        self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
        self.assertIn("Scan installed", self.wait()["error"])

    def test_catalog_reports_game_data_without_discarding_match_scan(self):
        self.scan()
        catalog = {"game": "Game", "units": [{"id": "factory", "name": "", "builds": ["unit"]}], "warnings": []}
        with patch("app.studio_matches.headless.scan_units", return_value=catalog):
            self.service.catalog("bar", "engine", "Game")
            status = self.wait()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["result"]["catalog"], catalog)
        self.assertEqual(self.service.latest_catalog(), catalog)
        def fail_smoke(engine, data, local, map_name, game, players, minutes, stop, progress, *, skip_smoke, process_started):
            progress(MatchProgress("smoke", "Failed"))
            return "Smoke test failed", self.root
        with patch("app.studio_matches.headless.run_match", side_effect=fail_smoke):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            self.assertEqual(self.wait()["error"], "Smoke test failed")

    def test_catalog_validation_rejects_duplicate_and_invalid_units(self):
        catalog = {"game": "Game", "units": [{"id": "unit", "name": "", "builds": ["unit2"]}], "warnings": []}
        self.assertTrue(valid_catalog(catalog))
        catalog["units"].append({"id": "UNIT", "name": "", "builds": []})
        self.assertFalse(valid_catalog(catalog))
        catalog["units"].pop()
        catalog["units"][0]["builds"] = [""]
        self.assertFalse(valid_catalog(catalog))

    def test_catalog_cancel_requires_the_current_operation_id(self):
        entered = threading.Event()

        def scan_units(engine, data, write, game, stop, **kwargs):
            entered.set()
            stop.wait(2)
            raise MatchError("Catalog cancelled.")

        with patch("app.studio_matches.headless.scan_units", side_effect=scan_units):
            job = self.service.catalog("bar", "engine", "Game")
            self.assertTrue(entered.wait(2))
            with self.assertRaises(MatchError):
                self.service.cancel("old-job")
            self.assertFalse(self.service.status()["cancel_requested"])
            self.service.cancel(job["id"])
            self.assertEqual(self.wait()["state"], "cancelled")
            self.assertIsNone(self.service.latest_catalog())

    def test_latest_catalog_loads_legacy_scan_and_replaces_it_only_on_success(self):
        scans = self.root / "data" / "studio-matches" / "scans"
        old = scans / "catalog-old"
        old.mkdir(parents=True)
        first = {"game": "Old", "units": [{"id": "armck", "name": "Old name", "builds": []}], "warnings": []}
        (old / "units.json").write_text(json.dumps(first), encoding="utf-8")
        self.assertEqual(self.service.latest_catalog(), first)
        newer = {"game": "New", "units": [{"id": "armck", "name": "New name", "builds": []}], "warnings": []}
        with patch("app.studio_matches.headless.scan_units", return_value=newer):
            self.service.catalog("bar", "engine", "New")
            self.assertEqual(self.wait()["state"], "completed")
        self.assertEqual(MatchService(self.root).latest_catalog(), newer)
        with patch("app.studio_matches.headless.scan_units", side_effect=MatchError("bad scan")):
            self.service.catalog("bar", "engine", "Game")
            self.assertEqual(self.wait()["state"], "failed")
        self.assertEqual(MatchService(self.root).latest_catalog(), newer)
        (self.root / "data" / "studio-matches" / "latest-catalog.json").write_text("bad", encoding="utf-8")
        with self.assertRaisesRegex(MatchError, "Cannot read saved unit catalog"):
            self.service.latest_catalog()

    def test_catalog_closes_dll_directory_after_load_or_init_failure(self):
        directory = MagicMock()
        lib = MagicMock()
        lib.Init.return_value = 0
        lib.GetNextError.return_value = b"init failed"
        with patch("app.unit_catalog.os.add_dll_directory", return_value=directory, create=True), \
                patch("app.unit_catalog.ctypes.CDLL", side_effect=OSError("load failed")):
            with self.assertRaises(OSError):
                scan(self.root, "Game")
        directory.__exit__.assert_called_once()
        directory.reset_mock()
        with patch("app.unit_catalog.os.add_dll_directory", return_value=directory, create=True), \
                patch("app.unit_catalog.ctypes.CDLL", return_value=lib):
            with self.assertRaisesRegex(RuntimeError, "init failed"):
                scan(self.root, "Game")
        directory.__exit__.assert_called_once()

    def test_catalog_does_not_pop_parent_for_non_table_build_options(self):
        lib = MagicMock()
        lib.Init.return_value = 1
        lib.GetPrimaryModCount.return_value = 1
        lib.GetPrimaryModInfoCount.return_value = 1
        lib.GetInfoKey.return_value = b"name"
        lib.GetInfoType.return_value = b"string"
        lib.GetInfoValueString.return_value = b"Game"
        lib.GetPrimaryModArchive.return_value = b"game.sdz"
        lib.GetNextError.return_value = b""
        lib.OpenFileVFS.return_value = 0
        lib.lpOpenSource.return_value = lib.lpExecute.return_value = lib.lpRootTable.return_value = 1
        lib.lpSubTableStr.side_effect = lambda key: 0 if key == b"buildoptions" else 1
        lib.lpGetIntKeyListCount.return_value = 0
        lib.lpGetStrKeyListCount.return_value = 2
        lib.lpGetStrKeyListEntry.side_effect = [b"bad", b"good"]
        lib.lpGetKeyExistsStr.side_effect = [1, 0]
        lib.lpGetStrKeyStrVal.return_value = b""
        with patch("app.unit_catalog.os.add_dll_directory", create=True), \
                patch("app.unit_catalog.ctypes.CDLL", return_value=lib):
            catalog = scan(self.root, "Game")
        self.assertIsNone(catalog["units"][0]["builds"])
        self.assertEqual(catalog["units"][1]["builds"], [])
        self.assertEqual(lib.lpPopTable.call_count, 3)

    def test_deadline_is_reported_as_failure(self):
        self.scan()

        class ImmediateTimer:
            daemon = False

            def __init__(self, _seconds, callback):
                self.callback = callback

            def start(self):
                self.callback()

            def cancel(self):
                pass

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *, skip_smoke, process_started):
            self.assertTrue(stop.is_set())
            raise MatchError("Stopped")

        with patch("app.studio_matches.threading.Timer", ImmediateTimer), \
                patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("real-time deadline", status["error"])

    def test_engine_path_traversal_is_rejected(self):
        with patch.object(self.service, "_selected", wraps=MatchService._selected.__get__(self.service)):
            self.service.scan("bar", "..")
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("Invalid engine folder", status["error"])


if __name__ == "__main__":
    unittest.main()
