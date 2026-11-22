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
from app.settings import Settings, save_settings
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

    def test_experiment_endpoints_require_a_token_and_pass_validated_requests(self):
        start_path = "/api/experiment/start"
        spec = {"bar_root": "bar", "engine": "engine", "game": "Game", "maps": ["Map"], "minutes": 1,
                "game_limit": 2, "seconds_limit": 60, "mode": "tournament", "players": []}
        for path, method, body in (
            (start_path, "POST", spec),
            ("/api/experiment/history", "GET", None),
            ("/api/experiment/pause", "POST", {"paused": True}),
            ("/api/experiment/promote", "POST", {"id": "e", "candidate_id": "v1", "name": "Winner"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self._request(path, method=method, body=body, token=False)[0], 401)
        service = self.server.match_service
        with patch.object(service, "start_experiment", return_value={"operation": "experiment"}) as start:
            self.assertEqual(self._request(start_path, method="POST", body={})[0], 400)
            self.assertEqual(self._request(start_path, method="POST", body=spec)[1]["operation"], "experiment")
            start.assert_called_once_with("bar", "engine", "Game", ["Map"], 1, 2, 60, "tournament", [], sweep=None)
        self.assertEqual(self._request("/api/experiment/history")[1], {"experiments": []})
        self.assertEqual(self._request("/api/experiment/pause", method="POST", body={"id": "e", "paused": 1})[0], 400)
        self.assertEqual(self._request("/api/experiment/pause", method="POST", body={"paused": True})[0], 400)
        with patch.object(service, "pause_experiment", return_value={}) as pause:
            self.assertEqual(self._request("/api/experiment/pause", method="POST", body={"id": "e", "paused": True})[0], 200)
            pause.assert_called_once_with(True, "e")
        with patch.object(service, "cancel", return_value={}) as cancel:
            self.assertEqual(self._request("/api/match/cancel", method="POST", body={"id": "e"})[0], 200)
            cancel.assert_called_once_with("e")
        with patch.object(service, "is_busy", return_value=True):
            for path in ("/api/install", "/api/remove"):
                self.assertIn("experiment", self._request(path, method="POST", body={})[1]["error"])
        with patch.object(service, "promote_experiment", return_value={"project": "AI", "profile": "winner"}) as promote:
            self.assertEqual(self._request("/api/experiment/promote", method="POST",
                                           body={"id": "e", "candidate_id": "v1", "name": "Winner"})[0], 200)
            promote.assert_called_once_with("e", "v1", "Winner")

    def test_match_start_requires_a_boolean_replay_flag(self):
        payload = {"bar_root": "bar", "engine": "engine", "game": "Game", "map_name": "Map",
                   "players": [], "minutes": 1, "skip_smoke": True}
        with patch.object(self.server.match_service, "start", return_value={}) as start:
            self.assertEqual(self._request("/api/match/start", method="POST", body=payload)[0], 200)
            self.assertIs(start.call_args.kwargs["record_demo"], False)
            self.assertEqual(self._request("/api/match/start", method="POST",
                                           body={**payload, "record_demo": True})[0], 200)
            self.assertIs(start.call_args.kwargs["record_demo"], True)
            for invalid in (None, "false", 1, []):
                self.assertEqual(self._request("/api/match/start", method="POST",
                                               body={**payload, "record_demo": invalid})[0], 400)
            self.assertEqual(start.call_count, 2)

    def test_match_log_endpoint_pages_bounded_raw_artifacts(self):
        url = "/api/match/logs?run_id=match-logs&file="
        run = self.server.match_service.local_data / "matches" / "match-logs"
        run.mkdir(parents=True)
        (run / "infolog.txt").write_bytes(b"Skirmish AI <BARb>: Load script: main.as\n" +
                                         b"Warning: shader setup\n" * 9000)
        for name in ("stdout.txt", "result.json"):
            (run / name).write_bytes(b"{}\n")
            self.assertEqual(self._request(url + name)[1]["lines"][0]["text"], "{}")
        status, _, _ = self._request(url + "infolog.txt", token=False)
        self.assertEqual(status, 401)
        status, page, _ = self._request(url + "infolog.txt")
        self.assertEqual(status, 200)
        self.assertEqual(page["lines"][0]["category"], "ai_script")
        self.assertLessEqual(page["next_offset"], 64 * 1024)
        status, next_page, _ = self._request(url + f"infolog.txt&offset={page['next_offset']}")
        self.assertEqual(status, 200)
        self.assertEqual(next_page["lines"][0]["text"], "Warning: shader setup")
        self.assertEqual(next_page["lines"][0]["category"], "engine_warning")
        raw = "\x00" * 32767 + "é\n"
        (run / "stdout.txt").write_bytes(raw.encode("utf-8"))
        _, before, headers = self._request(url + "stdout.txt")
        self.assertLess(int(headers["Content-Length"]), 256 * 1024)
        self.assertFalse(before["ends_with_newline"])
        _, after, _ = self._request(url + f"stdout.txt&offset={before['next_offset']}")
        self.assertEqual(before["lines"][0]["text"] + after["lines"][0]["text"], raw.rstrip("\n"))

    def test_match_log_endpoint_rejects_paths_and_links(self):
        prefix = "/api/match/logs?"
        matches = self.server.match_service.local_data / "matches"
        run = matches / "match-logs"
        run.mkdir(parents=True)
        private = matches.parent / "private.txt"
        private.write_text("private", encoding="utf-8")
        for query in (
            "run_id=..%2F..&file=infolog.txt",
            "run_id=match-logs&file=..%2Fprivate.txt",
            "run_id=match-logs&file=stdout.txt&offset=-1",
            "run_id=match-logs&file=stdout.txt&file=infolog.txt",
        ):
            with self.subTest(query=query):
                self.assertEqual(self._request(prefix + query)[0], 400)
        try:
            (run / "infolog.txt").symlink_to(private)
            (matches / "smoke-other").symlink_to(run, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Cannot create symlinks: {error}")
        for run_id in ("match-logs", "smoke-other"):
            with self.subTest(run_id=run_id):
                self.assertEqual(self._request(prefix + f"run_id={run_id}&file=infolog.txt")[0], 400)

    @unittest.skipUnless(shutil.which("git"), "Git is required for project history")
    def test_checkpoint_diff_endpoint_auth_and_revision_validation(self):
        endpoint = "/api/history/diff"
        body = {"project": "ExampleAI"}
        self.assertEqual(self._request(endpoint, method="POST", body=body, token=False)[0], 401)
        self.assertEqual(self._request(endpoint, method="POST", body=body)[0], 400)
        self._request("/api/state")
        profile = self.projects / "ExampleAI" / "profiles" / "hard"
        (profile / "script" / "init.as").write_text("updated content", encoding="utf-8")
        status, diff, _ = self._request(endpoint, method="POST", body=body)
        self.assertEqual(status, 200)
        self.assertEqual(diff["paths"], ["profiles/hard/script/init.as"])
        self.assertIn("+updated content", diff["patch"])
        self.assertEqual(self._request(endpoint, method="POST", body={**body, "commit": "HEAD"})[0], 400)

    @unittest.skipUnless(shutil.which("git"), "Git is required for project import")
    def test_project_mutations_and_zip_round_trip(self):
        def manage(action, **payload):
            return self._request("/api/project/manage", method="POST", body={
                "project": "ExampleAI", "action": action, **payload,
            })

        self.assertEqual(manage("rename_project", name="A renamed project")[0], 200)
        status, copied, _ = self._request("/api/clone", method="POST", body={
            "project": "ExampleAI", "profile": "hard", "name": "New profile",
        })
        self.assertEqual(status, 200)
        profile = copied["profile"]
        self.assertEqual(manage("rename_profile", profile=profile, name="Renamed profile")[0], 200)
        self.assertEqual(manage("delete_profile", profile="hard", confirm=True)[0], 400)
        self.assertEqual(manage("set_default", profile=profile)[0], 200)
        self.assertEqual(manage("delete_profile", profile="hard", confirm=True)[0], 200)
        self.assertEqual(manage("delete_profile", profile=profile, confirm=True)[0], 400)
        self.assertEqual(manage("delete_project")[0], 400)
        headers = {"X-Workshop-Token": self.server.session_token}
        request = urllib.request.Request(self.base + "/api/project/export?project=ExampleAI", headers=headers)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.headers.get_content_type(), "application/zip")
            archive = response.read()
        deleted, detail, _ = manage("delete_project", confirm=True)
        self.assertEqual(deleted, 200, detail)
        import_request = urllib.request.Request(
            self.base + "/api/project/import", data=archive, headers=headers, method="POST",
        )
        with urllib.request.urlopen(import_request) as response:
            self.assertEqual(json.load(response), {"project": "ExampleAI", "profile": profile})
        state = self._request("/api/state")[1]
        self.assertEqual(state["projects"][0]["name"], "A renamed project")
        self.assertEqual(state["projects"][0]["default_profile"], profile)
        checkpoints = self._request("/api/history", method="POST", body={"project": "ExampleAI"})[1]
        self.assertEqual(len(checkpoints["checkpoints"]), 1)

    def test_workspace_preferences_survive_requests_after_selection_disappears(self):
        workspace = {"project": "UnavailableAI", "profile": "missing"}
        payload = {"workspace": workspace}
        self.assertEqual(self._request("/api/preferences", method="POST", body=payload, token=False)[0], 401)
        self.assertEqual(self._request("/api/preferences", method="POST", body=payload)[0], 200)
        from app.settings import load_studio_preferences
        self.assertEqual(load_studio_preferences(self.settings.with_name("studio-preferences.json")), payload)
        status, state, _ = self._request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["preferences"], payload)
        self.assertEqual(state["projects"][0]["key"], "ExampleAI")
        self.assertEqual(self._request("/api/preferences", method="POST", body={"workspace": []})[0], 400)

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

    def test_browser_idle_shutdown_waits_for_unattended_work(self):
        with (
            patch.object(self.server.match_service, "is_busy", side_effect=[True, False]),
            patch.object(self.server, "_schedule_shutdown_locked") as retry,
            patch.object(self.server, "shutdown") as shutdown,
        ):
            self.server._shutdown_if_idle()
            retry.assert_called_once()
            shutdown.assert_not_called()
            self.server._shutdown_if_idle()
            shutdown.assert_called_once()

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

    def test_unit_icons_are_authenticated_and_use_selected_installation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Install Pillow to test unit icon conversion.")
        root = self.settings.parent / "bar"
        folder = root / "data" / "games" / "BAR.sdd" / "unitpics"
        folder.mkdir(parents=True)
        Image.new("RGB", (2, 2), "red").save(folder / "armck.dds", format="DDS")
        save_settings(self.settings, Settings(str(root), "engine"))
        self.assertEqual(self._request("/api/unit-icons", token=False)[0], 401)
        self.assertTrue(self._request("/api/unit-icons")[1]["available"])
        self.assertEqual(self._request("/api/unit-icon/armck", token=False)[0], 401)
        self.assertEqual(self._request("/api/unit-icon/missing")[0], 404)
        self.assertEqual(self._request("/api/unit-icon/armck.dds")[0], 400)
        request = urllib.request.Request(
            self.base + "/api/unit-icon/ARMCK",
            headers={"X-Workshop-Token": self.server.session_token},
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertEqual(image.getpixel((0, 0))[:3], (255, 0, 0))
        other_root = self.settings.parent / "other-bar"
        other_folder = other_root / "data" / "games" / "BAR.sdd" / "unitpics"
        other_folder.mkdir(parents=True)
        Image.new("RGB", (2, 2), "blue").save(other_folder / "armck.dds", format="DDS")
        save_settings(self.settings, Settings(str(other_root), "engine"))
        with urllib.request.urlopen(request) as response:
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertEqual(image.getpixel((0, 0))[:3], (0, 0, 255))

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


class StudioInstallSeriesHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        stock = self.root / "stock"
        (stock / "config" / "hard").mkdir(parents=True)
        (stock / "script" / "hard").mkdir(parents=True)
        (stock / "config" / "hard" / "settings.json").write_text("{}", encoding="utf-8")
        (stock / "script" / "hard" / "init.as").write_text("init", encoding="utf-8")
        (stock / "script" / "hard" / "main.as").write_text("main", encoding="utf-8")
        self.projects = self.root / "projects"
        create_project(self.projects, "Example AI", stock)
        self.settings = self.root / "settings.json"
        self.studio = self.root / "studio"
        self.studio.mkdir()
        (self.studio / "index.html").write_text("editor", encoding="utf-8")
        self.server = StudioServer(
            ("127.0.0.1", 0),
            projects_root=self.projects,
            settings_path=self.settings,
            studio_root=self.studio,
        )
        self.server.match_service = MatchService(self.root, projects_root=self.projects)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _request(self, path, *, method="GET", body=None, token=True):
        headers = {"X-Workshop-Token": self.server.session_token} if token else {}
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, json.loads(response.read().decode("utf-8"))

    def _setup_bar(self):
        bar_root = self.root / "bar"
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
        return bar_root, data, engine

    def test_install_status_endpoint_and_cache_sync_on_install(self):
        self.assertEqual(self._request("/api/install-status?project=ExampleAI", token=False)[0], 401)
        status, info = self._request("/api/install-status?project=ExampleAI")
        self.assertEqual(status, 200)
        self.assertEqual(info["status"], "not_configured")

        bar_root, data, engine = self._setup_bar()
        save_settings(self.settings, Settings(str(bar_root), engine))
        status, info = self._request("/api/install-status?project=ExampleAI")
        self.assertEqual((status, info["status"]), (200, "not_installed"))

        # Pre-seed scan cache with ExampleAI
        from app.settings import save_content_cache
        cache_path = self.server.match_service._content_cache_path(data, engine)
        save_content_cache(cache_path, data, engine, {
            "maps": [{"name": "Map", "width": 512, "height": 512}],
            "games": ["Game"],
            "ais": [{"short_name": "ExampleAI", "version": "1", "name": "Example AI",
                     "profiles": [{"key": "hard", "name": "Hard"}]}],
            "warnings": [],
        })

        # Clone a profile and install via /api/install -> updates cache without rescan
        self._request("/api/clone", method="POST", body={"project": "ExampleAI", "profile": "hard", "name": "Fast"})
        status, install_res = self._request("/api/install", method="POST", body={
            "project": "ExampleAI", "bar_root": str(bar_root), "engine": engine, "confirmations": [],
        })
        self.assertEqual(status, 200)
        self.assertTrue(install_res["changed"])
        self.assertTrue(install_res["cache_updated"])

        cached = self.server.match_service.cached_content(bar_root, engine)
        self.assertEqual([p["key"] for p in cached["ais"][0]["profiles"]], ["fast", "hard"])

        status, info = self._request("/api/install-status?project=ExampleAI")
        self.assertEqual((status, info["status"]), (200, "up_to_date"))

        # Edit saved project file -> out_of_date
        (self.projects / "ExampleAI" / "profiles" / "hard" / "script" / "main.as").write_text("edited", encoding="utf-8")
        status, info = self._request("/api/install-status?project=ExampleAI")
        self.assertEqual((status, info["status"]), (200, "out_of_date"))

        # Edit installed file -> modified_externally
        installed_main = data / "engine" / engine / "AI" / "Skirmish" / "ExampleAI" / "1" / "script" / "hard" / "main.as"
        installed_main.write_text("external", encoding="utf-8")
        status, info = self._request("/api/install-status?project=ExampleAI")
        self.assertEqual((status, info["status"]), (200, "modified_externally"))

    def test_match_series_start_and_persistent_history_endpoint(self):
        self.assertEqual(self._request("/api/match/history", token=False)[0], 401)
        self.assertEqual(self._request("/api/match/history?unknown=1")[0], 400)
        self.assertEqual(self._request("/api/match/history?limit=0")[0], 400)

        bar_root, data, engine = self._setup_bar()
        scan_content_data = {
            "maps": [{"name": "Map", "width": 512, "height": 512}, {"name": "Map2", "width": 512, "height": 512}],
            "games": ["Game"],
            "ais": [{"short_name": "ExampleAI", "version": "1", "name": "Example AI",
                     "profiles": [{"key": "hard", "name": "Hard"}]}],
            "warnings": [],
        }
        with patch("app.studio_matches.headless.scan_content", return_value=scan_content_data):
            self._request("/api/match/scan", method="POST", body={"bar_root": str(bar_root), "engine": engine})
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if self._request("/api/match/status")[1]["state"] == "completed":
                    break
                time.sleep(0.01)

        with patch("app.studio_matches.headless.run_match", return_value=("Winner: side 1.", self.root / "m")):
            status, started = self._request("/api/match/start", method="POST", body={
                "bar_root": str(bar_root),
                "engine": engine,
                "game": "Game",
                "map_name": "Map",
                "players": [
                    {"short_name": "ExampleAI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
                    {"short_name": "ExampleAI", "version": "1", "profile": "hard", "faction": "Armada", "ally": 0},
                    {"short_name": "ExampleAI", "version": "1", "profile": "hard", "faction": "Cortex", "ally": 1},
                ],
                "minutes": 15,
                "skip_smoke": True,
                "series": {"games": 2, "swap_sides": True, "maps": ["Map", "Map2"]},
            })
            self.assertEqual(status, 200)
            self.assertEqual(started["operation"], "match")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                snapshot = self._request("/api/match/status")[1]
                if snapshot["state"] not in ("running", "cancelling"):
                    break
                time.sleep(0.01)
        self.assertEqual(snapshot["state"], "completed")
        self.assertEqual(snapshot["series"]["side1_wins"], 1)
        self.assertEqual(snapshot["series"]["side2_wins"], 1)

        # Restart a new StudioServer pointing at the same root and verify persisted history
        server2 = StudioServer(
            ("127.0.0.1", 0),
            projects_root=self.projects,
            settings_path=self.settings,
            studio_root=self.studio,
        )
        self.addCleanup(server2.server_close)
        server2.match_service = MatchService(self.root, projects_root=self.projects)
        self.assertEqual(len(server2.match_service.history()), 1)

        status, hist = self._request("/api/match/history?project=ExampleAI&map=Map2")
        self.assertEqual(status, 200)
        self.assertEqual(len(hist["history"]), 1)
        self.assertEqual(hist["history"][0]["kind"], "series")


if __name__ == "__main__":
    unittest.main()
