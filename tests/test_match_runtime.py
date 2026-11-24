import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from app import headless, studio_experiments, studio_storage
from app.studio_matches import GameExecution, MatchBatch, MatchService, QueuedGame


class MatchRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = MatchService(directory.name)
        self.run = self.service.local_data / "matches" / "match-test"
        self.run.mkdir(parents=True)

    def test_shutdown_cancels_scan_and_rejects_further_operations(self):
        started = threading.Event()
        finished = threading.Event()

        def scan():
            started.set()
            self.service._stop.wait(2)
            finished.set()
            return {}, {}, "completed"

        self.service._launch("scan", scan)
        self.addCleanup(self.service._stop.set)
        self.assertTrue(started.wait(1))
        shutdown = getattr(self.service, "shutdown", lambda: None)
        shutdown()
        self.assertTrue(finished.is_set())
        self.assertEqual(self.service.status()["state"], "cancelled")
        with self.assertRaisesRegex(headless.MatchError, "shutting down"):
            self.service._launch("catalog", scan)

    def test_another_studio_process_cannot_clean_active_artifacts(self):
        artifact = self.run / "infolog.txt"
        artifact.write_text("engine output", encoding="utf-8")
        release = threading.Event()

        def match():
            release.wait(10)
            return {}, {}, "completed"

        attempt = """
import sys
from app import headless
from app.studio_matches import MatchService
service = MatchService(sys.argv[1])
try:
    service.cleanup_runs([sys.argv[2]])
except headless.MatchError:
    print("blocked")
else:
    print("removed")
"""
        self.service._launch("match", match)
        try:
            args = [sys.executable, "-B", "-c", attempt, str(self.service.root), self.run.name]
            completed = subprocess.run(args, capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(completed.stdout.strip(), "blocked")
            self.assertTrue(artifact.is_file())
            self.assertTrue(self.service.pin_run(self.run.name, True)["pinned"])
            self.assertFalse(self.service.pin_run(self.run.name, False)["pinned"])
        finally:
            release.set()
            self.service.shutdown()
        completed = subprocess.run(args, capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(completed.stdout.strip(), "removed")
        self.assertFalse(self.run.exists())

    def test_abrupt_process_exit_releases_operation_lease(self):
        subprocess.run([sys.executable, "-B", "-c", """
import os, sys
from pathlib import Path
from app.studio_storage import operation_lease
with operation_lease(Path(sys.argv[1]), Path(sys.argv[2])):
    os._exit(0)
""", str(self.service.root), str(self.service.local_data)], check=True, timeout=10)
        self.assertEqual(self.service.cleanup_runs([self.run.name])["removed"], [self.run.name])

    def test_pin_and_cleanup_share_an_artifact_mutation_lease(self):
        attempt = """
import sys
from app import headless
from app.studio_matches import MatchService
service = MatchService(sys.argv[1])
for mutate in (lambda: service.pin_run(sys.argv[2], True), lambda: service.cleanup_runs([sys.argv[2]])):
    try:
        mutate()
    except headless.MatchError:
        print("blocked")
    else:
        print("changed")
"""
        with studio_storage.artifact_mutation_lease(self.service.root, self.service.local_data):
            completed = subprocess.run(
                [sys.executable, "-B", "-c", attempt, str(self.service.root), self.run.name],
                capture_output=True, text=True, check=True, timeout=10,
            )
        self.assertEqual(completed.stdout.splitlines(), ["blocked", "blocked"])
        self.assertTrue(self.run.is_dir())

    def test_cleanup_kills_child_that_does_not_terminate(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("child", 1), 1]
        cleanup = getattr(headless, "_stop_child", lambda _process: None)
        cleanup(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_append_preserves_malformed_history(self):
        history = self.run.parent / "history.json"
        malformed = b'{"version":1,"entries":[{"id":"important"}'
        history.write_bytes(malformed)
        with self.assertRaises(headless.MatchError):
            self.service._append_history({"id": "new"})
        self.assertEqual(history.read_bytes(), malformed)

    def test_artifact_enrichment_waits_for_log_reader(self):
        result = self.run / "result.json"
        result.write_text("{}", encoding="utf-8")
        original_open = headless.open_artifact
        reader_started = threading.Event()
        release_reader = threading.Event()
        replaced = threading.Event()
        writer_started = threading.Event()
        errors = []

        @contextmanager
        def open_artifact(*args, **kwargs):
            with original_open(*args, **kwargs) as stream:
                if threading.current_thread() is reader:
                    reader_started.set()
                    release_reader.wait(2)
                yield stream

        def enrich():
            writer_started.set()
            try:
                self.service._enrich_result_json(self.run, {"outcome": "side1"})
            except Exception as error:
                errors.append(error)
            finally:
                replaced.set()

        reader = threading.Thread(target=self.service.read_log, args=(self.run.name, "result.json"))
        writer = threading.Thread(target=enrich)
        with patch("app.studio_matches.headless.open_artifact", side_effect=open_artifact):
            reader.start()
            try:
                self.assertTrue(reader_started.wait(1))
                writer.start()
                self.assertTrue(writer_started.wait(1))
                self.assertFalse(replaced.wait(0.15))
            finally:
                release_reader.set()
                reader.join(2)
                if writer.ident is not None:
                    writer.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(result.read_bytes())["outcome"], "side1")

    def test_game_over_telemetry_uses_explicit_terminal_frame(self):
        log = (
            "[f=001800] Studio match GameOver frame: 1800\n"
            "[f=003600] Executing debugcommand, quitforce\n"
            "[f=003601] Shutting down\n"
        )
        telemetry = headless.match_telemetry(self.run, [], log, "")
        self.assertEqual(telemetry["end_frame"], 1800)
        self.assertEqual(telemetry["end_frame_source"], "game_over")

    def test_last_log_frame_does_not_enter_rankings_as_duration(self):
        metrics = studio_experiments.telemetry_metrics({
            "telemetry": {"end_frame_source": "last_log_frame", "end_frame": 1800},
        })
        self.assertIsNone(metrics["simulated_seconds"])

    def test_missing_match_diagnostics_are_unverified(self):
        samples = [
            ({}, False),
            ({"engine_exit": 0}, False),
            ({"engine_exit": False, "telemetry": {"ai_script_errors": 0}}, False),
            ({"engine_exit": 0, "telemetry": {"ai_script_errors": -1}}, False),
            ({"smoke_passed": True, "engine_exit": 0, "telemetry": {"ai_script_errors": 0}}, False),
            ({"engine_exit": 0, "telemetry": {"ai_script_errors": 0}}, True),
        ]
        for metadata, expected in samples:
            with self.subTest(metadata=metadata):
                self.assertEqual(studio_experiments.telemetry_metrics(metadata).get("telemetry_verified"), expected)

    def test_structured_winner_survives_missing_result_metadata(self):
        terminal = headless.MatchTerminal("game_over", time.monotonic(), (0,))
        self.assertEqual(
            self.service._classify_game_outcome(
                "Could not save result metadata.", self.run, False, False, True, [], terminal=terminal,
            ),
            ("side2", False),
        )

    def test_cancel_queue_preserves_game_that_already_reached_terminal_event(self):
        task = GameExecution(terminal=headless.MatchTerminal("game_over", time.monotonic(), (0,)))
        self.service._active_games[1] = task
        with self.service._lock:
            self.service._request_cancel_locked()
        self.assertTrue(task.stop.is_set())
        self.assertFalse(task.cancel_requested)

    def test_simulated_limit_after_wall_deadline_is_a_wall_timeout(self):
        summary = "Time limit reached (1 game minutes)."

        def run(_engine, _data, _local, _map, _game, _players, _minutes, stop, _progress, **_kwargs):
            self.assertTrue(stop.wait(2))
            return summary, self.run

        task = GameExecution()
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            _, _, timed_out, cancelled, events = self.service._run_game(
                self.run, self.run, "Map", "Game", [], 1, skip_smoke=True,
                task=task, real_time_deadline=time.monotonic() + 0.02,
            )
        self.assertFalse(cancelled)
        self.assertTrue(timed_out)
        self.assertEqual(
            self.service._classify_game_outcome(summary, self.run, timed_out, cancelled, False, events),
            ("timeout", True),
        )
        finish_cause = getattr(task, "finish_cause", None)
        self.assertEqual(finish_cause, "wall_timeout")

    def test_engine_terminal_event_before_deadline_survives_slow_finalization(self):
        def run(_engine, _data, _local, _map, _game, _players, _minutes, stop, progress, **_kwargs):
            progress(headless.MatchProgress("match", "Finished", terminal=headless.MatchTerminal(
                "simulated_limit", time.monotonic(),
            )))
            self.assertTrue(stop.wait(2))
            return "Time limit reached (1 game minutes).", self.run

        task = GameExecution()
        with patch("app.studio_matches.headless.run_match", side_effect=run):
            _, _, timed_out, _, _ = self.service._run_game(
                self.run, self.run, "Map", "Game", [], 1, skip_smoke=True,
                task=task, real_time_deadline=time.monotonic() + 0.1,
            )
        self.assertFalse(timed_out)
        self.assertEqual(task.finish_cause, "simulated_limit")

    def test_history_write_failure_preserves_game_outcome(self):
        batch = MatchBatch(
            "test", self.run, self.run, "engine", "Game", 1, True, False, False,
            None, None, [], [],
        )
        batch.games.append(QueuedGame(
            batch, [], None, path=self.run, outcome="side1", started=time.monotonic(),
            row={"map": "Map", "players": [], "elapsed_seconds": 0, "summary": "Winner: side 1."},
        ))
        with patch.object(self.service, "_append_history", side_effect=PermissionError("history unavailable")):
            result, _, state = self.service._record_batch(batch)
        self.assertEqual(state, "completed")
        self.assertIn("Winner: side 1.", result["summary"])
        self.assertIn("Could not save match history", batch.history_error)

    def test_artifact_write_failure_does_not_skip_history(self):
        (self.run / "result.json").write_text("{}", encoding="utf-8")
        batch = MatchBatch(
            "test", self.run, self.run, "engine", "Game", 1, True, False, False,
            None, None, [], [],
        )
        batch.games.append(QueuedGame(
            batch, [], None, path=self.run, outcome="side2", started=time.monotonic(),
            row={"map": "Map", "players": [], "elapsed_seconds": 0, "summary": "Winner: side 2."},
        ))
        with patch.object(self.service, "_enrich_result_json", side_effect=PermissionError("result unavailable")):
            _, _, state = self.service._record_batch(batch)
        self.assertEqual(state, "completed")
        self.assertEqual(self.service.history()[0]["outcome"], "Winner: side 2.")
        self.assertIn("Could not annotate result", batch.history_error)
