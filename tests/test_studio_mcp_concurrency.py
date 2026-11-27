import asyncio
import importlib.util
import threading
import unittest

from app.studio_mcp import create_server


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install requirements-mcp.txt to test MCP")
class StudioMcpConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_studio_call_does_not_block_another_tool(self):
        class Studio:
            def request(self, path):
                if path == "/api/projects":
                    entered.set()
                    release.wait(timeout=2)
                    return {"projects": [], "project_issues": []}
                return {"id": "example-run", "operation": "experiment", "state": "running"}

        entered, release = threading.Event(), threading.Event()
        server = create_server(Studio())
        slow_call = asyncio.create_task(server.call_tool("list_projects", {}))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await asyncio.wait_for(server.call_tool("get_run_status", {}), timeout=1)
            self.assertFalse(slow_call.done())
        finally:
            release.set()
            await slow_call

    async def test_benchmark_admission_and_experiment_record_pages(self):
        class Studio:
            def installation(self):
                return {"bar_root": "fixture", "engine": "fixture"}

            def request(self, path, payload=None, **query):
                if path == "/api/match/start":
                    submitted.append(payload)
                    return {"operation": "match", "submission": {"batch_id": "benchmark-1", "game_count": 4}}
                if path == "/api/experiment/history":
                    if query.get("id") != "fixture-experiment":
                        raise AssertionError(query)
                    return {"experiment": record}
                raise AssertionError(path)

        submitted = []
        record = {"games": [{"index": 1}, {"index": 2}], "schedule": [{"index": i} for i in range(1, 5)]}
        server = create_server(Studio())
        series = {"games": 4, "maps": ["A", "B"], "swap_sides": True, "faction_pairs": [["Armada", "Cortex"]]}
        players = [
            {"short_name": "One", "version": "1", "faction": "Armada", "ally": 0},
            {"short_name": "Two", "version": "1", "faction": "Cortex", "ally": 1},
        ]
        _, accepted = await server.call_tool("start_test_match", {
            "game": "BAR", "map_name": "A", "players": players, "game_minutes": 10, "series": series,
        })
        self.assertEqual(submitted[0]["series"], series)
        self.assertEqual(accepted["submission"]["batch_id"], "benchmark-1")
        _, page = await server.call_tool("get_experiment", {
            "experiment_id": "fixture-experiment", "game_offset": 1, "game_limit": 2,
        })
        self.assertEqual(page["experiment"]["games"], [{"index": 2}])
        self.assertEqual(page["experiment"]["schedule"], [{"index": 2}, {"index": 3}])
        self.assertEqual(page["total_games"], 4)
        self.assertEqual(page["next_game_offset"], 3)
        self.assertEqual(len(record["schedule"]), 4)
