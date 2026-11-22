import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import headless
from app.headless import MatchError, Player, archive_cache_path, run_match


class HeadlessMatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = Path("engine")
        self.data = Path("game-data")
        self.local_data = Path("local-data")
        self.players = [
            Player("AI", "1", "hard", "Armada", 0, "AI"),
            Player("AI", "1", "hard", "Cortex", 1, "AI"),
        ]
        self.stop = threading.Event()
        self.events = []

    def run_match(self, **kwargs):
        return run_match(self.engine, self.data, self.local_data, "Map", "Game", self.players, 30,
                         self.stop, self.events.append, **kwargs)

    def test_replay_discovery_skips_linked_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            (outside / "test").mkdir(parents=True)
            (outside / "mock.sdfz").write_bytes(b"demo")
            run = root / "match-test"
            (run / "demos").mkdir(parents=True)
            (run / "demos" / "empty.sdfz").touch()
            try:
                (run / "demos" / "linked.sdfz").symlink_to(outside / "mock.sdfz")
                (outside / "test" / "demos").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Cannot create symlinks: {error}")
            self.assertEqual(headless.replay_files(run), [])
            self.assertEqual(headless.replay_files(outside / "test"), [])

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_smoke_pass_and_match_have_separate_durations(self, smoke, match):
        smoke.return_value = True, "Smoke test passed.", Path("smoke")
        match.return_value = "Winner: side 2.\nMatch reported 2 diagnostics.", Path("match")
        with patch("app.headless.time.monotonic", side_effect=[10, 15, 20, 33]):
            summary, path = self.run_match()
        self.assertEqual(path, Path("match"))
        self.assertIn("Smoke logs: smoke", summary)
        self.assertIn("Winner: side 2.", summary)
        self.assertEqual([(event.phase, event.summary, event.seconds) for event in self.events], [
            ("smoke", "Passed", 5), ("match", "Running", None), ("match", "Winner: side 2.", 13),
        ])
        self.assertEqual(match.call_args.kwargs["archive_cache"], Path("smoke") / "cache")

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_failed_smoke_does_not_start_match(self, smoke, match):
        smoke.return_value = False, "Smoke test failed.", Path("smoke")
        with patch("app.headless.time.monotonic", side_effect=[10, 17]):
            summary, path = self.run_match()
        self.assertEqual(path, Path("smoke"))
        self.assertIn("Smoke test failed.", summary)
        self.assertEqual((self.events[0].summary, self.events[0].seconds), ("Failed", 7))
        match.assert_not_called()

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_skip_smoke_uses_shared_archive_cache(self, smoke, match):
        match.return_value = "Winner: side 1.", Path("match")
        with patch("app.headless.time.monotonic", side_effect=[10, 19]):
            summary, _ = self.run_match(skip_smoke=True)
        smoke.assert_not_called()
        self.assertIn("Smoke test skipped", summary)
        self.assertEqual(match.call_args.kwargs["archive_cache"],
                         archive_cache_path(self.local_data, self.engine, self.data))
        self.assertEqual([(event.summary, event.seconds) for event in self.events], [
            ("Skipped", None), ("Running", None), ("Winner: side 1.", 9),
        ])

    @patch("app.headless.subprocess.Popen")
    @patch("app.headless._run_smoke")
    def test_skip_smoke_copies_shared_cache_into_real_match_directory(self, smoke, popen):
        popen.return_value.poll.return_value = 0
        popen.return_value.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine, data, local_data = root / "engine", root / "game", root / "local"
            engine.mkdir()
            (engine / "spring-headless.exe").touch()
            cache = archive_cache_path(local_data, engine, data)
            cache.mkdir(parents=True)
            (cache / "ArchiveCache17.lua").write_text("cached", encoding="utf-8")
            processes = []
            summary, match_path = run_match(engine, data, local_data, "Map", "Game", self.players, 30,
                                            self.stop, self.events.append, skip_smoke=True,
                                            process_started=processes.append)
            self.assertEqual((match_path / "cache" / "ArchiveCache17.lua").read_text(encoding="utf-8"),
                             "cached")
            self.assertIn("without a confirmed result", summary)
            self.assertEqual(processes, [popen.return_value])
        smoke.assert_not_called()
        popen.assert_called_once()

    @patch("app.headless.subprocess.Popen")
    def test_smoke_reports_its_engine_process(self, popen):
        popen.return_value.poll.return_value = 0
        popen.return_value.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "engine"
            engine.mkdir()
            (engine / "spring-headless.exe").touch()
            processes = []
            summary, _ = run_match(engine, root / "game", root / "local", "Map", "Game", self.players, 30,
                                   self.stop, self.events.append, process_started=processes.append)
        self.assertIn("Smoke test failed", summary)
        self.assertEqual(processes, [popen.return_value])
        popen.assert_called_once()

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_smoke_error_reports_elapsed_time_and_remains_an_error(self, smoke, match):
        smoke.side_effect = MatchError("Cannot start smoke test")
        with patch("app.headless.time.monotonic", side_effect=[10, 13]):
            with self.assertRaisesRegex(MatchError, "Cannot start smoke test"):
                self.run_match()
        self.assertEqual((self.events[-1].phase, self.events[-1].summary, self.events[-1].seconds),
                         ("smoke", "Failed", 3))
        match.assert_not_called()

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_match_error_reports_elapsed_time_and_remains_an_error(self, smoke, match):
        smoke.return_value = True, "Smoke test passed.", Path("smoke")
        match.side_effect = MatchError("Engine failed")
        with patch("app.headless.time.monotonic", side_effect=[10, 15, 20, 27]):
            with self.assertRaisesRegex(MatchError, "Engine failed"):
                self.run_match()
        self.assertEqual((self.events[-1].phase, self.events[-1].summary, self.events[-1].seconds),
                         ("match", "Failed", 7))

    @patch("app.headless._run_match")
    @patch("app.headless._run_smoke")
    def test_stop_after_smoke_does_not_start_match(self, smoke, match):
        def finish_smoke(*_args, **_kwargs):
            self.stop.set()
            return True, "Smoke test passed.", Path("smoke")

        smoke.side_effect = finish_smoke
        with patch("app.headless.time.monotonic", side_effect=[10, 14]):
            summary, _ = self.run_match()
        self.assertIn("match not started", summary)
        self.assertEqual(self.events[0].summary, "Passed")
        self.assertEqual(self.events[-1].summary, "Not started (stopped)")
        match.assert_not_called()


if __name__ == "__main__":
    unittest.main()
