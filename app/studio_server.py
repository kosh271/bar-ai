"""Loopback-only HTTP backend for the web-based AI editor."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from . import (
    discovery, game_sources, installation, profile_exchange, profile_text, project_archive, project_history,
    projects, unit_icons,
)
from .headless import MatchError
from .settings import (Settings, SettingsError, load_settings, load_studio_preferences, save_settings,
                       save_studio_preferences)
from .studio_matches import MatchService

APP_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = APP_ROOT / "projects"
SETTINGS_PATH = APP_ROOT / "data" / "settings.json"
STUDIO_ROOT = APP_ROOT / "web" / "studio"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
# JSON escape sequences can occupy six bytes per decoded byte. Leave room for
# each file's path, digest, and JSON fields as well as the profile envelope.
MAX_SAVE_REQUEST_BYTES = (
    6 * projects.MAX_WORKSPACE_BYTES
    + (6 * projects.MAX_WORKSPACE_PATH + 256) * projects.MAX_WORKSPACE_ENTRIES
    + 4096
)
CLIENT_HEARTBEAT_TIMEOUT = 120
CLIENT_SHUTDOWN_DELAY = 5


class StudioServer(ThreadingHTTPServer):
    """HTTP server configuration and per-process API token."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        projects_root: Path = PROJECTS_ROOT,
        settings_path: Path = SETTINGS_PATH,
        studio_root: Path = STUDIO_ROOT,
    ) -> None:
        if address[0] != "127.0.0.1":
            raise ValueError("The studio server must bind to 127.0.0.1.")
        self.projects_root = projects_root
        self.settings_path = settings_path
        self.studio_root = studio_root
        self.match_service = MatchService(APP_ROOT, projects_root=projects_root)
        self.write_lock = threading.RLock()
        self.session_token = secrets.token_urlsafe(32)
        self._client_lock = threading.Lock()
        self._clients: dict[str, float] = {}
        self._has_connected_client = False
        self._client_expiry_timer: threading.Timer | None = None
        self._shutdown_timer: threading.Timer | None = None
        super().__init__(address, StudioRequestHandler)

    def register_client(self, client_id: str) -> None:
        with self._client_lock:
            now = time.monotonic()
            self._expire_stale_clients_locked(now)
            self._clients[client_id] = now
            self._has_connected_client = True
            self._cancel_timer_locked("_shutdown_timer")
            self._schedule_client_expiry_locked()

    def close_client(self, client_id: str) -> None:
        with self._client_lock:
            self._expire_stale_clients_locked(time.monotonic())
            self._clients.pop(client_id, None)
            if self._clients:
                self._schedule_client_expiry_locked()
            else:
                self._cancel_timer_locked("_client_expiry_timer")
                if self._has_connected_client:
                    self._schedule_shutdown_locked()

    def _expire_clients(self) -> None:
        with self._client_lock:
            self._client_expiry_timer = None
            self._expire_stale_clients_locked(time.monotonic())
            if self._clients:
                self._schedule_client_expiry_locked()
            elif self._has_connected_client:
                self._schedule_shutdown_locked()

    def _shutdown_if_idle(self) -> None:
        with self._client_lock:
            self._shutdown_timer = None
            self._expire_stale_clients_locked(time.monotonic())
            if self._clients:
                self._schedule_client_expiry_locked()
                return
            if self.match_service.is_busy():
                self._schedule_shutdown_locked()
                return
        self.shutdown()

    def _expire_stale_clients_locked(self, now: float) -> None:
        expired = [
            client_id for client_id, last_seen in self._clients.items()
            if now - last_seen >= CLIENT_HEARTBEAT_TIMEOUT
        ]
        for client_id in expired:
            del self._clients[client_id]

    def _schedule_client_expiry_locked(self) -> None:
        self._cancel_timer_locked("_client_expiry_timer")
        timer = threading.Timer(CLIENT_HEARTBEAT_TIMEOUT, self._expire_clients)
        timer.daemon = True
        self._client_expiry_timer = timer
        timer.start()

    def _schedule_shutdown_locked(self) -> None:
        self._cancel_timer_locked("_shutdown_timer")
        timer = threading.Timer(CLIENT_SHUTDOWN_DELAY, self._shutdown_if_idle)
        timer.daemon = True
        self._shutdown_timer = timer
        timer.start()

    def _cancel_timer_locked(self, name: str) -> None:
        timer = getattr(self, name)
        if timer is not None:
            timer.cancel()
            setattr(self, name, None)

    def server_close(self) -> None:
        with self._client_lock:
            self._cancel_timer_locked("_client_expiry_timer")
            self._cancel_timer_locked("_shutdown_timer")
        try:
            if not self.match_service.shutdown():
                print("Studio engine workers did not finish before the shutdown deadline.", file=sys.stderr)
        finally:
            super().server_close()

    def handle_error(self, request: object, client_address: object) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class ConfirmationRequired(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class StudioRequestHandler(BaseHTTPRequestHandler):
    server: StudioServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if not self._valid_host_origin():
            return self._error(403, "Invalid request origin.")
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            if not self._authorized():
                return self._error(401, "A valid workshop token is required.")
            if parsed.path == "/api/state" and not parsed.query:
                try:
                    default_bar_root = discovery.default_root()
                    issues: list[dict[str, str]] = []
                    return self._json(200, {
                        "projects": self._state_projects(issues),
                        "project_issues": issues,
                        "settings": self._settings(),
                        "preferences": load_studio_preferences(self._preferences_path()),
                        "game_capacity": self.server.match_service.capacity(),
                        "default_bar_root": str(default_bar_root) if default_bar_root is not None else None,
                    })
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/match/status" and not parsed.query:
                try:
                    return self._json(200, self.server.match_service.status())
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/match/logs":
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True,
                                                  strict_parsing=True, max_num_fields=3)
                    if (not {"run_id", "file"} <= set(query) <= {"run_id", "file", "offset"}
                            or any(len(values) != 1 for values in query.values())):
                        raise ValueError("Provide a run ID, a log file, and an optional byte offset.")
                    offset = query.get("offset", ["0"])[0]
                    if not re.fullmatch(r"[0-9]{1,19}", offset):
                        raise ValueError("Invalid log byte offset.")
                    result = self.server.match_service.read_log(query["run_id"][0], query["file"][0], int(offset))
                    return self._json(200, result)
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/experiment/history":
                try:
                    if parsed.query:
                        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                        if set(query) == {"id"} and len(query["id"]) == 1:
                            return self._json(200, {"experiment": self.server.match_service.experiment_details(query["id"][0])})
                        offset, limit = self._page_values(query)
                        return self._json(200, self.server.match_service.experiment_history_page(offset=offset, limit=limit))
                    return self._json(200, {"experiments": self.server.match_service.experiment_history()})
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/storage":
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    offset, limit = self._page_values(query)
                    return self._json(200, self.server.match_service.storage(offset=offset, limit=limit))
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/match/history":
                try:
                    query = (
                        urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                        if parsed.query else {}
                    )
                    allowed = {"project", "profile", "map", "checkpoint", "limit", "offset"}
                    if not set(query) <= allowed or any(len(values) != 1 for values in query.values()):
                        raise ValueError("Unsupported or duplicate history query parameter.")
                    limit = 100
                    if "limit" in query:
                        raw_limit = query["limit"][0]
                        if not raw_limit.isdecimal():
                            raise ValueError("limit must be a positive integer.")
                        limit = int(raw_limit)
                    paging = {}
                    if "offset" in query:
                        paging = {"offset": self._page_values({"offset": query["offset"]})[0]}
                    result = self.server.match_service.history(
                        project=query["project"][0] or None if "project" in query else None,
                        profile=query["profile"][0] or None if "profile" in query else None,
                        map_name=query["map"][0] or None if "map" in query else None,
                        checkpoint=query["checkpoint"][0] or None if "checkpoint" in query else None,
                        limit=limit, **paging,
                    )
                    return self._json(200, result if paging else {"history": result})
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/install-status":
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(query) != {"project"} or any(len(values) != 1 for values in query.values()):
                        raise ValueError("project query value is required exactly once.")
                    return self._json(200, self._install_status(query["project"][0]))
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/catalog/latest" and not parsed.query:
                try:
                    return self._json(200, {"catalog": self.server.match_service.latest_catalog()})
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/unit-icons" and not parsed.query:
                try:
                    return self._json(200, unit_icons.icon_status(
                        load_settings(self.server.settings_path), catalog_game=self._unit_icon_catalog_game(),
                    ))
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path.startswith("/api/unit-icon/") and not parsed.query:
                try:
                    unit_id = parsed.path.removeprefix("/api/unit-icon/")
                    image = unit_icons.read_icon(
                        load_settings(self.server.settings_path), unit_id, catalog_game=self._unit_icon_catalog_game(),
                    )
                    if image is None:
                        return self._error(404, "Unit icon not found.")
                    return self._send(200, image, "image/png")
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path == "/api/profile":
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(query) != {"project", "profile"} or any(len(values) != 1 for values in query.values()):
                        raise ValueError("project and profile query values are required exactly once.")
                    with self.server.write_lock:
                        files = self._profile({
                            "project": query["project"][0],
                            "profile": query["profile"][0],
                        })
                    return self._json(200, {"files": files})
                except Exception as error:
                    return self._handle_error(error)
            if parsed.path in {"/api/project/export", "/api/profile/export"}:
                try:
                    is_profile = parsed.path == "/api/profile/export"
                    fields = ("project", "profile") if is_profile else ("project",)
                    query = self._query_fields(parsed.query, *fields)
                    with tempfile.TemporaryFile() as archive:
                        with self.server.write_lock:
                            project = self._project(query["project"])
                            if is_profile:
                                profile = projects._profile_path(project, query["profile"])
                                profile_exchange.write_archive(profile, archive)
                                filename = f"{profile.name}.profile.zip"
                            else:
                                project_archive.write(project, archive)
                                filename = f"{project.name}.zip"
                        size = archive.tell()
                        archive.seek(0)
                        self._send_headers(200, size, "application/zip", filename=filename)
                        shutil.copyfileobj(archive, self.wfile)
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as error:
                    return self._handle_error(error)
            else:
                return self._error(404, "Unknown API endpoint.")
        if parsed.query:
            return self._error(404, "Not found.")
        if parsed.path == "/session.js":
            if self.headers.get("Sec-Fetch-Site", "").casefold() == "cross-site":
                return self._error(403, "Session bootstrap is same-origin only.")
            origin = f"http://127.0.0.1:{self.server.server_port}"
            body = (
                "if (location.origin === "
                + json.dumps(origin)
                + ") window.WORKSHOP_TOKEN = "
                + json.dumps(self.server.session_token)
                + ";\n"
            ).encode("utf-8")
            return self._send(200, body, "application/javascript; charset=utf-8")
        if parsed.path in ("", "/"):
            relative = "index.html"
        else:
            relative = urllib.parse.unquote(parsed.path.lstrip("/"))
        if not relative or "\\" in relative or "\x00" in relative or _is_link(self.server.studio_root):
            return self._error(404, "Not found.")
        requested = PurePosixPath(relative)
        if requested.is_absolute() or any(part in ("", ".", "..") for part in requested.parts):
            return self._error(404, "Not found.")
        try:
            root = self.server.studio_root.resolve(strict=True)
            path = root.joinpath(*requested.parts)
            if self._path_has_link(root, path):
                return self._error(404, "Not found.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                return self._error(404, "Not found.")
            body = resolved.read_bytes()
        except (OSError, RuntimeError, ValueError):
            return self._error(404, "Not found.")
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self._send(200, body, content_type)

    def do_POST(self) -> None:
        if not self._valid_host_origin():
            return self._error(403, "Invalid request origin.")
        if not self._authorized():
            return self._error(401, "A valid workshop token is required.")
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/profile/import":
            try:
                return self._json(200, self._import_profile(parsed.query))
            except Exception as error:
                return self._handle_error(error)
        if parsed.query or parsed.path not in {
            "/api/client/heartbeat", "/api/client/close",
            "/api/save", "/api/clone", "/api/clone-project", "/api/sources",
            "/api/create-project", "/api/inspect", "/api/history", "/api/history/checkpoint", "/api/history/restore",
            "/api/history/diff",
            "/api/project/manage", "/api/project/import",
            "/api/profile/merge-preview", "/api/profile/merge",
            "/api/preferences",
            "/api/install", "/api/remove", "/api/installations/managed", "/api/settings",
            "/api/match/cache", "/api/match/scan",
            "/api/catalog/start", "/api/catalog/cancel",
            "/api/match/start", "/api/match/cancel", "/api/match/cancel-game", "/api/match/replay/copy", "/api/match/parallel",
            "/api/experiment/start", "/api/experiment/pause", "/api/experiment/promote", "/api/storage",
        }:
            return self._error(404, "Unknown API endpoint.")
        try:
            if parsed.path == "/api/project/import":
                return self._json(200, self._import_project())
            payload = self._read_json(
                allow_empty=parsed.path == "/api/match/cancel",
                limit=MAX_SAVE_REQUEST_BYTES if parsed.path == "/api/save" else MAX_REQUEST_BYTES,
            )
            if parsed.path == "/api/storage":
                if payload.get("action") == "pin" and set(payload) == {"action", "id", "pinned"}:
                    return self._json(200, self.server.match_service.pin_run(payload["id"], payload["pinned"]))
                if payload.get("action") == "cleanup" and set(payload) == {"action", "ids"}:
                    return self._json(200, self.server.match_service.cleanup_runs(payload["ids"]))
                raise ValueError("Choose a valid artifact storage action.")
            if parsed.path in {"/api/client/heartbeat", "/api/client/close"}:
                self._require_fields(payload, ("client_id",))
                client_id = self._string(payload["client_id"], "client_id")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
                    raise ValueError("client_id has an invalid format.")
                if parsed.path == "/api/client/heartbeat":
                    self.server.register_client(client_id)
                else:
                    self.server.close_client(client_id)
                return self._json(200, {"ok": True})
            if parsed.path == "/api/profile":
                return self._json(200, {"files": self._profile(payload)})
            if parsed.path == "/api/save":
                with self.server.write_lock:
                    return self._json(200, {"files": self._save(payload)})
            if parsed.path == "/api/clone":
                self._require_fields(payload, ("project", "profile", "name"))
                project_key = self._string(payload["project"], "project")
                profile_key = self._string(payload["profile"], "profile")
                name = self._string(payload["name"], "name")
                project_dir = self._project(project_key)
                with self.server.write_lock:
                    created = projects.clone_profile(project_dir, name, profile_key)
                return self._json(200, {
                    "project": project_key,
                    "profile": created.name,
                    "state": {"projects": self._state_projects(), "settings": self._settings()},
                })
            if parsed.path == "/api/clone-project":
                with self.server.write_lock:
                    return self._json(200, self._clone_project(payload))
            if parsed.path == "/api/project/manage":
                with self.server.write_lock:
                    return self._json(200, self._manage_project(payload))
            if parsed.path in {"/api/profile/merge-preview", "/api/profile/merge"}:
                try:
                    with self.server.write_lock:
                        result = self._merge_profile(payload, save=parsed.path == "/api/profile/merge")
                except profile_exchange.StalePreview as error:
                    return self._error(409, str(error))
                return self._json(200, result)
            if parsed.path == "/api/preferences":
                with self.server.write_lock:
                    save_studio_preferences(self._preferences_path(), payload)
                return self._json(200, {"ok": True})
            if parsed.path == "/api/history/diff":
                self._require_fields(payload, ("project",))
                names_only = payload.get("names_only", False)
                if type(names_only) is not bool:
                    raise ValueError("names_only must be a boolean.")
                with self.server.write_lock:
                    project = self._project(self._string(payload["project"], "project"))
                    return self._json(200, project_history.diff(
                        project,
                        base=self._string(payload["base"], "base") if "base" in payload else "",
                        commit=self._string(payload["commit"], "commit") if "commit" in payload else "",
                        names_only=names_only,
                    ))
            if parsed.path in {"/api/history", "/api/history/checkpoint", "/api/history/restore"}:
                self._require_fields(payload, ("project",))
                with self.server.write_lock:
                    project = self._project(self._string(payload["project"], "project"))
                    project_history.ensure(project)
                    if parsed.path == "/api/history/checkpoint":
                        self._require_fields(payload, ("description",))
                        project_history.checkpoint(project, self._string(payload["description"], "description"))
                    elif parsed.path == "/api/history/restore":
                        self._require_fields(payload, ("commit", "confirm"))
                        if payload["confirm"] is not True:
                            raise ValueError("Confirm the project restore before proceeding.")
                        project_history.restore(project, self._string(payload["commit"], "commit"))
                    return self._json(200, {"checkpoints": project_history.list_checkpoints(project)})
            if parsed.path == "/api/sources":
                return self._json(200, self._sources(payload))
            if parsed.path == "/api/create-project":
                with self.server.write_lock:
                    return self._json(200, self._create_project(payload))
            if parsed.path == "/api/inspect":
                return self._json(200, self._inspect(payload))
            if parsed.path == "/api/installations/managed":
                self._require_fields(payload, ("bar_root",))
                root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
                with self.server.write_lock:
                    found = discovery.discover(root)
                    entries, issues = installation.list_managed(found.data, found.engines)
                return self._json(200, {"installations": entries, "issues": issues})
            if parsed.path in {"/api/install", "/api/remove"}:
                with self.server.write_lock:
                    if self.server.match_service.is_busy("experiment"):
                        raise MatchError("Wait for the experiment to finish before changing installed AIs.")
                    if self.server.match_service.is_busy("match"):
                        raise MatchError("Wait for the test queue to finish before changing installed AIs.")
                    if self.server.match_service.is_busy("scan"):
                        raise MatchError("Wait for the installed-content scan to finish before changing installed AIs.")
                    change = self._install if parsed.path == "/api/install" else self._remove
                    return self._json(200, change(payload))
            if parsed.path == "/api/match/cache":
                self._require_fields(payload, ("bar_root", "engine"))
                return self._json(200, {"content": self.server.match_service.cached_content(
                    self._string(payload["bar_root"], "bar_root"),
                    self._string(payload["engine"], "engine"),
                )})
            if parsed.path == "/api/match/scan":
                self._require_fields(payload, ("bar_root", "engine"))
                with self.server.write_lock:
                    return self._json(200, self.server.match_service.scan(
                        self._string(payload["bar_root"], "bar_root"),
                        self._string(payload["engine"], "engine"),
                    ))
            if parsed.path == "/api/catalog/start":
                self._require_fields(payload, ("bar_root", "engine", "game"))
                return self._json(200, self.server.match_service.catalog(
                    self._string(payload["bar_root"], "bar_root"),
                    self._string(payload["engine"], "engine"),
                    self._string(payload["game"], "game"),
                ))
            if parsed.path == "/api/catalog/cancel":
                self._require_fields(payload, ("id",))
                return self._json(200, self.server.match_service.cancel(self._string(payload["id"], "id")))
            if parsed.path == "/api/match/start":
                self._require_fields(payload, (
                    "bar_root", "engine", "game", "map_name", "players", "minutes", "skip_smoke",
                ))
                record_demo = payload.get("record_demo", False)
                if type(record_demo) is not bool:
                    raise ValueError("record_demo must be a boolean.")
                extra_units = payload.get("extra_units", False)
                if type(extra_units) is not bool:
                    raise ValueError("extra_units must be a boolean.")
                with self.server.write_lock:
                    return self._json(200, self.server.match_service.start(
                        payload["bar_root"],
                        payload["engine"],
                        payload["game"],
                        payload["map_name"],
                        payload["players"],
                        payload["minutes"],
                        payload["skip_smoke"],
                        series=payload.get("series"), record_demo=record_demo, extra_units=extra_units,
                        parallel_games=payload.get("parallel_games"), start_options=payload.get("start_options"),
                    ))
            if parsed.path == "/api/match/cancel-game":
                self._require_fields(payload, ("id", "game_index"))
                index = payload["game_index"]
                if type(index) is not int or index < 1:
                    raise ValueError("game_index must be a positive integer.")
                return self._json(200, self.server.match_service.cancel_game(
                    self._string(payload["id"], "id"), index,
                ))
            if parsed.path == "/api/match/parallel":
                self._require_fields(payload, ("parallel_games",))
                return self._json(200, self.server.match_service.set_parallel_games(payload["parallel_games"]))
            if parsed.path == "/api/match/replay/copy":
                self._require_fields(payload, ("run_id", "path"))
                selected = load_settings(self.server.settings_path)
                if selected is None:
                    raise ValueError("Choose and save a BAR installation before copying a replay.")
                return self._json(200, self.server.match_service.copy_replay(
                    self._string(payload["run_id"], "run_id"), self._string(payload["path"], "path"),
                    selected.bar_root, selected.engine,
                ))
            if parsed.path == "/api/experiment/start":
                fields = ("bar_root", "engine", "game", "maps", "minutes", "game_limit", "seconds_limit", "mode", "players")
                self._require_fields(payload, fields)
                with self.server.write_lock:
                    return self._json(200, self.server.match_service.start_experiment(
                        *(payload[field] for field in fields), sweep=payload.get("sweep"),
                        parallel_games=payload.get("parallel_games"), start_options=payload.get("start_options"),
                        **({"seed": payload["seed"]} if "seed" in payload else {}),
                    ))
            if parsed.path == "/api/experiment/pause":
                self._require_fields(payload, ("id", "paused"))
                return self._json(200, self.server.match_service.pause_experiment(
                    payload["paused"], self._string(payload["id"], "id"),
                ))
            if parsed.path == "/api/experiment/promote":
                self._require_fields(payload, ("id", "candidate_id", "name"))
                with self.server.write_lock:
                    return self._json(200, self.server.match_service.promote_experiment(
                        self._string(payload["id"], "id"), self._string(payload["candidate_id"], "candidate_id"),
                        self._string(payload["name"], "name"),
                    ))
            if parsed.path == "/api/match/cancel":
                expected = self._string(payload["id"], "id") if "id" in payload else None
                return self._json(200, self.server.match_service.cancel(expected))
            with self.server.write_lock:
                return self._json(200, self._save_settings(payload))
        except Exception as error:
            return self._handle_error(error)

    def do_HEAD(self) -> None:
        self._error(405, "Method not allowed.")

    def do_OPTIONS(self) -> None:
        self._error(405, "Method not allowed.")

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self._send_headers(status, len(body), content_type)
        self.wfile.write(body)

    def _send_headers(self, status: int, length: int, content_type: str, *, filename: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _handle_error(self, error: Exception) -> None:
        if isinstance(error, (projects.ProjectError, project_history.HistoryError, game_sources.GameSourceError, installation.InstallationError,
                              discovery.DiscoveryError, SettingsError, MatchError,
                              OSError, ValueError)):
            self._error(400, str(error)[:1000])
        elif isinstance(error, (KeyError, TypeError)):
            self._error(400, "Invalid request.")
        else:
            traceback.print_exception(type(error), error, error.__traceback__)
            self._error(500, "The request could not be completed.")

    def _valid_host_origin(self) -> bool:
        expected_host = f"127.0.0.1:{self.server.server_port}"
        host = self.headers.get("Host", "")
        if host.casefold() != expected_host.casefold():
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            parsed = urllib.parse.urlsplit(origin)
            return (
                parsed.scheme == "http"
                and parsed.netloc.casefold() == expected_host.casefold()
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            return False

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Workshop-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.server.session_token)

    def _read_json(self, *, allow_empty: bool = False, limit: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
        length = self._body_length(limit, allow_empty=allow_empty)
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body.")
        if allow_empty and length == 0:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid UTF-8 JSON.") from error
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _body_length(self, limit: int, *, allow_empty: bool = False) -> int:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ValueError("Transfer-encoded requests are not supported.")
        lengths = self.headers.get_all("Content-Length", [])
        if allow_empty and not lengths:
            return 0
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
            self.close_connection = True
            raise ValueError("A valid Content-Length header is required.")
        length = int(lengths[0])
        if length > limit:
            self.close_connection = True
            raise ValueError("Request body is too large.")
        return length

    @staticmethod
    def _require_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> None:
        if any(field not in payload for field in fields):
            raise ValueError("Missing required request fields.")

    @staticmethod
    def _string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"{label} must be a non-empty string.")
        return value

    @staticmethod
    def _initialize_history(project: Path) -> str | None:
        try:
            project_history.ensure(project)
        except (project_history.HistoryError, OSError) as error:
            return str(error)
        return None

    def _state_projects(self, issues: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        result = []
        if issues is None:
            issues = []
        root = self.server.projects_root
        with self.server.write_lock:
            for key, name in projects.list_projects(root, issues=issues):
                project = root / key
                try:
                    default = projects.project_metadata(project)["default_profile"]
                    profiles = projects.list_profiles(project, issues=issues)
                    if default not in dict(profiles):
                        issues.append({"project": key, "profile": default, "message": "Default profile is unavailable."})
                except (projects.ProjectError, OSError) as error:
                    issues.append({"project": key, "message": str(error)})
                    continue
                result.append({
                    "key": key,
                    "name": name,
                    "default_profile": default,
                    "history_error": self._initialize_history(project),
                    "profiles": [{"key": profile_key, "name": profile_name}
                                 for profile_key, profile_name in profiles],
                })
        return result

    def _settings(self) -> dict[str, str] | None:
        configured = load_settings(self.server.settings_path)
        return None if configured is None else {
            "bar_root": configured.bar_root,
            "engine": configured.engine,
        }

    def _preferences_path(self) -> Path:
        return self.server.settings_path.with_name("studio-preferences.json")

    def _clone_project(self, payload: dict[str, Any]) -> dict[str, str]:
        self._require_fields(payload, ("project", "name"))
        source = self._project(self._string(payload["project"], "project"))
        name = self._string(payload["name"], "name").strip()
        key = projects._key(name, project=True)
        projects.validate_deploy_name(key)
        root = self.server.projects_root
        if _is_link(root):
            raise ValueError("Projects directory cannot be a link.")
        root.mkdir(parents=True, exist_ok=True)
        if any(child.name.casefold() == key.casefold() for child in root.iterdir()):
            raise projects.ProjectError(f"{key!r} already exists in {root}.")
        self._check_project_tree(source)
        try:
            with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary:
                staged = Path(temporary) / key
                shutil.copytree(source, staged, symlinks=True,
                                ignore=lambda directory, names: {name for name in names if
                                    Path(directory) == source and name.casefold() == ".git"})
                self._check_project_tree(staged)
                metadata_path = staged / "project.json"
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise projects.ProjectError(f"Cannot read project metadata: {error}") from error
                if not isinstance(metadata, dict) or any(
                    not isinstance(metadata.get(field), str) or not metadata[field]
                    for field in ("name", "short_name", "default_profile")
                ):
                    raise projects.ProjectError(f"Invalid metadata: {metadata_path}")
                metadata["name"] = name
                metadata["short_name"] = key
                metadata_path.write_text(
                    json.dumps(metadata, indent=2) + "\n",
                    encoding="utf-8",
                    newline="",
                )
                projects.list_profiles(staged)
                if any(child.name.casefold() == key.casefold() for child in root.iterdir()):
                    raise projects.ProjectError(f"{key!r} already exists in {root}.")
                staged.rename(root / key)
        except OSError as error:
            raise projects.ProjectError(f"Cannot clone project: {error}") from error
        result = {"project": key, "name": name}
        error = self._initialize_history(root / key)
        if error:
            result["history_error"] = error
        return result

    def _sources(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, ("bar_root",))
        bar_root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
        found = discovery.discover(bar_root)
        sources, suggested = game_sources.list_game_sources(found.data, found.game_directory)
        return {
            "sources": [
                {"label": source.label, "game_name": source.game_name}
                for source in sources
            ],
            "suggested": suggested.label if suggested is not None else None,
        }

    def _stock_source(self, payload: dict[str, Any]) -> game_sources.GameSource:
        bar_root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
        source_label = self._string(payload["source"], "source")
        found = discovery.discover(bar_root)
        sources = game_sources.list_game_sources(found.data, found.game_directory)[0]
        matches = [source for source in sources if source.label == source_label]
        if len(matches) != 1:
            raise game_sources.GameSourceError("Selected game source is no longer available.")
        return matches[0]

    def _create_project(self, payload: dict[str, Any]) -> dict[str, str]:
        self._require_fields(payload, ("bar_root", "source", "name"))
        name = self._string(payload["name"], "name")
        source = self._stock_source(payload)
        with game_sources.stock_directory(source, self.server.projects_root) as stock:
            created = projects.create_project(
                self.server.projects_root,
                name,
                stock,
                source_label=source.label,
            )
        result = {"project": created.name, "name": name.strip()}
        error = self._initialize_history(created)
        if error:
            result["history_error"] = error
        return result

    def _manage_project(self, payload: dict[str, Any]) -> dict[str, str]:
        self._require_fields(payload, ("project", "action"))
        project = self._project(self._string(payload["project"], "project"))
        action = self._string(payload["action"], "action")
        profile = ""
        if action in ("delete_project", "delete_profile") and payload.get("confirm") is not True:
            raise ValueError("Confirm the deletion before proceeding.")
        if action == "delete_project":
            self._check_project_tree(project)
            projects.delete_project(self.server.projects_root, project.name)
            return {"project": "", "profile": ""}
        if action == "rename_project":
            projects.rename_project(project, self._string(payload.get("name"), "name"))
            return {"project": project.name}
        elif action == "create_profile":
            self._require_fields(payload, ("bar_root", "source", "name"))
            source = self._stock_source(payload)
            with game_sources.stock_directory(source, self.server.projects_root) as stock:
                profile = projects.create_profile_from_hard(
                    project, self._string(payload["name"], "name"), stock, source_label=source.label,
                ).name
        elif action in ("rename_profile", "set_default", "delete_profile"):
            profile = self._string(payload.get("profile"), "profile")
            if action == "rename_profile":
                projects.rename_profile(project, profile, self._string(payload.get("name"), "name"))
            elif action == "set_default":
                projects.set_default_profile(project, profile)
            else:
                projects.delete_profile(project, profile)
                profile = ""
        else:
            raise ValueError("Unknown project management action.")
        return {"project": project.name, "profile": profile}

    @staticmethod
    def _page_values(query: dict[str, list[str]]) -> tuple[int, int]:
        if (not set(query) <= {"offset", "limit"}
                or any(len(values) != 1 or not re.fullmatch(r"[0-9]{1,10}", values[0]) for values in query.values())):
            raise ValueError("Provide a nonnegative offset and a positive page limit.")
        return int(query.get("offset", ["0"])[0]), int(query.get("limit", ["25"])[0])

    @staticmethod
    def _query_fields(query: str, *fields: str) -> dict[str, str]:
        values = urllib.parse.parse_qs(query, strict_parsing=True)
        if set(values) != set(fields) or any(len(value) != 1 for value in values.values()):
            raise ValueError(f"Supply one value for each parameter: {', '.join(fields)}.")
        return {field: values[field][0] for field in fields}

    def _read_archive(self, archive) -> None:
        remaining = self._body_length(project_archive.MAX_ARCHIVE_BYTES)
        while remaining:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("Incomplete ZIP upload.")
            archive.write(chunk)
            remaining -= len(chunk)
        archive.seek(0)

    def _import_project(self) -> dict[str, str]:
        with tempfile.TemporaryFile() as archive:
            self._read_archive(archive)
            with self.server.write_lock:
                project = project_archive.load(self.server.projects_root, archive)
                return {"project": project.name, "profile": projects.project_metadata(project)["default_profile"]}

    def _import_profile(self, query: str) -> dict[str, str]:
        fields = self._query_fields(query, "project", "name")
        with tempfile.TemporaryFile() as archive:
            self._read_archive(archive)
            with self.server.write_lock:
                project = self._project(fields["project"])
                profile = profile_exchange.load_archive(project, fields["name"], archive)
        return {"project": project.name, "profile": profile.name}

    def _merge_profile(self, payload: dict[str, Any], *, save: bool) -> dict[str, Any]:
        required = ("project", "profile", "other_project", "other_profile", "prefer")
        if save:
            required += ("name", "revision")
        self._require_fields(payload, required)
        fields = {field: self._string(payload[field], field) for field in required}
        if fields["prefer"] not in {"selected", "other"}:
            raise ValueError("Choose which profile to prefer on conflict.")
        if save and not re_full_sha256(fields["revision"]):
            raise ValueError("A valid merge preview revision is required.")
        stale = "A source profile changed. Preview the merge again before creating the profile."
        try:
            project = self._project(fields["project"])
            selected = projects._profile_path(project, fields["profile"])
            other = projects._profile_path(self._project(fields["other_project"]), fields["other_profile"])
            if selected == other:
                raise ValueError("Choose two different profiles to merge.")
            sources = (selected, other) if fields["prefer"] == "selected" else (other, selected)
            preferred_files, other_files = (profile_exchange.read(source)[1] for source in sources)
        except (projects.ProjectError, ValueError, OSError) as error:
            if save:
                raise profile_exchange.StalePreview(stale) from error
            raise
        revision = profile_exchange.source_revision(preferred_files, other_files)
        if save and fields["revision"] != revision:
            raise profile_exchange.StalePreview(stale)
        files, report = profile_exchange.merge(preferred_files, other_files)
        if not save:
            return {"files": report, "revision": revision,
                    "candidate": [{"path": path, "text": data.decode("utf-8")} for path, data in files.items()]}
        created = profile_exchange.save_merge(project, fields["name"], files)
        return {"project": project.name, "profile": created.name}

    @staticmethod
    def _check_project_tree(root: Path) -> None:
        if _is_link(root) or not root.is_dir():
            raise projects.ProjectError(f"Project is not a regular directory: {root}")
        try:
            project_history._check_tree(root)
        except project_history.HistoryError as error:
            raise projects.ProjectError(str(error)) from error

    def _project(self, key: str) -> Path:
        if _is_link(self.server.projects_root):
            raise ValueError("Projects directory cannot be a link.")
        return projects.project_path(self.server.projects_root.resolve(), key)

    def _profile(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        self._require_fields(payload, ("project", "profile"))
        project_dir = self._project(self._string(payload["project"], "project"))
        profile_key = self._string(payload["profile"], "profile")
        profile = projects._profile_path(project_dir, profile_key)
        result = []
        files = projects.profile_files(profile)
        total = (profile / "profile.json").stat().st_size
        count = len(files) + 1
        projects.check_workspace_size(total, total, count)
        for relative in files:
            path = profile / relative
            if self._path_has_link(profile, path):
                raise ValueError("Profile files cannot be links.")
            size = path.stat().st_size
            projects.check_workspace_size(size, total + size, count)
            raw = profile_text.read(path)
            total += len(raw)
            projects.check_workspace_size(len(raw), total, count)
            text = raw.decode("utf-8")
            result.append({
                "path": relative.as_posix(),
                "text": text,
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        return result

    def _save(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        self._require_fields(payload, ("project", "profile", "files"))
        project_dir = self._project(self._string(payload["project"], "project"))
        profile_key = self._string(payload["profile"], "profile")
        profile = projects._profile_path(project_dir, profile_key)
        submitted = payload["files"]
        if not isinstance(submitted, list) or not submitted or len(submitted) > projects.MAX_WORKSPACE_ENTRIES:
            raise ValueError(f"files must be a non-empty list of at most {projects.MAX_WORKSPACE_ENTRIES} files.")

        known = {path.as_posix(): profile / path for path in projects.profile_files(profile)}
        edits: list[tuple[Path, str, bytes]] = []
        seen: set[str] = set()
        edited_size = (profile / "profile.json").stat().st_size
        count = len(known) + 1
        projects.check_workspace_size(edited_size, edited_size, count)
        for entry in submitted:
            if not isinstance(entry, dict) or not all(field in entry for field in ("path", "text", "sha256")):
                raise ValueError("Each file must include path, text, and sha256.")
            relative_name = self._string(entry["path"], "file path")
            if relative_name not in known:
                raise ValueError("Only existing profile files may be saved.")
            projects.workspace_path_parts(relative_name)
            identity = relative_name.casefold()
            if identity in seen:
                raise ValueError("Duplicate file paths are not allowed.")
            seen.add(identity)
            text = entry["text"]
            expected_hash = entry["sha256"]
            if not isinstance(text, str) or not isinstance(expected_hash, str) or not re_full_sha256(expected_hash):
                raise ValueError("File text and a valid sha256 value are required.")
            try:
                new_bytes = text.encode("utf-8", errors="strict")
            except UnicodeError as error:
                raise ValueError(f"File is not valid UTF-8 text: {relative_name}") from error
            edited_size += len(new_bytes)
            projects.check_workspace_size(len(new_bytes), edited_size, count)
            edits.append((known[relative_name], expected_hash.lower(), new_bytes))
        edited_paths = {path for path, _revision, _text in edits}
        for path in known.values():
            if path not in edited_paths:
                size = path.stat().st_size
                edited_size += size
                projects.check_workspace_size(size, edited_size, count)
        profile_text.replace(profile, edits)
        return self._profile({"project": payload["project"], "profile": profile_key})

    def _inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, ("bar_root",))
        root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
        found = discovery.discover(root)
        readiness = {
            engine: list(discovery.engine_readiness(found, engine))
            for engine in found.engines
        }
        return {
            "root": str(found.root),
            "data": str(found.data),
            "engines": list(found.engines),
            "active_engine": found.active_engine,
            "active_engine_message": found.active_engine_message,
            "game_directory": str(found.game_directory) if found.game_directory else None,
            "readiness": readiness,
        }

    def _install(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "confirm" in payload:
            raise ValueError("The confirm boolean is no longer supported; send confirmations.")
        self._require_fields(payload, ("project", "bar_root", "engine", "confirmations"))
        project_dir = self._project(self._string(payload["project"], "project"))
        bar_root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
        engine = self._string(payload["engine"], "engine")
        confirmations = payload["confirmations"]
        if (not isinstance(confirmations, list) or len(confirmations) > 16
                or any(not isinstance(message, str) or len(message) > 4096 for message in confirmations)):
            raise ValueError("confirmations must be a list of at most 16 exact prompt strings.")
        found = discovery.discover(bar_root)
        if engine not in found.engines:
            raise ValueError("Selected engine is not installed.")
        accepted = set(confirmations)

        def confirm(message: str) -> bool:
            if message not in accepted:
                raise ConfirmationRequired(message)
            return True

        try:
            result = installation.install(project_dir, found.data, engine, confirm)
        except ConfirmationRequired as challenge:
            return {"confirmation": challenge.message}
        short_name, name, _default = installation._metadata(project_dir)
        profiles = [{"key": key, "name": label} for key, label in projects.list_profiles(project_dir)]
        cache_updated = False
        try:
            cache_updated = self.server.match_service.sync_installed_ai(
                bar_root, engine, short_name, installation.VERSION, name, profiles,
            )
        except (MatchError, SettingsError, installation.InstallationError):
            cache_updated = False
        return {
            "path": str(result.path),
            "project": project_dir.name,
            "engine": engine,
            "changed": result.changed,
            "cache_updated": cache_updated,
        }

    def _install_status(self, project_key: str) -> dict[str, Any]:
        project_dir = self._project(self._string(project_key, "project"))
        configured = load_settings(self.server.settings_path)
        if configured is None:
            return {
                "installed": False,
                "managed": False,
                "status": "not_configured",
                "target": None,
                "message": "No BAR installation configured.",
            }
        try:
            found = discovery.discover(Path(configured.bar_root).expanduser())
            if configured.engine not in found.engines:
                return {
                    "installed": False,
                    "managed": False,
                    "status": "not_configured",
                    "target": None,
                    "message": f"Configured engine {configured.engine!r} is not installed.",
                }
            return installation.inspect_status(project_dir, found.data, configured.engine)
        except discovery.DiscoveryError as error:
            return {
                "installed": False,
                "managed": False,
                "status": "not_configured",
                "target": None,
                "message": str(error),
            }

    def _remove(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "confirm" in payload:
            raise ValueError("The confirm boolean is no longer supported; send confirmations.")
        self._require_fields(payload, ("project", "bar_root", "engine", "confirmations"))
        short_name = self._string(payload["project"], "project")
        bar_root = Path(self._string(payload["bar_root"], "bar_root")).expanduser()
        engine = self._string(payload["engine"], "engine")
        confirmations = payload["confirmations"]
        if (not isinstance(confirmations, list) or len(confirmations) > 16
                or any(not isinstance(message, str) or len(message) > 4096 for message in confirmations)):
            raise ValueError("confirmations must be a list of at most 16 exact prompt strings.")
        found = discovery.discover(bar_root)
        if engine not in found.engines:
            raise ValueError("Selected engine is not installed.")
        accepted = set(confirmations)

        def confirm(message: str) -> bool:
            if message not in accepted:
                raise ConfirmationRequired(message)
            return True

        try:
            target = installation.remove_installed(short_name, found.data, engine, confirm)
        except ConfirmationRequired as challenge:
            return {"confirmation": challenge.message}
        try:
            cache_updated = self.server.match_service.sync_removed_ai(
                bar_root, engine, short_name, installation.VERSION,
            )
        except (MatchError, SettingsError, installation.InstallationError):
            cache_updated = False
        return {"path": str(target), "project": short_name, "engine": engine, "cache_updated": cache_updated}

    def _unit_icon_catalog_game(self) -> str:
        try:
            catalog = self.server.match_service.latest_catalog()
        except (MatchError, installation.InstallationError):
            return ""
        return catalog["game"] if catalog else ""

    def _save_settings(self, payload: dict[str, Any]) -> dict[str, str]:
        self._require_fields(payload, ("bar_root", "engine"))
        bar_root = self._string(payload["bar_root"], "bar_root")
        engine = self._string(payload["engine"], "engine")
        found = discovery.discover(Path(bar_root).expanduser())
        if engine not in found.engines:
            raise ValueError("Selected engine is not installed.")
        missing = discovery.engine_readiness(found, engine)
        if missing:
            raise ValueError("Selected engine is not ready: " + "; ".join(missing))
        saved = Settings(str(found.root), engine)
        save_settings(self.server.settings_path, saved)
        unit_icons.clear_icon_cache()
        return {"bar_root": saved.bar_root, "engine": saved.engine}

    @staticmethod
    def _path_has_link(root: Path, path: Path) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return True
        current = root
        if _is_link(current):
            return True
        for part in relative.parts:
            current = current / part
            if _is_link(current):
                return True
        return False


def _is_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (is_junction is not None and is_junction())


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def serve(*, port: int = 0, no_browser: bool = False) -> None:
    server = StudioServer(("127.0.0.1", port))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(url, flush=True)
    if not no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local BAR AI web editor.")
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: random)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    serve(port=args.port, no_browser=args.no_browser)


if __name__ == "__main__":
    main()
