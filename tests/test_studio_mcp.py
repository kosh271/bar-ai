import asyncio
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

    async def call(self, name, **arguments):
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    def structured(self, result):
        self.assertIn("structuredContent", result, result)
        return result["structuredContent"]

    async def test_stdio_lists_tools_and_edits_through_studio_revision_checks(self):
        tools = {tool["name"]: tool for tool in (await self.request("tools/list", {}))["tools"]}
        self.assertTrue(tools["list_projects"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["save_profile_file"]["annotations"]["destructiveHint"])
        projects = self.structured(await self.call("list_projects"))["projects"]
        self.assertEqual(projects[0]["profiles"][0]["key"], "hard")
        keys = {"project": "ExampleAI", "profile": "hard", "path": "config/settings.json"}
        file = self.structured(await self.call("read_profile_file", **keys, start_line=2, line_count=1))
        self.assertEqual(file["text"], '  "threshold": 150.0\r\n')
        edit = '{"threshold": 200}\n'
        saved = await self.call("save_profile_file", **keys, sha256=file["sha256"], text=edit)
        self.assertFalse(saved.get("isError"))
        conflict = await self.call("save_profile_file", **keys, sha256=file["sha256"], text="{}")
        self.assertTrue(conflict["isError"])
        self.assertEqual((self.profile / keys["path"]).read_bytes(), edit.encode())
        self.assertTrue(await asyncio.to_thread(self.mcp_connected.wait, 3))
        await self.close_client()
        with self.server._client_lock:
            self.assertFalse(self.server._clients)

    async def test_tournament_uses_saved_installation_and_shared_game_service(self):
        player = {"short_name": "BARb", "version": "0.1", "profile": "hard", "faction": "Armada", "ally": 0}
        with patch.object(self.server.match_service, "start_experiment", return_value={"id": "fixture-run"}) as start:
            result = await self.call(
                "start_experiment", game="BAR 1", maps=["Map A"], game_minutes=12, real_time_seconds=180,
                game_limit=2, mode="tournament", players=[player, {**player, "ally": 1}],
            )
        self.assertEqual(self.structured(result)["id"], "fixture-run")
        args = start.call_args.args
        self.assertEqual(args[1:8], ("engine-example", "BAR 1", ["Map A"], 12, 2, 180, "tournament"))
        self.assertEqual(args[8][0], player)

    async def test_sweep_passes_the_selected_source_revision_to_studio(self):
        keys = {"project": "ExampleAI", "profile": "hard", "path": "config/settings.json"}
        (self.profile / keys["path"]).write_bytes('{"title": "before\u2028after",\r\n  "threshold": 150.0\r\n}\r\n'.encode())
        file = self.structured(await self.call("read_profile_file", **keys, start_line=2, line_count=1))
        self.assertEqual(file["text"], '  "threshold": 150.0\r\n')
        sweep = {**keys, "line": 2, "column": 17, "values": [100, 200], "sha256": file["sha256"]}
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
