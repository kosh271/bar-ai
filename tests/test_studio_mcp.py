import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.projects import create_project
from app.settings import Settings, save_settings
from app.studio_matches import MatchService
from app.studio_server import StudioServer


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install requirements-mcp.txt to test MCP")
class StudioMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        stock = root / "stock"
        (stock / "config" / "hard").mkdir(parents=True)
        (stock / "script" / "hard").mkdir(parents=True)
        (stock / "config" / "hard" / "settings.json").write_bytes(b'{\r\n  "threshold": 150.0\r\n}\r\n')
        (stock / "script" / "hard" / "init.as").write_text("init", encoding="utf-8")
        (stock / "script" / "hard" / "main.as").write_text("main", encoding="utf-8")
        projects = root / "projects"
        create_project(projects, "Example AI", stock)
        self.profile = projects / "ExampleAI" / "profiles" / "hard"
        settings = root / "settings.json"
        save_settings(settings, Settings(bar_root=str(root), engine="engine-example"))
        self.server = StudioServer(("127.0.0.1", 0), projects_root=projects, settings_path=settings)
        self.server.match_service = MatchService(root, projects_root=projects)
        self.mcp_connected = threading.Event()
        register_client = self.server.register_client

        def register_and_signal(client_id):
            register_client(client_id)
            if client_id.startswith("mcp-"):
                self.mcp_connected.set()

        self.server.register_client = register_and_signal
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def close():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=3)

        self.addCleanup(close)

    async def asyncSetUp(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.studio_mcp", "--port", str(self.server.server_port),
            cwd=Path(__file__).resolve().parent.parent,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        self.addAsyncCleanup(self.close_client)
        self.request_id = 0
        initialized = await self.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
        })
        self.assertIn("tools", initialized["capabilities"])
        self.assertEqual(initialized["serverInfo"]["name"], "BAR AI Studio")
        await self.send({"method": "notifications/initialized"})

    async def close_client(self):
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
                self.fail("MCP server did not exit on EOF")

    async def send(self, message):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}).encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def request(self, method, params):
        self.request_id += 1
        await self.send({"id": self.request_id, "method": method, "params": params})
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=15)
        self.assertTrue(line, "MCP server exited without responding")
        response = json.loads(line)
        self.assertEqual(response["id"], self.request_id)
        self.assertNotIn("error", response)
        return response["result"]

    async def call(self, tool_name, **arguments):
        return await self.request("tools/call", {"name": tool_name, "arguments": arguments})

    def structured(self, result):
        self.assertIn("structuredContent", result, result)
        return result["structuredContent"]

    async def test_stdio_lists_tools_and_edits_through_studio_revision_checks(self):
        tools = {tool["name"]: tool for tool in (await self.request("tools/list", {}))["tools"]}
        self.assertTrue(tools["list_projects"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["save_profile_file"]["annotations"]["destructiveHint"])
        project_history = self.profile.parents[1] / ".git"
        self.assertFalse(project_history.exists())
        projects = self.structured(await self.call("list_projects"))["projects"]
        self.assertEqual(projects[0]["profiles"][0]["key"], "hard")
        self.assertFalse(project_history.exists())
        keys = {"project": "ExampleAI", "profile": "hard", "path": "config/settings.json"}
        unrelated = self.profile / "script" / "main.as"
        unrelated.write_bytes(b"\xff")
        page = self.structured(await self.call("list_profile_files", project="ExampleAI", profile="hard", limit=1))
        self.assertEqual(page["files"][0]["path"], "config/settings.json")
        next_page = self.structured(await self.call(
            "list_profile_files", project="ExampleAI", profile="hard", offset=page["next_offset"],
        ))
        self.assertIn({"path": "script/main.as", "sha256": hashlib.sha256(b"\xff").hexdigest()}, next_page["files"])
        file = self.structured(await self.call("read_profile_file", **keys, start_line=2, line_count=1))
        self.assertEqual(file["text"], '  "threshold": 150.0\r\n')
        edit = '{"threshold": 200}\n'
        saved = self.structured(await self.call("save_profile_file", **keys, sha256=file["sha256"], text=edit))
        self.assertEqual(saved, {"path": keys["path"], "sha256": hashlib.sha256(edit.encode()).hexdigest()})
        conflict = await self.call("save_profile_file", **keys, sha256=file["sha256"], text="{}")
        self.assertTrue(conflict["isError"])
        self.assertEqual((self.profile / keys["path"]).read_bytes(), edit.encode())
        self.assertEqual(unrelated.read_bytes(), b"\xff")
        self.assertTrue(await asyncio.to_thread(self.mcp_connected.wait, 3))
        await self.close_client()
        with self.server._client_lock:
            self.assertFalse(self.server._clients)

    async def test_profile_read_pages_a_single_long_line_without_losing_text(self):
        keys = {"project": "ExampleAI", "profile": "hard", "path": "script/main.as"}
        source = "é" * 120 + "\r\nsecond line\r\n"
        (self.profile / keys["path"]).write_bytes(source.encode())
        first = self.structured(await self.call("read_profile_file", **keys, max_chars=100))
        self.assertEqual(first["text"], "é" * 100)
        self.assertEqual((first["next_line"], first["next_column"]), (1, 101))
        second = self.structured(await self.call(
            "read_profile_file", **keys, start_line=first["next_line"], start_column=first["next_column"],
        ))
        self.assertEqual(first["text"] + second["text"], source)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertIsNone(second["next_line"])
        self.assertIsNone(second["next_column"])

    async def test_tournament_uses_saved_installation_and_shared_game_service(self):
        player = {"short_name": "BARb", "version": "0.1", "profile": "hard", "faction": "Armada", "ally": 0}
        options = {"starting_metal": 2000, "starting_energy": 5000, "starter_base": True, "build_turret": False}
        with patch.object(self.server.match_service, "start_experiment", return_value={"id": "fixture-run"}) as start:
            result = await self.call(
                "start_experiment", game="BAR 1", maps=["Map A"], game_minutes=12, real_time_seconds=180,
                game_limit=2, mode="tournament", players=[player, {**player, "ally": 1}],
                parallel_games=2, start_options=options,
            )
        self.assertEqual(self.structured(result)["id"], "fixture-run")
        args = start.call_args.args
        self.assertEqual(args[1:8], ("engine-example", "BAR 1", ["Map A"], 12, 2, 180, "tournament"))
        self.assertEqual(args[8][0], player)
        self.assertEqual(start.call_args.kwargs["parallel_games"], 2)
        self.assertEqual(start.call_args.kwargs["start_options"], options)

    async def test_sweep_passes_the_selected_source_revision_to_studio(self):
        keys = {"project": "ExampleAI", "profile": "hard", "path": "config/settings.json"}
        (self.profile / keys["path"]).write_bytes('{"title": "before\u2028after",\r\n  "threshold": 150.0\r\n}\r\n'.encode())
        file = self.structured(await self.call("read_profile_file", **keys, start_line=2, line_count=1))
        self.assertEqual(file["text"], '  "threshold": 150.0\r\n')
        revision = {**keys, "line": 2, "column": 17, "sha256": file["sha256"]}
        preview = self.structured(await self.call("preview_sweep_literal", **revision))
        self.assertEqual(preview, {"column": 16, "length": 5})
        sweep = {**revision, "values": [100, 200]}
        with patch.object(self.server.match_service, "start_experiment", return_value={"id": "sweep-run"}) as start:
            result = await self.call(
                "start_experiment", game="BAR 1", maps=["Map A"], game_minutes=12, real_time_seconds=180,
                game_limit=4, mode="sweep", sweep=sweep, players=[
                    {"short_name": "ExampleAI", "profile": "hard", "faction": "Armada", "ally": 0},
                    {"short_name": "BARb", "version": "0.1", "profile": "hard", "faction": "Cortex", "ally": 1},
                ],
            )
        self.assertEqual(self.structured(result)["id"], "sweep-run")
        self.assertEqual(start.call_args.kwargs["sweep"], sweep)
        (self.profile / keys["path"]).write_bytes(b'{"threshold": 200}\n')
        conflict = await self.call("preview_sweep_literal", **revision)
        self.assertTrue(conflict["isError"])
        self.assertIn("Reload the profile", conflict["content"][0]["text"])

    async def test_queue_page_keeps_game_indexes_and_uses_the_active_queue_id(self):
        queue = {"id": "queue-id", "operation": "match", "state": "running", "games": [
            {"game_index": 1, "state": "running", "logs": {"run_id": "match-one"}},
            {"game_index": 2, "state": "queued", "logs": {}},
        ]}
        with patch.object(self.server.match_service, "status", return_value=queue):
            page = self.structured(await self.call("list_queue_games", run_id="queue-id", limit=1))
            self.assertEqual(page["total_games"], 2)
            self.assertEqual(page["games"][0]["logs"]["run_id"], "match-one")
            second_page = self.structured(await self.call(
                "list_queue_games", run_id="queue-id", offset=page["next_offset"], limit=1,
            ))
            self.assertEqual(second_page["games"][0]["game_index"], 2)
            self.assertIsNone(second_page["next_offset"])
            wrong_queue = await self.call("list_queue_games", run_id="old-queue")
            self.assertTrue(wrong_queue["isError"])
            wrong_status = await self.call("get_run_status", run_id="old-queue")
            self.assertTrue(wrong_status["isError"])
        with patch.object(self.server.match_service, "cancel_game", return_value=queue) as cancel:
            response = self.structured(await self.call("cancel_match_game", run_id="queue-id", game_index=2))
        self.assertEqual(response["id"], "queue-id")
        cancel.assert_called_once_with("queue-id", 2)

    async def test_parallel_capacity_can_pause_and_resume_game_dispatch(self):
        with patch.object(self.server.match_service, "_processor_cores", 4):
            paused = self.structured(await self.call("set_parallel_games", parallel_games=0))
            self.assertEqual(paused["parallel_games"], 0)
            resumed = self.structured(await self.call("set_parallel_games", parallel_games=2))
            self.assertEqual(resumed["parallel_games"], 2)
            invalid = await self.call("set_parallel_games", parallel_games=5)
            self.assertTrue(invalid["isError"])

    async def test_filtered_history_provides_run_ids_for_log_reads(self):
        matches = self.server.match_service.local_data / "matches"
        for suffix, map_name in (("one", "Map A & B"), ("two", "Other map")):
            run = matches / f"match-{suffix}"
            run.mkdir(parents=True)
            (run / "result.json").write_text(json.dumps({"result": "Draw.", "map": map_name}), encoding="utf-8")
            (run / "infolog.txt").write_text("Warning: shader setup\n", encoding="utf-8")
        page = self.structured(await self.call("list_matches", map_name="Map A & B", limit=1))
        self.assertEqual([entry["run_dir"] for entry in page["history"]], ["match-one"])
        self.assertIsNone(page["next_offset"])
        log = self.structured(await self.call("read_match_log", run_id=page["history"][0]["run_dir"], file="infolog.txt"))
        self.assertEqual(log["lines"], [{"text": "Warning: shader setup", "category": "engine_warning"}])
        self.assertIsNone(log["next_offset"])
        invalid = await self.call("read_match_log", run_id="match-one", file="unknown.txt")
        self.assertTrue(invalid["isError"])

    async def test_promotion_reports_ineligible_sweep_and_returns_verified_project_keys(self):
        arguments = {"experiment_id": "a" * 32, "candidate_id": "v1", "name": "Winning variant"}
        missing = await self.call("promote_sweep_candidate", **arguments)
        self.assertTrue(missing["isError"])
        self.assertIn("no saved variant", missing["content"][0]["text"])
        promoted = {"project": "ExampleAI", "profile": "winning_variant"}
        with patch.object(self.server.match_service, "promote_experiment", return_value=promoted) as promote:
            result = self.structured(await self.call("promote_sweep_candidate", **arguments))
        self.assertEqual(result, promoted)
        promote.assert_called_once_with(arguments["experiment_id"], arguments["candidate_id"], arguments["name"])

    async def test_install_reports_confirmation_before_replacing_unmanaged_files(self):
        skirmish = self.server.match_service.root / "data" / "engine" / "engine-example" / "AI" / "Skirmish"
        stock = skirmish / "BARb" / "stable"
        stock.mkdir(parents=True)
        (stock / "AIInfo.lua").write_text(
            "local infos = {\n{key='shortName',value='BARb'},\n{key='version',value='stable'},\n"
            "{key='name',value='BARbarIAn'},\n}\nreturn infos\n", encoding="utf-8",
        )
        (stock / "AIOptions.lua").write_text(
            "local options = {\n{key='profile',def='hard'},\n{key='game_config',def=true},\n}\nreturn options\n",
            encoding="utf-8",
        )
        (stock / "SkirmishAI.dll").write_bytes(b"stock fixture")
        target = skirmish / "ExampleAI" / "1"
        target.mkdir(parents=True)
        marker = target / "unmanaged.txt"
        marker.write_bytes(b"existing")

        status = self.structured(await self.call("get_project_install_status", project="ExampleAI"))
        self.assertTrue(status["installed"])
        self.assertFalse(status["managed"])
        prompt = self.structured(await self.call("install_project", project="ExampleAI"))["confirmation"]
        self.assertIn("Replace the existing unmanaged AI files?", prompt)
        self.assertEqual(marker.read_bytes(), b"existing")
        installed = self.structured(await self.call("install_project", project="ExampleAI", confirmations=[prompt]))
        self.assertTrue(installed["changed"])
        self.assertEqual(Path(installed["path"]), target)
        self.assertFalse(marker.exists())
        status = self.structured(await self.call("get_project_install_status", project="ExampleAI"))
        self.assertEqual(status["status"], "up_to_date")
