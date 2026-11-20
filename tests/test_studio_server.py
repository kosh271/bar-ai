import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import nullcontext, redirect_stderr
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from app.game_sources import GameSource
from app.projects import create_project
from app.studio_server import StudioServer
from app.studio_matches import MatchService


TEST_DIR = Path(__file__).resolve().parent


class StudioServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        stock = root / "stock"
        (stock / "config" / "hard").mkdir(parents=True)
        (stock / "script" / "hard").mkdir(parents=True)
        (stock / "config" / "hard" / "settings.json").write_bytes(b'{\r\n  "name": "\xc3\xa9"\r\n}\r\n')
        (stock / "script" / "hard" / "init.as").write_text("init", encoding="utf-8")
        (stock / "script" / "hard" / "main.as").write_text("main", encoding="utf-8")
        self.projects = root / "projects"
        create_project(self.projects, "Example AI", stock)
        self.settings = root / "settings.json"
        self.studio = root / "studio"
        self.studio.mkdir()
        (self.studio / "index.html").write_text("editor", encoding="utf-8")
        self.server = StudioServer(
            ("127.0.0.1", 0),
            projects_root=self.projects,
            settings_path=self.settings,
            studio_root=self.studio,
        )
        self.server.match_service = MatchService(root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _request(self, path, *, method="GET", body=None, token=True, headers=None):
        request_headers = dict(headers or {})
        if token:
            request_headers["X-Workshop-Token"] = self.server.session_token
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data, headers=request_headers, method=method,
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, json.loads(response.read().decode("utf-8")) if (
            response.headers.get_content_type() == "application/json"
        ) else response.read().decode("utf-8"), response.headers

    def test_api_requires_token_and_rejects_cross_origin(self):
        status, _, _ = self._request("/api/state", token=False)
        self.assertEqual(status, 401)
        status, _, _ = self._request(
            "/api/client/heartbeat", method="POST", body={"client_id": "tab-one"}, token=False,
        )
        self.assertEqual(status, 401)
        status, _, _ = self._request(
            "/api/state", headers={"Origin": "http://attacker.example"},
        )
        self.assertEqual(status, 403)

        status, _, _ = self._request("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)
        status, state, headers = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["projects"][0]["key"], "ExampleAI")
        self.assertIsNone(state["settings"])
        self.assertIn("no-store", headers["Cache-Control"])
        status, bootstrap, _ = self._request("/session.js", token=False)
        self.assertEqual(status, 200)
        self.assertIn("window.WORKSHOP_TOKEN", bootstrap)
        self.assertIn(self.server.session_token, bootstrap)
        status, _, _ = self._request(
            "/session.js", token=False, headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)

    def test_closing_one_tab_keeps_server_open_until_last_tab_closes(self):
        with (
            patch.object(self.server, "_schedule_client_expiry_locked"),
            patch.object(self.server, "_schedule_shutdown_locked") as schedule_shutdown,
        ):
            for client_id in ("tab-one", "tab-two"):
                status, response, _ = self._request(
                    "/api/client/heartbeat", method="POST", body={"client_id": client_id},
                )
                self.assertEqual(status, 200)
                self.assertEqual(response, {"ok": True})
            status, _, _ = self._request(
                "/api/client/close", method="POST", body={"client_id": "tab-one"},
            )
            self.assertEqual(status, 200)
            schedule_shutdown.assert_not_called()
            status, _, _ = self._request(
                "/api/client/close", method="POST", body={"client_id": "tab-two"},
            )
            self.assertEqual(status, 200)
            schedule_shutdown.assert_called_once()

    def test_expired_page_heartbeat_schedules_server_shutdown(self):
        with (
            patch("app.studio_server.time.monotonic", return_value=0),
            patch.object(self.server, "_schedule_client_expiry_locked"),
            patch.object(self.server, "_schedule_shutdown_locked") as schedule_shutdown,
        ):
            self.server.register_client("abandoned-tab")
            with patch("app.studio_server.time.monotonic", return_value=120):
                self.server._expire_clients()
            schedule_shutdown.assert_called_once()
            self.assertEqual(self.server._clients, {})

    def test_server_does_not_log_expected_client_disconnects(self):
        error = ConnectionResetError("client closed the connection")
        with (
            patch("app.studio_server.sys.exc_info", return_value=(ConnectionResetError, error, None)),
            redirect_stderr(io.StringIO()) as output,
        ):
            self.server.handle_error(None, None)
        self.assertEqual(output.getvalue(), "")

    def test_state_includes_default_installation_guess(self):
        guessed = Path("C:/Users/example/AppData/Local/Programs/Beyond-All-Reason")
        with patch("app.studio_server.discovery.default_root", return_value=guessed):
            status, state, _ = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["default_bar_root"], str(guessed))

        with patch("app.studio_server.discovery.default_root", return_value=None):
            status, state, _ = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(state["default_bar_root"])

    def test_latest_catalog_requires_token_and_reads_existing_scan(self):
        folder = self.server.match_service.local_data / "scans" / "catalog-test"
        folder.mkdir(parents=True)
        catalog = {"game": "Game", "units": [{"id": "armlab", "name": "Bot Lab", "builds": []}], "warnings": []}
        (folder / "units.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.assertEqual(self._request("/api/catalog/latest", token=False)[0], 401)
        status, data, _ = self._request("/api/catalog/latest")
        self.assertEqual(status, 200)
        self.assertEqual(data["catalog"], catalog)

    def test_profile_round_trips_utf8_and_newlines_and_detects_conflicts(self):
        status, result, _ = self._request("/api/profile?project=ExampleAI&profile=hard")
        self.assertEqual(status, 200)
        file_data = next(item for item in result["files"] if item["path"] == "config/settings.json")
        self.assertEqual(file_data["text"], '{\r\n  "name": "é"\r\n}\r\n')
        self.assertEqual(file_data["sha256"], hashlib.sha256(file_data["text"].encode("utf-8")).hexdigest())

        updated_text = file_data["text"].replace("é", "ø")
        save_body = {
            "project": "ExampleAI",
            "profile": "hard",
            "files": [{
                "path": file_data["path"],
                "text": updated_text,
                "sha256": file_data["sha256"],
            }],
        }
        status, _, _ = self._request("/api/save", method="POST", body=save_body)
        self.assertEqual(status, 200)
        saved_path = self.projects / "ExampleAI" / "profiles" / "hard" / "config" / "settings.json"
        self.assertEqual(saved_path.read_bytes(), updated_text.encode("utf-8"))

        save_body["files"][0]["text"] = "stale"
        status, error, _ = self._request("/api/save", method="POST", body=save_body)
        self.assertEqual(status, 400)
        self.assertIn("changed outside", error["error"])
        self.assertEqual(saved_path.read_bytes(), updated_text.encode("utf-8"))

    def test_save_rejects_traversal_and_duplicate_paths_without_changes(self):
        profile = self.projects / "ExampleAI" / "profiles" / "hard"
        target = profile / "config" / "settings.json"
        original = target.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        base = {"project": "ExampleAI", "profile": "hard"}
        traversal = dict(base, files=[{"path": "../outside", "text": "x", "sha256": digest}])
        status, _, _ = self._request("/api/save", method="POST", body=traversal)
        self.assertEqual(status, 400)
        duplicate = dict(base, files=[
            {"path": "config/settings.json", "text": "x", "sha256": digest},
            {"path": "config/settings.json", "text": "y", "sha256": digest},
        ])
        status, _, _ = self._request("/api/save", method="POST", body=duplicate)
        self.assertEqual(status, 400)
        self.assertEqual(target.read_bytes(), original)

    @unittest.skipUnless(shutil.which("git"), "Git is required for project history")
    def test_clone_project_copies_all_profiles_and_updates_only_the_copy(self):
        source = self.projects / "ExampleAI"
        (source / "profiles" / "hard" / "script" / "nested").mkdir()
        (source / "profiles" / "hard" / "script" / "nested" / "custom.as").write_text(
            "independent", encoding="utf-8"
        )
        clone_source = source / "profiles" / "hard"
        from app.projects import clone_profile

        clone_profile(source, "Fast", "hard")
        self.assertEqual(self._request("/api/state")[0], 200)
        original_log = self._request("/api/history", method="POST", body={"project": "ExampleAI"})[1]["checkpoints"]
        status, result, _ = self._request(
            "/api/clone-project",
            method="POST",
            body={"project": "ExampleAI", "name": "Copied AI"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result, {"project": "CopiedAI", "name": "Copied AI"})
        copied = self.projects / "CopiedAI"
        self.assertTrue((copied / ".git").is_dir())
        self.assertEqual(self._request("/api/state")[0], 200)
        copied_log = self._request("/api/history", method="POST", body={"project": "CopiedAI"})[1]["checkpoints"]
        self.assertEqual(len(copied_log), 1)
        self.assertNotEqual(copied_log[0]["id"], original_log[0]["id"])
        self.assertEqual(json.loads((copied / "project.json").read_text())["short_name"], "CopiedAI")
        self.assertEqual(json.loads((copied / "project.json").read_text())["name"], "Copied AI")
        self.assertEqual(
            {path.name for path in (copied / "profiles").iterdir()},
            {path.name for path in (source / "profiles").iterdir()},
        )
        copied_file = copied / "profiles" / "hard" / "script" / "nested" / "custom.as"
        self.assertEqual(copied_file.read_text(encoding="utf-8"), "independent")
        copied_file.write_text("changed", encoding="utf-8")
        self.assertEqual(
            (clone_source / "script" / "nested" / "custom.as").read_text(encoding="utf-8"),
            "independent",
        )
        status, error, _ = self._request(
            "/api/clone-project",
            method="POST",
            body={"project": "ExampleAI", "name": "Copied AI"},
        )
        self.assertEqual(status, 400)
        self.assertIn("already exists", error["error"])

    @unittest.skipUnless(shutil.which("git"), "Git is required for project history")
    def test_project_history_initializes_checkpoints_and_refuses_dirty_restore(self):
        status, state, _ = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(state["projects"][0]["history_error"])
        status, history, _ = self._request("/api/history", method="POST", body={"project": "ExampleAI"})
        self.assertEqual(status, 200)
        baseline = history["checkpoints"][0]["id"]
        target = self.projects / "ExampleAI" / "profiles" / "hard" / "config" / "settings.json"
        target.write_text('{"name":"new"}', encoding="utf-8")
        status, history, _ = self._request("/api/history/checkpoint", method="POST",
                                           body={"project": "ExampleAI", "description": "New settings"})
        self.assertEqual(status, 200)
        self.assertEqual(history["checkpoints"][0]["description"], "New settings")
        target.write_text("external edit", encoding="utf-8")
        body = {"project": "ExampleAI", "commit": baseline, "confirm": True}
        status, _, _ = self._request("/api/history/restore", method="POST",
                                     body={"project": "ExampleAI", "commit": baseline})
        self.assertEqual(status, 400)
        status, error, _ = self._request("/api/history/restore", method="POST", body=body)
        self.assertEqual(status, 400)
        self.assertIn("uncheckpointed", error["error"])
        self.assertEqual(target.read_text(encoding="utf-8"), "external edit")
        target.write_text('{"name":"new"}', encoding="utf-8")
        self.assertEqual(self._request("/api/history/restore", method="POST", body=body)[0], 200)
        self.assertIn('"name": "é"', target.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("git"), "Git is required for project history")
    def test_unmanaged_git_is_reported_without_blocking_profile(self):
        project = self.projects / "ExampleAI"
        subprocess.run(["git", "init", "--quiet", str(project)], check=True)
        status, state, _ = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("not managed", state["projects"][0]["history_error"])
        self.assertEqual(self._request("/api/profile?project=ExampleAI&profile=hard")[0], 200)
        self.assertFalse((project / ".git" / "bar-ai-studio-history").exists())

    def test_match_service_status_scan_and_cancel_errors(self):
        status, state, _ = self._request("/api/match/status")
        self.assertEqual(status, 200)
        self.assertEqual(state["state"], "idle")
        status, error, _ = self._request("/api/match/cancel", method="POST")
        self.assertEqual(status, 400)
        self.assertIn("No scan or match is running", error["error"])
        status, error, _ = self._request(
            "/api/match/start", method="POST",
            body={"bar_root": "unused"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Missing required", error["error"])

        status, started, _ = self._request(
            "/api/match/scan", method="POST",
            body={"bar_root": str(self.projects / "missing-bar-root"), "engine": "missing"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["operation"], "scan")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, state, _ = self._request("/api/match/status")
            if state["state"] == "failed":
                break
            time.sleep(0.02)
        self.assertEqual(state["state"], "failed")
        self.assertIn("BAR data directory not found", state["error"])

    def test_match_cache_endpoint_requires_token_and_returns_saved_content(self):
        content = {"games": ["Game"]}
        with patch.object(self.server.match_service, "cached_content", return_value=content) as cached:
            body = {"bar_root": "C:\\BAR", "engine": "engine"}
            self.assertEqual(self._request("/api/match/cache", method="POST", body=body, token=False)[0], 401)
            status, result, _ = self._request("/api/match/cache", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"content": content})
        cached.assert_called_once_with("C:\\BAR", "engine")

    def test_install_prompts_are_confirmed_one_at_a_time_before_changes(self):
        install_temp = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(install_temp.cleanup)
        bar_root = Path(install_temp.name)
        data = bar_root / "data"
        engine = "engine1"
        stock = data / "engine" / engine / "AI" / "Skirmish" / "BARb" / "stable"
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
        (stock / "SkirmishAI.dll").write_bytes(b"stock")
        (data / "engine" / engine / "spring-headless.exe").write_bytes(b"engine")
        (data / "launcher_cfg.json").write_text('{"config":"setup1"}', encoding="utf-8")
        (data / "config.json").write_text(json.dumps({
            "setups": [{"package": {"id": "setup1"}, "launch": {"engine": engine}}],
        }), encoding="utf-8")
        lobby = data / "LuaMenu" / "configs" / "gameConfig" / "byar" / "aiSimpleName.lua"
        lobby.parent.mkdir(parents=True)
        lobby.write_text(
            "local subnameMap = { ['Existing 1'] = 'Existing' }\n"
            "local function GetAiSimpleName(name)\n    return subnameMap[name]\nend\n"
            "local simpleAiOrder = { ['Existing 1'] = 7 }\n"
            "local aiTooltip = { ['Existing 1'] = 'Existing' }\n"
            "return { GetAiSimpleName = GetAiSimpleName, simpleAiOrder = simpleAiOrder, "
            "aiTooltip = aiTooltip }\n",
            encoding="utf-8",
        )
        old_lobby = lobby.read_text(encoding="utf-8")
        target = data / "engine" / engine / "AI" / "Skirmish" / "ExampleAI" / "1"
        target.mkdir(parents=True)
        (target / "important.txt").write_text("keep until confirmed", encoding="utf-8")

        first_prompt = (
            "Extend the existing shared lobby AI-name file without replacing its entries?\n\n"
            + str(lobby)
        )
        second_prompt = (
            "Replace the existing unmanaged AI files?\n\n"
            + str(target)
            + "\n\nThe replaced files will not be restored on removal."
        )
        body = {"project": "ExampleAI", "bar_root": str(bar_root), "engine": engine, "confirmations": []}
        status, response, _ = self._request("/api/install", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(response, {"confirmation": first_prompt})
        self.assertEqual(lobby.read_text(encoding="utf-8"), old_lobby)
        self.assertEqual((target / "important.txt").read_text(encoding="utf-8"), "keep until confirmed")

        body["confirmations"] = [first_prompt]
        status, response, _ = self._request("/api/install", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(response, {"confirmation": second_prompt})
        self.assertEqual(lobby.read_text(encoding="utf-8"), old_lobby)
        self.assertEqual((target / "important.txt").read_text(encoding="utf-8"), "keep until confirmed")

        body["confirmations"] = [first_prompt, second_prompt]
        status, response, _ = self._request("/api/install", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(response["engine"], engine)
        self.assertTrue((target / "AIInfo.lua").is_file())
        self.assertIn("Existing 1", lobby.read_text(encoding="utf-8"))
        self.assertIn("ExampleAI 1", lobby.read_text(encoding="utf-8"))

        body["confirmations"] = []
        status, response, _ = self._request("/api/install", method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertFalse(response["changed"])

        body["confirm"] = True
        status, error, _ = self._request("/api/install", method="POST", body=body)
        self.assertEqual(status, 400)
        self.assertIn("confirm boolean is no longer supported", error["error"])

    def test_remove_returns_confirmation_then_removes_selected_project(self):
        bar_root = self.projects / "bar-root"
        data = bar_root / "data"
        target = data / "engine" / "engine1" / "AI" / "Skirmish" / "ExampleAI" / "1"
        prompt = "Installed AI files have been modified. Remove them anyway?\n\n" + str(target)
        found = SimpleNamespace(data=data, engines=("engine1",))

        def remove_with_confirmation(project, selected_data, engine, confirm):
            self.assertEqual(project, self.projects / "ExampleAI")
            self.assertEqual(selected_data, data)
            self.assertEqual(engine, "engine1")
            confirm(prompt)
            return target

        body = {
            "project": "ExampleAI", "bar_root": str(bar_root), "engine": "engine1",
            "confirmations": [],
        }
        with (
            patch("app.studio_server.discovery.discover", return_value=found),
            patch("app.studio_server.installation.remove", side_effect=remove_with_confirmation) as remove,
        ):
            status, response, _ = self._request("/api/remove", method="POST", body=body)
            self.assertEqual(status, 200)
            self.assertEqual(response, {"confirmation": prompt})
            remove.assert_called_once()

            body["confirmations"] = [prompt]
            status, response, _ = self._request("/api/remove", method="POST", body=body)
            self.assertEqual(status, 200)
            self.assertEqual(response, {
                "path": str(target), "project": "ExampleAI", "engine": "engine1",
            })
            self.assertEqual(remove.call_count, 2)

    def test_sources_and_create_project_use_a_fresh_exact_source_label(self):
        source_data = self.projects / "bar-data"
        found = SimpleNamespace(data=source_data, game_directory=source_data / "games" / "BAR.sdd")
        source = GameSource("BAR (byar:stable, 12345678)", "BAR", source_data, "1" * 32)
        stock = self.projects / "extracted-stock"
        created = self.projects / "FreshAI"
        with (
            patch("app.studio_server.discovery.discover", return_value=found) as discover,
            patch("app.studio_server.game_sources.list_game_sources", return_value=([source], source)) as listed,
            patch("app.studio_server.game_sources.stock_directory", return_value=nullcontext(stock)) as extracted,
            patch("app.studio_server.projects.create_project", return_value=created) as create,
            patch("app.studio_server.StudioRequestHandler._initialize_history", return_value=None),
        ):
            status, response, _ = self._request(
                "/api/sources", method="POST", body={"bar_root": "C:/BAR"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(response, {
                "sources": [{"label": source.label, "game_name": "BAR"}],
                "suggested": source.label,
            })
            status, response, _ = self._request(
                "/api/create-project", method="POST",
                body={"bar_root": "C:/BAR", "source": source.label, "name": "Fresh AI"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(response, {"project": "FreshAI", "name": "Fresh AI"})
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(listed.call_count, 2)
            extracted.assert_called_once_with(source, self.projects)
            create.assert_called_once_with(
                self.projects, "Fresh AI", stock, source_label=source.label,
            )

        with (
            patch("app.studio_server.discovery.discover", return_value=found),
            patch("app.studio_server.game_sources.list_game_sources", return_value=([], None)),
            patch("app.studio_server.projects.create_project") as create,
        ):
            status, error, _ = self._request(
                "/api/create-project", method="POST",
                body={"bar_root": "C:/BAR", "source": source.label, "name": "Fresh AI"},
            )
            self.assertEqual(status, 400)
            self.assertIn("source is no longer available", error["error"])
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
