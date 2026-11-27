from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.studio_mcp import StudioClient, StudioError
from app.studio_server import StudioRequestHandler, StudioServer


class StudioMcpTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.server = StudioServer(("127.0.0.1", 0), projects_root=root / "projects",
                                   settings_path=root / "settings.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.client = StudioClient(self.server.server_port)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def get_bootstrap(self, **headers):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", "/mcp/session", headers=headers)
            with connection.getresponse() as response:
                return response.status, response.headers, response.read()
        finally:
            connection.close()

    def test_bootstrap_returns_versioned_non_cacheable_json(self):
        status, headers, body = self.get_bootstrap()
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(body), {"version": 1, "token": self.server.session_token})

    def test_bootstrap_respects_the_loopback_origin_boundary(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://example.test"},
                        {"Host": "example.test"}):
            with self.subTest(headers=headers):
                status, _, body = self.get_bootstrap(**headers)
                self.assertEqual(status, 403)
                self.assertNotIn(self.server.session_token.encode("ascii"), body)

    def test_bridge_uses_json_without_loading_the_browser_script(self):
        handle_get = StudioRequestHandler.do_GET

        def get_without_browser_script(handler):
            if handler.path == "/session.js":
                return handler._error(404, "Not found.")
            return handle_get(handler)

        with patch.object(StudioRequestHandler, "do_GET", get_without_browser_script):
            state = self.client.request("/api/match/status")
        self.assertEqual(state["state"], "idle")

    def test_malformed_bootstrap_asks_to_restart_studio(self):
        handle_get = StudioRequestHandler.do_GET

        def get_with_incompatible_bootstrap(handler):
            if handler.path == "/mcp/session":
                return handler._json(200, {"version": 2, "token": self.server.session_token})
            return handle_get(handler)

        with patch.object(StudioRequestHandler, "do_GET", get_with_incompatible_bootstrap):
            with self.assertRaisesRegex(StudioError, "Restart Studio") as error:
                self.client.request("/api/match/status")
        self.assertNotIn(self.server.session_token, str(error.exception))

    def test_a_mutation_with_an_unreadable_response_has_an_unknown_outcome(self):
        attempts = []

        def fail_after_submission(handler):
            attempts.append(handler.path)
            handler._send(200, b"invalid JSON", "application/json")

        with patch.object(StudioRequestHandler, "do_POST", fail_after_submission):
            with self.assertRaisesRegex(StudioError, "Check the operation or workspace state before retrying"):
                self.client.request("/api/save", {})
        self.assertEqual(attempts, ["/api/save"])

    def test_mutation_preserves_definite_validation_errors(self):
        with self.assertRaisesRegex(StudioError, "Missing required request fields"):
            self.client.request("/api/match/start", {})

    def test_idle_shutdown_waits_for_an_admitted_http_request(self):
        preparing = threading.Event()
        release = threading.Event()
        response = []

        def save(handler, payload):
            preparing.set()
            release.wait(3)
            return []

        def submit():
            connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            try:
                connection.request("POST", "/api/save", body="{}", headers={
                    "X-Workshop-Token": self.server.session_token,
                })
                with connection.getresponse() as result:
                    response.append(result.status)
            finally:
                connection.close()

        with patch.object(StudioRequestHandler, "_save", save):
            request = threading.Thread(target=submit)
            request.start()
            try:
                self.assertTrue(preparing.wait(3))
                self.server._shutdown_if_idle()
                self.assertTrue(self.thread.is_alive())
            finally:
                release.set()
                request.join(timeout=3)
        self.assertEqual(response, [200])
