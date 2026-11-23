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

    def test_live_snapshot_requires_complete_sample_and_uses_engine_side_mapping(self):
        players = [self.players[1], self.players[0]]
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            config = run / "LuaUI" / "Config"
            config.mkdir(parents=True)
            snapshot = config / "studio_match_live.txt"
            data = ("451\nTeam 0 stats: metalProduced=15, energyProduced=120, damageDealt=8\n"
                    "Team 1 stats: metalProduced=20, energyProduced=60, damageDealt=0\n")
            snapshot.write_text(data, encoding="ascii")
            self.assertIsNone(headless.live_match_sample(run, players))
            snapshot.write_text(data + "end\n", encoding="ascii")
            sample = headless.live_match_sample(run, players)
            self.assertAlmostEqual(sample["simulated_seconds"], 451 / 30)
            self.assertEqual(sample["side_totals"], [
                {"resources": 21, "damage": 0}, {"resources": 17, "damage": 8},
            ])

    def test_live_clock_can_use_recent_complete_engine_log_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "infolog.txt").write_text(
                "[f=-000001] loading\n" + "x" * 20000 +
                "\n[t=00:00:01.000000][f=0000900] simulation started\n[f=0001200",
                encoding="utf-8",
            )
            self.assertIsNone(headless.live_match_sample(run, self.players))
            self.assertEqual(headless._logged_game_seconds(run), 30)

    @patch("app.headless.subprocess.Popen")
    def test_match_collects_team_totals_without_retaining_a_replay(self, popen):
        def launch(command, **kwargs):
            run = Path(command[-1]).parent
            self.assertIn("RecordDemo=0;", (run / "script.txt").read_text(encoding="utf-8"))
            self.assertTrue((run / "LuaUI" / "Widgets" / "studio_match_stats.lua").is_file())
            (run / "infolog.txt").write_text(
                "[f=0000901] Team 0 stats: damageDealt=7, metalProduced=11, energyProduced=20\n"
                "[f=0001800] Executing debugcommand, quitforce\n"
                "[f=0001800] Team 0 stats: damageDealt=15, metalProduced=25, energyProduced=35\n"
                "[f=0001800] Team 1 stats: damageDealt=2, metalProduced=20, energyProduced=40\n",
                encoding="utf-8",
            )
            return process

        process = unittest.mock.Mock()
        process.poll.return_value = process.wait.return_value = 0
        popen.side_effect = launch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spring-headless.exe").touch()
            _, run = run_match(root, root, root, "Map", "Game", self.players, 1, self.stop, skip_smoke=True,
                               capture_stats=True)
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
            stats = [team["stats"] for team in result["telemetry"]["teams"]]
            self.assertEqual([row["damage_dealt"] for row in stats], [15, 2])
            self.assertEqual([row["metal_produced"] for row in stats], [25, 20])
            self.assertEqual([row["energy_produced"] for row in stats], [35, 40])
            self.assertEqual(result["replays"], [])
            self.assertIn("Time limit reached", result["result"])

    @patch("app.headless.subprocess.Popen")
    def test_opt_in_records_only_match_and_persists_replays_and_telemetry(self, popen):
        def launch(command, **kwargs):
            run = Path(command[-1]).parent
            match = run.name.startswith("match-")
            self.assertIn(f"RecordDemo={int(match)};", (run / "script.txt").read_text(encoding="utf-8"))
            script = r"LuaRules\Configs\BARb\stable\script\hard\main.as"
            (run / "infolog.txt").write_text(f"Skirmish AI <AI-1>: Load script: {script}\n"
                                            "[t=00:01:01.000000][f=0001800] Executing debugcommand, quitforce\n",
                                            encoding="utf-8")
            if match:
                (run / "demos").mkdir()
                (run / "demos" / "example.sdfz").write_bytes(b"demo")
                (run / "demos" / "notes.txt").write_text("text", encoding="utf-8")
            return process

        process = unittest.mock.Mock()
        process.poll.return_value = process.wait.return_value = 0
        popen.side_effect = launch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spring-headless.exe").touch()
            self.assertIn("RecordDemo=0;", headless.build_script("Map", "Game", self.players, 1))
            _, run = run_match(root, root, root, "Map", "Game", self.players, 1, self.stop,
                               record_demo=True)
            details = json.loads((run / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(details["replays"], [{"path": "demos/example.sdfz", "size_bytes": 4}])
            self.assertEqual(details["telemetry"]["end_frame"], 1800)
        self.assertEqual(popen.call_count, 2)

    @patch("app.headless.subprocess.Popen")
    def test_extra_units_pack_applies_to_smoke_and_match(self, popen):
        def launch(command, **kwargs):
            run = Path(command[-1]).parent
            self.assertIn("experimentalextraunits=1;", (run / "script.txt").read_text(encoding="utf-8"))
            (run / "infolog.txt").write_text(
                "Skirmish AI <AI-1>: Load script: script/hard/main.as\n"
                "[f=0001800] Executing debugcommand, quitforce\n", encoding="utf-8"
            )
            return process

        process = unittest.mock.Mock()
        process.poll.return_value = process.wait.return_value = 0
        popen.side_effect = launch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spring-headless.exe").touch()
            self.assertNotIn("experimentalextraunits=", headless.build_script("Map", "Game", self.players, 1))
            run_match(root, root, root, "Map", "Game", self.players, 1, self.stop, extra_units=True)
        self.assertEqual(popen.call_count, 2)

    def test_match_telemetry_associates_startup_and_explicit_team_events(self):
        log = r"""
[t=00:00:08.800001][f=-000001] Skirmish AI <AI-1>: Load script: LuaRules\Configs\BARb\stable\script\hard\main.as
[t=00:00:08.800002][f=-000001] Skirmish AI <AI-1>: AI 1.6.29 (0) Initialized!
[t=00:00:08.800003][f=-000001] Skirmish AI <AI-1>: Load script: LuaRules\Configs\BARb\stable\script\normal\main.as
[t=00:00:08.800004][f=-000001] Skirmish AI <AI-1>: AI 1.6.29 (1) Initialized!
[t=00:00:09.000000][f=0000010] Skirmish AI <AI-1>: [Team 0] Selected factory: armlab
[t=00:00:09.100000][f=0000030] Skirmish AI <AI-1>: [Team 0] Opener queue: armck, armpw
[t=00:00:10.100000][f=0001000] Skirmish AI <AI-1>: [Team 1] Switching factory: corlab -> corvp
[t=00:00:12.000000][f=0001200] Skirmish AI <AI-1>: [Team 1] AngelScript runtime exception: null pointer
[t=00:00:12.000001][f=0001300] Skirmish AI <AI-1>: Selected factory: armvp
[t=00:00:12.000002][f=0001800] Team 0 stats: metalProduced=15, unitsKilled=3
[t=00:00:12.100000][f=0001860] Game over
[t=00:00:12.200000][f=0001863] [Game::KillRendering][1]
"""
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            config = run / "LuaUI" / "Config"
            config.mkdir(parents=True)
            (config / "teamstats.json").write_text(
                '{"teams":{"1":[{"metalProduced":12},{"metalProduced":28,"unitsProduced":7}],"bad":null}}',
                encoding="utf-8",
            )
            summary = headless.match_telemetry(run, self.players, log, log)
        self.assertEqual((summary["end_frame"], summary["duration"]), (1860, "01:02"))
        self.assertEqual(summary["ai_script_errors"], 1)
        first, second = summary["teams"]
        self.assertEqual(first["profile_loaded"], "hard")
        self.assertEqual(first["factory"], "armlab")
        self.assertEqual(first["opener"], "armck, armpw")
        self.assertEqual(first["stats"], {"metal_produced": 15, "units_killed": 3})
        self.assertEqual(second["profile_loaded"], "normal")
        self.assertEqual(second["factory_switches"], ["corlab -> corvp"])
        self.assertEqual(second["ai_script_errors"], 1)
        self.assertEqual(second["stats"], {"metal_produced": 28, "units_produced": 7})
        self.assertEqual(summary["unassigned_ai"]["factory"], "armvp")

    def test_partial_telemetry_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            config = run / "LuaUI" / "Config"
            config.mkdir(parents=True)
            (config / "teamstats.json").write_text('{"teams":[', encoding="utf-8")
            missing = headless.match_telemetry(run, self.players, "[f=-000001] pregame", "")
            self.assertIsNone(missing["duration"])
            self.assertEqual(missing["teams"][0]["stats"], {})
            early = headless.match_telemetry(run, self.players, "[f=0000196] Team 1 was defeated\n"
                                             "[f=0000199] [Game::KillRendering][1]", "")
            self.assertEqual((early["end_frame"], early["duration"]), (196, "00:06"))
        for line, category in (
            ("[f=0000001] script.as (12, 5) : ERR Unexpected token", "ai_script"),
            ("Skirmish AI <BARbarIAn-stable>: Load script: main.as", "ai_script"),
            ("Error: Skirmish AI BARb not found!", "ai_script"),
            ("Error: sync failure", "engine_error"),
            ("Warning: weaponDef missing model", "engine_warning"),
            ("LuaShader: shader error(s):", "engine_warning"),
            ("[unitdefs.lua] yardmap row has incorrect length", "engine_warning"),
            ("Loaded synced gadget", "other"),
        ):
            with self.subTest(line=line):
                self.assertEqual(headless.diagnostic_category(line), category)

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

    @patch("app.headless.subprocess.Popen")
    def test_failed_smoke_persists_compilation_error_telemetry(self, popen):
        process = popen.return_value
        process.poll.return_value = process.wait.return_value = 0

        def launch(command, **kwargs):
            path = Path(command[command.index("--write-dir") + 1])
            (path / "infolog.txt").write_text(
                "main.as (12, 7) : ERR Unexpected token\n", encoding="utf-8",
            )
            return process

        popen.side_effect = launch
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "spring-headless.exe").touch()
            passed, _, run = headless._run_smoke(work, work, work / "local", "Map", "Game", self.players, self.stop)
            metadata = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertFalse(passed)
        self.assertEqual(metadata["telemetry"]["ai_script_errors"], 1)

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


class MultiAIRosterScriptTests(unittest.TestCase):
    def test_build_script_supports_2v2_and_asymmetric_rosters(self):
        players_2v2 = [
            Player("MyAI", "1", "hard", "Armada", 0, "My AI"),
            Player("BARb", "stable", "medium", "Cortex", 0, "BARbarIAn"),
            Player("MyAI", "1", "fast", "Legion", 1, "My AI"),
            Player("BARb", "stable", "hard", "Armada", 1, "BARbarIAn"),
        ]
        script_2v2 = headless.build_script("Map", "Game", players_2v2, 30)
        for index in range(4):
            self.assertIn(f"[AI{index}]", script_2v2)
            self.assertIn(f"[TEAM{index}]", script_2v2)
        self.assertIn("profile=fast;", script_2v2)
        self.assertEqual(script_2v2.count("AllyTeam=0;"), 2)
        self.assertEqual(script_2v2.count("AllyTeam=1;"), 2)

        asymmetric = players_2v2[:3]
        script_2v1 = headless.build_script("Map", "Game", asymmetric, 20)
        self.assertIn("[AI2]", script_2v1)
        self.assertNotIn("[AI3]", script_2v1)
        self.assertEqual(script_2v1.count("AllyTeam=0;"), 2)
        self.assertEqual(script_2v1.count("AllyTeam=1;"), 1)


if __name__ == "__main__":
    unittest.main()
