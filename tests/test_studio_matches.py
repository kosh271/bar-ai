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
from app.headless import MatchError, MatchProgress, Player
from app.settings import SettingsError
from app.studio_experiments import Candidate, rankings
from app.studio_matches import MatchService, _operation_lock
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

    def test_record_demo_is_strict_and_result_artifacts_are_exposed(self):
        with self.assertRaisesRegex(MatchError, "record_demo must be a boolean"):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 1, record_demo=1)
        self.assertEqual(self.scan()["state"], "completed")
        run = self.service.local_data / "matches" / "match-test"
        (run / "demos").mkdir(parents=True)
        (run / "demos" / "recorded.sdfz").write_bytes(b"demo")
        telemetry = {"end_frame": 1800, "duration": "01:00", "teams": []}
        replays = [{"path": "demos/recorded.sdfz", "size_bytes": 4}]
        (run / "result.json").write_text(json.dumps({"telemetry": telemetry, "replays": replays}), encoding="utf-8")
        with patch("app.studio_matches.headless.run_match", return_value=("Done", run)) as launch:
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 1, record_demo=True)
            status = self.wait()
            self.assertIs(launch.call_args.kwargs["record_demo"], True)
        self.assertEqual(status["result"]["telemetry"], telemetry)
        self.assertEqual(status["logs"]["run_id"], "match-test")
        self.assertEqual(status["logs"]["replays"], replays)

    def test_copy_replay_refuses_to_overwrite_a_bar_demo(self):
        run = self.service.local_data / "matches" / "match-test"
        (run / "demos").mkdir(parents=True)
        source = run / "demos" / "recorded.sdfz"
        source.write_bytes(b"demo")
        (run / "result.json").write_text('{"replays":[{"path":"demos/recorded.sdfz","size_bytes":4}]}',
                                         encoding="utf-8")
        self.installation.data.mkdir(parents=True)
        copied = self.service.copy_replay("match-test", "demos/recorded.sdfz", "bar", "engine")
        self.assertEqual(Path(copied["path"]).read_bytes(), b"demo")
        source.write_bytes(b"new")
        with self.assertRaisesRegex(MatchError, "already exists"):
            self.service.copy_replay("match-test", "demos/recorded.sdfz", "bar", "engine")
        self.assertEqual(Path(copied["path"]).read_bytes(), b"demo")

    def test_scan_and_match_report_serializable_progress_and_logs(self):
        self.assertEqual(self.scan()["result"]["content"], CONTENT)

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *,
                skip_smoke, process_started, record_demo, extra_units, capture_stats):
            self.assertTrue(skip_smoke)
            self.assertTrue(extra_units)
            self.assertEqual(players[0].name, "Display")
            progress(MatchProgress("smoke", "Skipped"))
            progress(MatchProgress("match", "Winner: side 1.", 4, "Done"))
            return "Smoke skipped\nWinner: side 1.", self.root / "match"

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30, True, extra_units=True)
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
        with patch.object(restored, "_selected", return_value=(self.installation, self.root / "engine")), \
                patch("app.studio_matches.headless.run_match", return_value=("Draw.", self.root / "match")) as run:
            self.service = restored
            restored.start(str(self.installation.root), "engine", "Game", "Map", PLAYERS, 30)
            status = self.wait()
        self.assertEqual(status["state"], "completed", status["error"])
        self.assertEqual(run.call_args.args[5][0].name, "Display")

    def test_completed_operation_has_released_the_worker_slot(self):
        published, resume, finished = threading.Event(), threading.Event(), threading.Event()
        service = self.service

        class PublicationLock:
            def __init__(self):
                self.lock = threading.Lock()

            def __enter__(self):
                self.lock.acquire()

            def __exit__(self, *_):
                terminal = (threading.current_thread().name == "studio-scan"
                            and service._state["state"] == "completed")
                self.lock.release()
                if terminal and not published.is_set():
                    published.set()
                    resume.wait(3)

        def run_worker(target):
            try:
                target()
            finally:
                finished.set()

        thread_factory = threading.Thread
        service._lock = PublicationLock()
        with patch("app.studio_matches.threading.Thread", side_effect=lambda target, **kwargs:
                   thread_factory(target=run_worker, args=(target,), **kwargs)):
            service._launch("scan", lambda: ({}, {}, "completed"))
        try:
            self.assertTrue(published.wait(3))
            self.assertEqual(service.status()["state"], "completed")
            self.assertFalse(_operation_lock.locked())
        finally:
            resume.set()
            self.assertTrue(finished.wait(3))

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

    def test_scan_reports_map_progress_and_cancellation_as_a_terminal_status(self):
        entered = threading.Event()

        def scan_content(engine, data, write, stop, *, archive_cache, progress):
            progress(4, 12)
            entered.set()
            stop.wait(2)
            raise MatchError("Content scan cancelled.")

        with patch("app.studio_matches.headless.scan_content", side_effect=scan_content):
            job = self.service.scan("bar", "engine")
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.service.status()["scan_progress"], {"checked": 4, "total": 12})
            self.service.cancel(job["id"])
            status = self.wait()
            self.assertEqual(status["state"], "cancelled")
            self.assertIsNone(status["error"])
            self.assertEqual(self.service.cancel(job["id"])["state"], "cancelled")

    def test_match_status_checks_each_stage_process_and_reports_exit(self):
        self.scan()
        smoke_ready, match_ready, advance, finish = (threading.Event() for _ in range(4))
        smoke, match = MagicMock(), MagicMock()
        smoke.pid, match.pid = 123, 456
        smoke.poll.return_value = None
        match.poll.return_value = None

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *,
                skip_smoke, process_started, record_demo, extra_units, capture_stats):
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
                self.assertEqual(status["games"][0]["state"], "smoke")
                self.assertEqual(status["games"][0]["pid"], 123)
                smoke_elapsed = status["phase_elapsed_seconds"]
                smoke.poll.return_value = 7
                self.assertEqual(self.service.status()["engine"], {"state": "exited", "exit_code": 7})
                advance.set()
                self.assertTrue(match_ready.wait(2))
                status = self.service.status()
                self.assertEqual((status["phase"], status["engine"]["state"]), ("match", "running"))
                self.assertEqual(status["games"][0]["pid"], 456)
                self.assertGreater(status["games"][0]["elapsed_seconds"], 0)
                self.assertLess(status["phase_elapsed_seconds"], smoke_elapsed)
                match.poll.return_value = 9
                self.assertEqual(self.service.status()["engine"], {"state": "exited", "exit_code": 9})
            finally:
                advance.set()
                finish.set()
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIsNone(status["engine"])
        self.assertEqual((status["games"][0]["state"], status["games"][0]["pid"]), ("failed", 456))

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

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *,
                skip_smoke, process_started, record_demo, extra_units, capture_stats):
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
        def fail_smoke(engine, data, local, map_name, game, players, minutes, stop, progress, *,
                       skip_smoke, process_started, record_demo, extra_units, capture_stats):
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

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, *,
                skip_smoke, process_started, record_demo, extra_units, capture_stats):
            self.assertTrue(stop.is_set())
            raise MatchError("Stopped")

        with patch("app.studio_matches.threading.Timer", ImmediateTimer), \
                patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("real-time deadline", status["error"])

    def test_late_timeout_does_not_stop_the_next_match(self):
        self.scan()
        callbacks = []

        def timer(_seconds, callback):
            callbacks.append(callback)
            return MagicMock()

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            if len(callbacks) == 2:
                callbacks[0]()
                if stop.is_set():
                    raise MatchError("The previous match stopped this match.")
            return "Draw.", self.root / "match"

        with patch("app.studio_matches.threading.Timer", side_effect=timer), \
                patch("app.studio_matches.headless.run_match", side_effect=run):
            for _ in range(2):
                self.service.start("bar", "engine", "Game", "Map", PLAYERS, 30)
                status = self.wait()
                self.assertEqual(status["state"], "completed", status["error"])

    def test_engine_path_traversal_is_rejected(self):
        with patch.object(self.service, "_selected", wraps=MatchService._selected.__get__(self.service)):
            self.service.scan("bar", "..")
            status = self.wait()
        self.assertEqual(status["state"], "failed")
        self.assertIn("Invalid engine folder", status["error"])


SERIES_CONTENT = {
    "maps": [
        {"name": "Map", "width": 512, "height": 512},
        {"name": "Map2", "width": 1024, "height": 1024},
    ],
    "games": ["Game"],
    "ais": [
        {
            "short_name": "AI",
            "version": "1",
            "name": "My AI",
            "profiles": [
                {"key": "hard", "name": "Hard"},
                {"key": "fast", "name": "Fast"},
            ],
        },
        {
            "short_name": "BARb",
            "version": "stable",
            "name": "BARbarIAn",
            "profiles": [{"key": "hard", "name": "Hard"}],
        },
    ],
    "warnings": [],
}


class BenchmarkSeriesAndHistoryTests(unittest.TestCase):
    def setUp(self):
        import shutil as _shutil

        self._shutil = _shutil
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        (self.root / "scan").mkdir()
        self.bar_root = self.root / "bar"
        self.data = self.bar_root / "data"
        self.engine = "engine"
        stock = self.data / "engine" / self.engine / "AI" / "Skirmish" / "BARb" / "stable"
        stock.mkdir(parents=True)
        (stock / "AIInfo.lua").write_text(
            "local infos = {\n"
            "{ key = 'shortName', value = 'BARb' },\n"
            "{ key = 'version', value = 'stable' },\n"
            "{ key = 'name', value = 'BARbarIAn' },\n"
            "}\nreturn infos\n",
            encoding="utf-8",
        )
        (stock / "AIOptions.lua").write_text(
            "local options = {\n{key='profile', def='hard'},\n{key='game_config', def=true},\n}\nreturn options\n",
            encoding="utf-8",
        )
        (stock / "SkirmishAI.dll").write_bytes(b"dll")
        self.installation = Installation(self.bar_root, self.data, (self.engine,), self.engine, "", None)
        self.service = MatchService(self.root, projects_root=self.projects)
        for mock in (
            patch.object(self.service, "_selected", return_value=(self.installation, self.data / "engine" / self.engine)),
            patch("app.studio_matches.discovery.engine_readiness", return_value=()),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.service.status()
            if status["state"] not in ("running", "cancelling"):
                return status
            time.sleep(0.01)
        self.fail("worker did not finish")

    def scan(self):
        with patch("app.studio_matches.tempfile.mkdtemp", return_value=str(self.root / "scan")), \
                patch("app.studio_matches.headless.scan_content", return_value=SERIES_CONTENT):
            self.service.scan(self.bar_root, self.engine)
            return self.wait()

    def test_multi_ai_team_rosters_2v2_and_asymmetric(self):
        self.scan()
        recorded = []

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            recorded.append([(p.short_name, p.profile, p.faction, p.ally, p.name) for p in players])
            progress(MatchProgress("match", "Winner: side 1.", 1, "Done"))
            return "Winner: side 1.", self.root / "match"

        roster_2v2 = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "AI", "version": "1", "profile": "fast", "faction": "Cortex", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Armada", "ally": 1},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Legion", "ally": 1},
        ]
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start(self.bar_root, self.engine, "Game", "Map", roster_2v2, 20, True)
            self.assertEqual(self.wait()["state"], "completed")
            self.service.start(self.bar_root, self.engine, "Game", "Map", roster_2v2[:3], 20, True)
            self.assertEqual(self.wait()["state"], "completed")

        self.assertEqual(len(recorded[0]), 4)
        self.assertEqual(recorded[0][1], ("AI", "fast", "Cortex", 0, "My AI"))
        self.assertEqual(len(recorded[1]), 3)

    def test_sequential_benchmark_series_maps_factions_swapping_and_tallies(self):
        self.scan()
        calls = []
        outcomes = [
            "Smoke test passed.\nWinner: side 1.",
            "Smoke test passed.\nWinner: side 2.",
            "Smoke test passed.\nDraw.",
            MatchError("Engine crashed"),
        ]

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            idx = len(calls)
            calls.append((map_name, [(p.short_name, p.faction, p.ally) for p in players]))
            item = outcomes[idx]
            if isinstance(item, Exception):
                raise item
            run_dir = local / "matches" / f"match-{idx}"
            run_dir.mkdir(parents=True)
            (run_dir / "result.json").write_text(json.dumps({"telemetry": {
                "teams": [{"team": 0, "side": players[0].ally + 1, "stats": {"damage_dealt": idx + 1}}],
            }}), encoding="utf-8")
            progress(MatchProgress("match", "Done", 2))
            return item, run_dir

        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        series = {
            "games": 4,
            "swap_sides": True,
            "maps": ["Map", "Map2"],
            "faction_pairs": [["Armada", "Cortex"], ["Legion", "Armada"]],
        }
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start(self.bar_root, self.engine, "Game", "Map", players, 30, True, series=series)
            status = self.wait()

        self.assertEqual(status["state"], "completed")
        tally = status["result"]["series"]
        self.assertEqual((tally["side1_wins"], tally["side2_wins"], tally["draws"], tally["failures"]), (2, 0, 1, 1))
        self.assertEqual(tally["completed_games"], 4)
        self.assertEqual(calls[0], ("Map", [("AI", "Armada", 0), ("BARb", "Cortex", 1)]))
        self.assertEqual(calls[1], ("Map2", [("AI", "Legion", 1), ("BARb", "Armada", 0)]))
        self.assertEqual(calls[2][0], "Map")
        self.assertEqual(calls[3][0], "Map2")
        rows = status["games"]
        self.assertEqual([row["state"] for row in rows], ["completed", "completed", "draw", "failed"])
        self.assertEqual([row["winner_side"] for row in rows], [1, 2, None, None])
        self.assertEqual(rows[1]["telemetry"]["teams"][0]["stats"]["damage_dealt"], 2)
        self.assertEqual(rows[0]["logs"]["run_id"], "match-0")

    def test_mid_series_cancellation_stops_active_game_and_prevents_next(self):
        self.scan()
        entered = threading.Event()
        calls = []

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            calls.append(map_name)
            if len(calls) == 2:
                entered.set()
                stop.wait(2)
                raise MatchError("Stopped by user.")
            return "Winner: side 1.", self.root / "match-1"

        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start(
                self.bar_root, self.engine, "Game", "Map", players, 30, True,
                series={"games": 5, "swap_sides": False, "maps": ["Map", "Map2"]},
            )
            self.assertTrue(entered.wait(2))
            active = self.service.status()["games"]
            self.assertEqual(active[0]["state"], "completed")
            self.assertEqual([row["state"] for row in active[2:]], ["queued"] * 3)
            self.assertEqual(active[2]["players"][0]["profile"], "hard")
            self.service.cancel()
            status = self.wait()

        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(len(calls), 2)
        self.assertEqual(status["series"]["side1_wins"], 1)
        self.assertEqual(status["series"]["cancelled"], 1)
        self.assertEqual([row["state"] for row in status["games"]],
                         ["completed", "cancelled", "not_run", "not_run", "not_run"])
        history = self.service.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "cancelled")

    def test_parallel_queue_refills_a_slot_and_cancels_one_game_independently(self):
        self.scan()
        first, second, third = (threading.Event() for _ in range(3))
        release_second, release_third = threading.Event(), threading.Event()
        started = {}

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            process = MagicMock(pid=minutes + 100)
            process.poll.return_value = None
            kwargs["process_started"](process)
            started[minutes] = stop
            {10: first, 20: second, 30: third}[minutes].set()
            if minutes == 10:
                stop.wait(3)
            else:
                {20: release_second, 30: release_third}[minutes].wait(3)
            return "Winner: side 1.", self.root / f"match-{minutes}"

        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            try:
                for minutes in (10, 20, 30):
                    self.service.start(self.bar_root, self.engine, "Game", "Map", players, minutes, True,
                                       parallel_games=2)
                self.assertTrue(first.wait(2))
                self.assertTrue(second.wait(2))
                self.assertFalse(third.is_set())
                status = self.service.status()
                self.assertEqual([(row["state"], row["pid"]) for row in status["games"]],
                                 [("running", 110), ("running", 120), ("queued", None)])
                self.service.cancel_game(status["id"], status["games"][0]["game_index"])
                self.assertTrue(third.wait(2))
                self.assertFalse(started[20].is_set())
                self.assertFalse(started[30].is_set())
            finally:
                release_second.set()
                release_third.set()
            status = self.wait()
        self.assertEqual([row["state"] for row in status["games"]], ["cancelled", "completed", "completed"])
        self.assertEqual(len(self.service.history()), 3)

    def test_cancelling_queue_rejects_an_in_flight_submission(self):
        self.scan()
        entered, preparing, continue_preparation = (threading.Event() for _ in range(3))
        errors = []
        prepare = self.service._prepare_batch

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            entered.set()
            stop.wait(3)
            raise MatchError("Stopped by user.")

        def delayed_prepare(*args):
            preparing.set()
            continue_preparation.wait(3)
            return prepare(*args)

        # The generic test roster contains two profiles of the same installed AI.
        def start():
            try:
                self.service.start(self.bar_root, self.engine, "Game", "Map", PLAYERS, 20, True)
            except MatchError as error:
                errors.append(str(error))

        with patch("app.studio_matches.headless.run_match", side_effect=run) as launch:
            start()
            self.assertTrue(entered.wait(2), errors)
            with patch.object(self.service, "_prepare_batch", side_effect=delayed_prepare):
                submitter = threading.Thread(target=start, daemon=True)
                submitter.start()
                try:
                    self.assertTrue(preparing.wait(2))
                    self.service.cancel()
                    self.assertEqual(self.wait()["state"], "cancelled")
                finally:
                    continue_preparation.set()
                    submitter.join(3)
        self.assertEqual(errors, ["The test queue was cancelled before the games could be added."])
        launch.assert_called_once()

    @unittest.skipUnless(os.name != "invalid" and __import__("shutil").which("git"), "Git required")
    def test_persistent_history_checkpoint_drift_legacy_and_filtering(self):
        from app import installation, project_history
        from app.projects import create_project

        source = self.root / "stock-src"
        (source / "config" / "hard").mkdir(parents=True)
        (source / "script" / "hard").mkdir(parents=True)
        (source / "config" / "hard" / "settings.json").write_text("{}", encoding="utf-8")
        (source / "script" / "hard" / "init.as").write_text("init", encoding="utf-8")
        (source / "script" / "hard" / "main.as").write_text("main", encoding="utf-8")
        project = create_project(self.projects, "AI", source)
        project_history.ensure(project)
        head = project_history.list_checkpoints(project)[0]["id"]
        installation.install(project, self.data, self.engine, lambda _m: True)

        self.scan()
        matches_dir = self.service.local_data / "matches"
        matches_dir.mkdir(parents=True, exist_ok=True)
        run1 = matches_dir / "match-run1"
        run1.mkdir()
        (run1 / "result.json").write_text(json.dumps({"result": "Winner: side 1."}), encoding="utf-8")

        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        with patch("app.studio_matches.headless.run_match", return_value=("Winner: side 1.", run1)):
            self.service.start(self.bar_root, self.engine, "Game", "Map", players, 30, True)
            self.assertEqual(self.wait()["state"], "completed")

        enriched = json.loads((run1 / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(enriched["projects"][0]["checkpoint"], head)
        self.assertTrue(enriched["projects"][0]["clean"])

        # Dirty project and install drift for run 2 (with external path never leaked)
        (project / "profiles" / "hard" / "script" / "main.as").write_text("edited", encoding="utf-8")
        external_dir = self.root / "outside"
        external_dir.mkdir()
        with patch("app.studio_matches.headless.run_match", return_value=("Engine failed (exit 1)", external_dir)):
            self.service.start(self.bar_root, self.engine, "Game", "Map2", players, 30, True)
            self.assertEqual(self.wait()["state"], "failed")

        # Legacy unindexed result.json with script.txt
        legacy_dir = matches_dir / "match-legacy"
        legacy_dir.mkdir()
        (legacy_dir / "result.json").write_text(json.dumps({"result": "Draw."}), encoding="utf-8")
        (legacy_dir / "script.txt").write_text(
            "[GAME]\n{\nMapName=LegacyMap;\nGameType=Game;\n"
            "[AI0]\n{\nShortName=BARb;\nVersion=stable;\nTeam=0;\n[OPTIONS]\n{\nprofile=hard;\n}\n}\n"
            "[TEAM0]\n{\nAllyTeam=0;\nSide=Armada;\n}\n}\n",
            encoding="utf-8",
        )

        restarted = MatchService(self.root, projects_root=self.projects)
        all_entries = restarted.history()
        self.assertEqual(len(all_entries), 3)
        self.assertEqual(len(restarted.history(project="AI")), 2)
        self.assertEqual(len(restarted.history(map_name="LegacyMap")), 1)
        self.assertEqual(len(restarted.history(checkpoint=head[:10])), 2)
        dirty_entry = next(e for e in all_entries if e["state"] == "failed")
        self.assertIsNone(dirty_entry["run_dir"])
        self.assertTrue(dirty_entry["projects"][0]["uncheckpointed_changes"])
        self.assertTrue(dirty_entry["projects"][0]["install_drift"])
        self.assertFalse(dirty_entry["projects"][0]["clean"])

    def test_experiment_round_robin_budget_and_persisted_rankings(self):
        self.scan()
        opponents = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Legion", "ally": 1},
        ]
        calls = []

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            calls.append((map_name, players[0].faction, players[1].faction))
            if len(calls) == 2:
                raise MatchError("Engine crashed")
            path = local / "matches" / f"match-experiment-{len(calls)}"
            path.mkdir(parents=True)
            (path / "result.json").write_text(json.dumps({
                "winning_sides": [0], "engine_exit": 0,
                "telemetry": {"end_frame": 3600, "ai_script_errors": 1,
                              "teams": [{"side": 1, "ai_script_errors": 0}, {"side": 2, "ai_script_errors": 1}]},
            }), encoding="utf-8")
            return "Winner: side 1.", path

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map", "Map2"], 30, 4, 60, "tournament", opponents,
            )
            status = self.wait()
        self.assertEqual(status["state"], "completed")
        entry = status["result"]["experiment"]
        self.assertEqual(entry["stop_reason"], "game_limit")
        self.assertEqual(entry["completed_games"], 4)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len({frozenset(call[1:]) for call in calls[:3]}), 3)
        self.assertEqual({call[0] for call in calls}, {"Map", "Map2"})
        self.assertEqual(entry["games"][1]["outcome"], "failed")
        first = entry["rankings"][0]
        self.assertGreater(first["wins"], 0)
        self.assertEqual(first["average_duration_seconds"], 120.0)
        self.assertEqual(first["error_free_games"], first["wins"])
        restarted = MatchService(self.root, projects_root=self.projects)
        self.assertEqual(restarted.experiment_history()[0], entry)

    def test_experiment_pauses_after_active_game_and_cancels_while_paused(self):
        self.scan()
        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        entered, finish = threading.Event(), threading.Event()

        def run(*args, **kwargs):
            entered.set()
            self.assertTrue(finish.wait(2))
            return "Draw.", self.root / "match"

        with patch("app.studio_matches.headless.run_match", side_effect=run) as launch:
            job = self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map"], 30, 2, 60, "tournament", players,
            )
            self.assertTrue(entered.wait(2))
            self.service.pause_experiment(True)
            finish.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not self.service.status()["experiment"]["paused"]:
                time.sleep(0.01)
            self.assertTrue(self.service.status()["experiment"]["paused"])
            self.service.cancel(job["id"])
            status = self.wait()
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["result"]["experiment"]["stop_reason"], "cancelled")

    def test_experiment_deadline_stops_active_game_without_launching_another(self):
        self.scan()
        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            if stop.wait(2):
                raise MatchError("Stopped by experiment")
            self.fail("The cumulative real-time deadline did not stop the active game")

        with patch("app.studio_matches.headless.run_match", side_effect=run) as launch:
            self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map"], 30, 2, 1, "tournament", players,
            )
            status = self.wait()
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(status["result"]["experiment"]["stop_reason"], "time_limit")
        self.assertEqual(status["result"]["experiment"]["games"][0]["outcome"], "timeout")
        self.assertFalse(status["result"]["experiment"]["games"][0]["simulated_limit"])
        self.assertEqual(status["result"]["experiment"]["rankings"][0]["error_free_games"], 0)

    def test_experiment_simulated_time_limit_counts_as_error_free_without_a_winner(self):
        self.scan()
        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        run = self.service.local_data / "matches" / "match-capped"
        run.mkdir(parents=True)
        (run / "result.json").write_text('{"engine_exit":0,"telemetry":{"end_frame":1800}}', encoding="utf-8")
        with patch("app.studio_matches.headless.run_match", return_value=("Time limit reached (1 game minutes).", run)):
            self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map"], 1, 1, 60, "tournament", players,
            )
            experiment = self.wait()["experiment"]
        self.assertTrue(experiment["games"][0]["simulated_limit"])
        for rank in experiment["rankings"]:
            self.assertEqual(rank["error_free_games"], 1)
            self.assertEqual(rank["average_duration_seconds"], 60)
            self.assertIsNone(rank["win_rate"])

    def test_experiment_cancel_sets_active_game_stop_signal(self):
        self.scan()
        players = [
            {"short_name": "AI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        entered, stopped = threading.Event(), threading.Event()

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            entered.set()
            self.assertTrue(stop.wait(2))
            stopped.set()
            return "Stopped by user.", self.root / "match"

        with patch("app.studio_matches.headless.run_match", side_effect=run) as launch:
            job = self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map"], 30, 2, 60, "tournament", players,
            )
            self.assertTrue(entered.wait(2))
            self.service.cancel(job["id"])
            self.assertEqual(self.wait()["state"], "cancelled")
        self.assertTrue(stopped.is_set())
        self.assertEqual(launch.call_count, 1)

    def test_experiment_sweep_uses_an_isolated_runtime_and_promotes_saved_candidate(self):
        self.scan()
        profile = self.projects / "UninstalledAI" / "profiles" / "custom"
        (profile / "config").mkdir(parents=True)
        (profile / "script").mkdir()
        (profile / "profile.json").write_text('{"name":"Hard"}', encoding="utf-8")
        for name in ("init.as", "main.as"):
            (profile / "script" / name).write_text("void Entry() {}", encoding="utf-8")
        config = profile / "config" / "settings.json"
        config.write_text('{"weight": 1}', encoding="utf-8")
        live_engine = self.data / "engine" / self.engine
        (live_engine / "spring-headless.exe").write_bytes(b"engine")
        players = [
            {"short_name": "UninstalledAI", "version": "1", "profile": "custom", "faction": "Armada", "ally": 0},
            {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
        ]
        sweep = {"project": "UninstalledAI", "profile": "custom", "path": "config/settings.json",
                 "line": 1, "column": 12, "values": [2, 3]}
        choices = []

        def run(engine, data, local, map_name, game, players, minutes, stop, progress, **kwargs):
            self.assertNotEqual(engine, live_engine)
            self.assertEqual(data, self.data)
            self.assertEqual(map_name, "Map")
            candidate = next(player for player in players if player.short_name.startswith("StudioSweep"))
            path = (engine / "AI" / "Skirmish" / candidate.short_name / candidate.version
                    / "config" / candidate.profile / "settings.json")
            value = json.loads(path.read_text(encoding="utf-8"))["weight"]
            choices.append(value)
            self.assertFalse((live_engine / "AI" / "Skirmish" / candidate.short_name).exists())
            self.assertEqual(config.read_text(encoding="utf-8"), '{"weight": 1}')
            winner = candidate.ally if value == 3 else 1 - candidate.ally
            summary = f"Winner: side {winner + 1}."
            run_dir = local / "matches" / f"match-sweep-{len(choices)}"
            run_dir.mkdir(parents=True)
            (run_dir / "result.json").write_text(json.dumps({
                "winning_sides": [winner], "result": summary, "engine_exit": 0,
            }), encoding="utf-8")
            return summary, run_dir

        with patch("app.studio_matches.headless.run_match", side_effect=run):
            job = self.service.start_experiment(
                self.bar_root, self.engine, "Game", ["Map", "Map2"], 30, 4, 60, "sweep", players, sweep,
            )
            status = self.wait()
        self.assertEqual(status["state"], "completed", status)
        self.assertEqual(choices, [2, 2, 3, 3])
        self.assertEqual(status["experiment"]["best_candidate_id"], "v2")
        config.write_text('{"weight": 99}', encoding="utf-8")
        restarted = MatchService(self.root, projects_root=self.projects)
        self.assertEqual(len(restarted.history(project="UninstalledAI", profile="custom")), 4)
        won = restarted.promote_experiment(job["id"], "v2", "Winner")
        self.assertEqual(won, {"project": "UninstalledAI", "profile": "winner"})
        promoted = profile.parent / won["profile"] / "config" / "settings.json"
        self.assertEqual(json.loads(promoted.read_text(encoding="utf-8")), {"weight": 3})
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"weight": 99})
        with self.assertRaises(MatchError):
            restarted.promote_experiment(job["id"], "baseline", "Ignore")
        with self.assertRaises(MatchError):
            restarted.promote_experiment(job["id"], "v1", "Losing variant")

    def test_sweep_promotion_requires_a_win_on_a_fully_paired_map(self):
        player = Player("AI", "1", "hard", "Armada", 0, "AI")
        candidates = [Candidate("variant", "Variant", player, True), Candidate("base", "Base", player)]

        def game(map_name, first, second, outcome):
            return {"map": map_name, "side1": first, "side2": second, "outcome": outcome,
                    "simulated_seconds": 60, "script_errors_by_side": {"side1": 0, "side2": 0},
                    "unassigned_script_errors": 0, "engine_error": False, "simulated_limit": False}

        played = [
            game("Map", "variant", "base", "side2"),
            game("Map", "base", "variant", "side1"),
            game("Map2", "variant", "base", "side1"),
        ]
        before = next(row for row in rankings(candidates, played) if row["id"] == "variant")
        self.assertEqual(before["wins"], 1)
        self.assertFalse(before["promotable"])
        played.append(game("Map2", "base", "variant", "side2"))
        after = next(row for row in rankings(candidates, played) if row["id"] == "variant")
        self.assertTrue(after["promotable"])
        self.assertEqual(after["paired_win_rate"], 0.5)

    def test_sync_installed_ai_updates_cached_profiles_without_rescan(self):
        self.scan()
        updated = self.service.sync_installed_ai(
            self.bar_root, self.engine, "AI", "1", "My AI",
            [{"key": "hard", "name": "Hard"}, {"key": "rush", "name": "Rush"}],
        )
        self.assertTrue(updated)
        cached = MatchService(self.root).cached_content(self.bar_root, self.engine)
        ai_entry = next(item for item in cached["ais"] if item["short_name"] == "AI")
        self.assertEqual([p["key"] for p in ai_entry["profiles"]], ["hard", "rush"])
        self.assertFalse(self.service.sync_installed_ai(
            self.bar_root, self.engine, "MissingAI", "1", "Missing", [{"key": "hard", "name": "Hard"}],
        ))
        with patch("app.studio_matches.headless.run_match", return_value=("Winner: side 1.", self.root / "match")):
            self.service.start(
                self.bar_root, self.engine, "Game", "Map",
                [
                    {"short_name": "AI", "version": "1", "profile": "rush", "faction": "Armada", "ally": 0},
                    {"short_name": "BARb", "version": "stable", "profile": "hard", "faction": "Cortex", "ally": 1},
                ],
                10, True,
            )
            self.assertEqual(self.wait()["state"], "completed")


if __name__ == "__main__":
    unittest.main()
