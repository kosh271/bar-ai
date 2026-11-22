"""HTTP-independent, asynchronous headless match service for the workshop.

Create one service per server, passing the repository root to ``MatchService``.
``scan`` and ``start`` return a status snapshot immediately; poll ``status``
until its state is terminal.  Synchronous admission errors raise MatchError;
worker errors are reported in the status.  All snapshots are JSON-serializable.
"""

from __future__ import annotations

import codecs
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfileobj
from typing import Callable

from . import discovery, headless, installation, project_history, settings
from .unit_catalog import valid_catalog


_operation_lock = threading.Lock()
_MAX_MATCH_SECONDS = 6 * 60 * 60
# The raw-byte and row limits keep even a fully JSON-escaped page below 256 KiB.
_LOG_PAGE_BYTES = 32 * 1024
_LOG_PAGE_LINES = 512
_RESULT_METADATA_BYTES = 8 * 1024 * 1024
_LOG_FILES = {"infolog.txt", "stdout.txt", "result.json"}
_RUN_ID = re.compile(r"(?:match|smoke)-[a-zA-Z0-9_-]{1,64}")
_REPLAY_PATH = re.compile(r"demos/([^/\\:\x00-\x1f]+\.sdfz)", re.IGNORECASE)
_VALID_FACTIONS = ("Armada", "Cortex", "Legion")
_FAILURE_MARKERS = (
    "Engine failed (exit",
    "Stopped: the game did not confirm",
    "Invalid winning sides reported:",
    "Smoke test failed",
    "Match ended without a confirmed result",
)


class MatchService:
    """Run at most one scan or match at a time across service instances."""

    def __init__(self, repository_root: str | Path, *, projects_root: Path | None = None) -> None:
        self.root = Path(repository_root).expanduser().resolve()
        if not self.root.is_dir():
            raise headless.MatchError(f"Repository root not found: {self.root}")
        self.projects_root = (
            Path(projects_root).expanduser().resolve()
            if projects_root is not None
            else (self.root / "projects")
        )
        self.local_data = self.root / "data" / "studio-matches"
        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._stop = threading.Event()
        self._game_stop: threading.Event | None = None
        self._state: dict = self._empty_state()
        self._process: subprocess.Popen | None = None
        self._content: dict | None = None
        self._scan_key: tuple[Path, str] | None = None
        self._invalidated_scans: set[tuple[Path, str]] = set()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "id": None, "operation": None, "state": "idle", "phase": None,
            "message": "", "progress": [], "result": None, "error": None,
            "logs": {}, "elapsed_seconds": None, "cancel_requested": False,
            "phase_elapsed_seconds": None, "engine": None, "series": None,
        }

    def status(self) -> dict:
        """Return an independent snapshot of the current or most recent operation."""
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            started = snapshot.pop("_started", None)
            if started is not None:
                snapshot["elapsed_seconds"] = max(0.0, time.monotonic() - started)
            phase_started = snapshot.pop("_phase_started", None)
            if phase_started is not None:
                snapshot["phase_elapsed_seconds"] = max(0.0, time.monotonic() - phase_started)
            if snapshot["operation"] == "match" and snapshot["state"] in ("running", "cancelling"):
                if snapshot["phase"] in ("smoke", "match"):
                    if self._process is None:
                        snapshot["engine"] = {"state": "starting", "exit_code": None}
                    else:
                        code = self._process.poll()
                        snapshot["engine"] = {
                            "state": "running" if code is None else "exited", "exit_code": code,
                        }
            return snapshot

    def cancel(self, expected_id: str | None = None) -> dict:
        """Request cooperative cancellation; poll status for the final outcome."""
        with self._lock:
            if self._state["state"] not in ("running", "cancelling"):
                raise headless.MatchError("No scan or match is running.")
            if expected_id is not None and self._state["id"] != expected_id:
                raise headless.MatchError("The requested operation is no longer running.")
            self._state["state"] = "cancelling"
            self._state["cancel_requested"] = True
            self._state["message"] = "Cancelling..."
            self._stop.set()
            if self._game_stop is not None:
                self._game_stop.set()
        return self.status()

    def _launch(self, operation: str, work: Callable[[], tuple[dict, dict, str]]) -> dict:
        with self._lock:
            if self._state["state"] in ("running", "cancelling"):
                raise headless.MatchError("A scan or match is already running.")
            if not _operation_lock.acquire(blocking=False):
                raise headless.MatchError("Another workshop scan or match is already running.")
            self._stop = threading.Event()
            self._game_stop = None
            self._state = self._empty_state()
            self._process = None
            self._state.update(
                id=uuid.uuid4().hex, operation=operation, state="running",
                phase="preparing", message=f"Preparing {operation}...",
                _started=time.monotonic(),
            )
            if operation == "scan":
                self._content = None
                self._scan_key = None

            def worker() -> None:
                completion = None
                failure = None
                try:
                    completion = work()
                except Exception as error:
                    failure = error
                finally:
                    with self._lock:
                        try:
                            if failure is not None:
                                raise failure
                            if completion is not None:
                                self._publish_result(operation, *completion)
                        except Exception as error:
                            self._state.update(
                                state="cancelled" if self._state["cancel_requested"] else "failed",
                                error=f"{type(error).__name__}: {error}",
                                message=str(error) or type(error).__name__,
                            )
                        finally:
                            finished = time.monotonic()
                            started = self._state.pop("_started")
                            self._state["elapsed_seconds"] = max(0.0, finished - started)
                            phase_started = self._state.pop("_phase_started", None)
                            if phase_started is not None:
                                self._state["phase_elapsed_seconds"] = max(0.0, finished - phase_started)
                            if self._state["state"] in ("running", "cancelling"):
                                self._state.update(
                                    state="failed", error="Operation worker stopped unexpectedly.",
                                    message="Operation worker stopped unexpectedly.",
                                )
                            self._process = None
                            self._game_stop = None
                            _operation_lock.release()

            try:
                threading.Thread(target=worker, name=f"studio-{operation}", daemon=True).start()
            except Exception:
                _operation_lock.release()
                self._state.update(state="failed", error="Cannot start worker thread.")
                self._state.pop("_started", None)
                raise
        return self.status()

    def _publish_result(self, operation: str, result: dict, logs: dict, outcome: str) -> None:
        """Publish the result while holding the service lock."""
        if outcome == "completed" and self._state["cancel_requested"]:
            outcome = "cancelled"
        if operation == "scan":
            cache_info = result.pop("_content_cache", None)
            if cache_info and outcome == "completed":
                data, engine = cache_info
                try:
                    cache_path = self._content_cache_path(data, engine)
                    installation._check_links(self.root, cache_path)
                    settings.save_content_cache(cache_path, data, engine, result["content"])
                except settings.SettingsError as error:
                    result["summary"] += f" Scan results could not be saved for next run: {error}"
                self._content = result["content"]
                self._scan_key = (data.parent, engine)
                self._invalidated_scans.discard(self._scan_key)
            elif outcome == "cancelled":
                result = {"summary": "Content scan cancelled."}
        if operation == "catalog" and outcome == "completed":
            self._save_catalog(result["catalog"])
        self._state.update(result=result, logs=logs, state=outcome)
        if outcome == "failed":
            self._state["error"] = result.get("summary", "Operation failed.")
        self._state["message"] = result.get("summary", outcome.capitalize())

    def _set_phase(self, phase: str, message: str) -> None:
        with self._lock:
            self._change_phase(phase)
            if not self._state["cancel_requested"]:
                self._state["message"] = message

    def _change_phase(self, phase: str) -> None:
        if self._state["phase"] != phase:
            self._state["phase"] = phase
            self._state["_phase_started"] = time.monotonic()
            self._process = None

    def _selected(self, bar_root: str | Path, engine: str) -> tuple[discovery.Installation, Path]:
        if not isinstance(engine, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", engine) or engine in (".", ".."):
            raise headless.MatchError("Invalid engine folder.")
        if not isinstance(bar_root, (str, Path)) or not str(bar_root).strip():
            raise headless.MatchError("BAR root is required.")
        selected = discovery.discover(Path(bar_root))
        if engine not in selected.engines:
            raise headless.MatchError(f"Select an installed engine; {engine!r} is not available.")
        engine_dir = selected.data / "engine" / engine
        installation._check_links(selected.data, engine_dir)
        return selected, engine_dir

    def _prepare_storage(self, engine_dir: Path, data: Path) -> Path:
        cache = headless.archive_cache_path(self.local_data, engine_dir, data)
        for path in (self.local_data, self.local_data / "scans", self.local_data / "matches", cache):
            installation._check_links(self.root, path)
        self.local_data.mkdir(parents=True, exist_ok=True)
        for directory in (self.local_data / "scans", self.local_data / "matches"):
            directory.mkdir(exist_ok=True)
        if cache.is_dir():
            for path in cache.iterdir():
                if re.fullmatch(r"ArchiveCache\d+\.lua", path.name):
                    installation._check_links(self.root, path)
        return cache

    def _content_cache_path(self, data: Path, engine: str) -> Path:
        identity = f"{str(data.resolve()).casefold()}\0{engine.casefold()}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.local_data / "content-cache" / f"{digest}.json"

    @staticmethod
    def _run_id(run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise headless.MatchError("Invalid match or smoke run ID.")
        return run_id

    def read_log(self, run_id: str, filename: str, offset: int = 0) -> dict:
        run_id = self._run_id(run_id)
        if (not isinstance(filename, str) or filename not in _LOG_FILES
                or type(offset) is not int or not 0 <= offset <= 2**63 - 1):
            raise headless.MatchError("Invalid log file or byte offset.")
        try:
            with headless.open_artifact(self.root, "data", "studio-matches", "matches", run_id, filename) as stream:
                stream.seek(offset)
                raw = stream.read(_LOG_PAGE_BYTES + 1)
        except FileNotFoundError:
            return {"offset": offset, "next_offset": None, "lines": [], "missing": True}
        has_next = len(raw) > _LOG_PAGE_BYTES
        chunk = raw[:_LOG_PAGE_BYTES]
        split = chunk.split(b"\n", _LOG_PAGE_LINES)
        if len(split) > _LOG_PAGE_LINES and split[-1]:
            chunk = chunk[:-len(split[-1])]
            has_next = True
        if has_next and (last_newline := chunk.rfind(b"\n")) >= 0:
            chunk = chunk[:last_newline + 1]
        decoded, consumed = codecs.utf_8_decode(chunk, "replace", not has_next)
        lines = decoded.split("\n")
        if lines[-1] == "":
            lines.pop()
        lines = [line.removesuffix("\r") for line in lines]
        return {
            "offset": offset, "next_offset": offset + consumed if has_next else None,
            "ends_with_newline": chunk.endswith(b"\n"),
            "lines": [{"text": line, "category": headless.diagnostic_category(line)} for line in lines],
        }

    def _artifact_metadata(self, run_id: str) -> dict:
        try:
            with headless.open_artifact(self.root, "data", "studio-matches", "matches", run_id, "result.json") as stream:
                raw = stream.read(_RESULT_METADATA_BYTES + 1)
            metadata = json.loads(raw) if len(raw) <= _RESULT_METADATA_BYTES else None
        except (OSError, ValueError, RecursionError, headless.MatchError):
            return {}
        return metadata if isinstance(metadata, dict) else {}

    def _run_artifacts(self, path: Path, events: list[dict]) -> tuple[dict, dict | None]:
        logs = {
            "directory": str(path), "output": str(path / "stdout.txt"),
            "infolog": str(path / "infolog.txt"), "result": str(path / "result.json"),
        }
        for event in events:
            if event["phase"] == "smoke":
                if event.get("run_id") and _RUN_ID.fullmatch(event["run_id"]):
                    logs["smoke_run_id"] = event["run_id"]
                for line in event["message"].splitlines():
                    if line.startswith("Smoke logs: "):
                        logs["smoke_directory"] = line[len("Smoke logs: "):]
        run_id = self._relative_run_dir(path)
        if not run_id or not _RUN_ID.fullmatch(run_id):
            return logs, None
        logs["run_id"] = run_id
        metadata = self._artifact_metadata(run_id)
        if isinstance(metadata.get("replays"), list):
            logs["replays"] = metadata["replays"]
        telemetry = metadata.get("telemetry")
        return logs, telemetry if isinstance(telemetry, dict) else None

    def copy_replay(self, run_id: str, replay: str, bar_root: str | Path, engine: str) -> dict:
        run_id = self._run_id(run_id)
        match = _REPLAY_PATH.fullmatch(replay) if isinstance(replay, str) else None
        if not run_id.startswith("match-") or match is None:
            raise headless.MatchError("Invalid match replay path.")
        replays = self._artifact_metadata(run_id).get("replays")
        if not isinstance(replays, list) or not any(
            isinstance(item, dict) and item.get("path") == replay for item in replays
        ):
            raise headless.MatchError("The replay is absent from the saved match result.")
        selected, _ = self._selected(bar_root, engine)
        directory = selected.data / "demos"
        name = match[1]
        target = directory / name
        with headless.open_artifact(self.root, "data", "studio-matches", "matches", run_id, "demos", name) as source:
            headless.artifact_stat(selected.root, "data", directory=True)
            directory.mkdir(exist_ok=True)
            headless.artifact_stat(selected.root, "data", "demos", directory=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix=".studio-demo-", dir=directory, delete=False) as destination:
                    temporary = Path(destination.name)
                    copyfileobj(source, destination)
                headless.artifact_stat(selected.root, "data", "demos", directory=True)
                # Windows rename and POSIX hard-link publication both refuse an existing target.
                if os.name == "nt":
                    temporary.rename(target)
                else:
                    os.link(temporary, target)
            except FileExistsError as error:
                raise headless.MatchError(f"A demo named {name} already exists in BAR's demos folder.") from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return {"path": str(target)}

    def cached_content(self, bar_root: str | Path, engine: str) -> dict | None:
        if not isinstance(bar_root, (str, Path)) or not str(bar_root).strip():
            raise headless.MatchError("BAR root is required.")
        if not isinstance(engine, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", engine) or engine in (".", ".."):
            raise headless.MatchError("Invalid engine folder.")
        root = Path(bar_root).expanduser().resolve()
        with self._lock:
            if (root, engine) in self._invalidated_scans:
                return None
        data = root / "data"
        path = self._content_cache_path(data, engine)
        installation._check_links(self.root, path)
        return settings.load_content_cache(path, data, engine)

    def sync_installed_ai(
        self, bar_root: str | Path, engine: str, short_name: str,
        version: str, display_name: str, profiles: list[dict[str, str]],
    ) -> bool:
        """Update cached AI display name and profiles after an installation without full rescan."""
        if not isinstance(bar_root, (str, Path)) or not str(bar_root).strip():
            raise headless.MatchError("BAR root is required.")
        if not isinstance(engine, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", engine) or engine in (".", ".."):
            raise headless.MatchError("Invalid engine folder.")
        root = Path(bar_root).expanduser().resolve()
        data = root / "data"
        cache_path = self._content_cache_path(data, engine)
        installation._check_links(self.root, cache_path)
        with self._lock:
            if self._content is not None and self._scan_key == (root, engine):
                content = copy.deepcopy(self._content)
            else:
                content = settings.load_content_cache(cache_path, data, engine)
            if content is None:
                return False
            matches = [
                ai for ai in content["ais"]
                if ai["short_name"] == short_name and ai["version"] == version
            ]
            if len(matches) != 1:
                return False
            matches[0]["name"] = display_name
            matches[0]["profiles"] = [
                {"key": str(item["key"]), "name": str(item["name"])}
                for item in profiles
            ]
            if not settings._content_valid(content):
                raise headless.MatchError("Updated AI profile metadata is invalid.")
            if self._scan_key == (root, engine) and self._content is not None:
                self._content = copy.deepcopy(content)
        settings.save_content_cache(cache_path, data, engine, content)
        return True

    def _save_catalog(self, catalog: dict) -> None:
        path = self.local_data / "latest-catalog.json"
        installation._check_links(self.root, path)
        self.local_data.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".catalog-", dir=self.local_data)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(catalog, output, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def latest_catalog(self) -> dict | None:
        """Return the last completed catalog, or a pre-existing legacy scan."""
        path = self.local_data / "latest-catalog.json"
        installation._check_links(self.root, path)
        if not path.exists():
            scans = self.local_data / "scans"
            installation._check_links(self.root, scans)
            if not scans.is_dir():
                return None
            candidates = []
            for directory in scans.iterdir():
                if not directory.name.startswith("catalog-"):
                    continue
                installation._check_links(self.root, directory)
                if not directory.is_dir() or (directory / ".studio-catalog").exists():
                    continue
                candidate = directory / "units.json"
                installation._check_links(self.root, candidate)
                if candidate.is_file():
                    candidates.append(candidate)
            if not candidates:
                return None
            path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise headless.MatchError(f"Cannot read saved unit catalog {path}: {error}") from error
        if not valid_catalog(catalog):
            raise headless.MatchError(f"Saved unit catalog is invalid: {path}")
        return catalog

    def scan(self, bar_root: str | Path, engine: str) -> dict:
        """Scan installed content asynchronously; result.content contains the choices."""
        def work() -> tuple[dict, dict, str]:
            selected, engine_dir = self._selected(bar_root, engine)
            with self._lock:
                self._invalidated_scans.add((selected.root, engine))
            cache = self._prepare_storage(engine_dir, selected.data)
            scan_dir = Path(tempfile.mkdtemp(prefix="scan-", dir=self.local_data / "scans"))
            logs = {"directory": str(scan_dir), "output": str(scan_dir / "scan-output.txt")}
            with self._lock:
                self._state["logs"] = logs
            if self._stop.is_set():
                raise headless.MatchError("Content scan cancelled.")
            self._set_phase("scan", "Scanning installed content...")
            content = headless.scan_content(engine_dir, selected.data, scan_dir, self._stop, archive_cache=cache)
            if not settings._content_valid(content):
                raise headless.MatchError("The engine returned an invalid content scan result.")
            return {
                "summary": "Content scan complete.", "content": content,
                "_content_cache": (selected.data, engine),
            }, logs, "completed"

        return self._launch("scan", work)

    def catalog(self, bar_root: str | Path, engine: str, game: str) -> dict:
        """Read a selected game's unit definitions in an isolated process."""
        if not isinstance(game, str) or not game.strip():
            raise headless.MatchError("Select an installed game for the unit catalog.")

        def work() -> tuple[dict, dict, str]:
            selected, engine_dir = self._selected(bar_root, engine)
            cache = self._prepare_storage(engine_dir, selected.data)
            scan_dir = Path(tempfile.mkdtemp(prefix="catalog-", dir=self.local_data / "scans"))
            (scan_dir / ".studio-catalog").write_text("catalog scan\n", encoding="utf-8")
            logs = {"directory": str(scan_dir), "output": str(scan_dir / "scan-output.txt")}
            with self._lock:
                self._state["logs"] = logs
            if self._stop.is_set():
                raise headless.MatchError("Unit catalog cancelled.")
            self._set_phase("catalog", "Reading unit definitions from the selected game...")
            catalog = headless.scan_units(engine_dir, selected.data, scan_dir, game, self._stop, archive_cache=cache)
            if self._stop.is_set():
                raise headless.MatchError("Unit catalog cancelled.")
            return {"summary": "Unit catalog complete.", "catalog": catalog}, logs, "completed"

        return self._launch("catalog", work)

    def _validate_players(self, players: object, content: dict, *, require_both_sides: bool = True) -> list[headless.Player]:
        minimum = 2 if require_both_sides else 1
        if not isinstance(players, list) or not minimum <= len(players) <= 16:
            raise headless.MatchError(f"Players must be a list of {minimum} to 16 entries.")
        match_players = []
        for player in players:
            if not isinstance(player, dict):
                raise headless.MatchError("Each player must be an object.")
            short_name, version = player.get("short_name"), player.get("version")
            profile, faction, ally = player.get("profile"), player.get("faction"), player.get("ally")
            if (not isinstance(short_name, str) or not isinstance(version, str)
                    or not isinstance(profile, str) or not isinstance(faction, str)
                    or type(ally) is not int or ally not in (0, 1)
                    or faction not in _VALID_FACTIONS):
                raise headless.MatchError("Invalid player fields or faction.")
            ais = [ai for ai in content["ais"] if ai["short_name"] == short_name and ai["version"] == version]
            if len(ais) != 1:
                raise headless.MatchError(f"AI {short_name}:{version} is not uniquely installed.")
            profiles = ais[0]["profiles"]
            if profile not in [item["key"] for item in profiles] and (profile or profiles):
                raise headless.MatchError(f"Profile {profile!r} is not installed for {short_name}:{version}.")
            match_players.append(headless.Player(short_name, version, profile, faction, ally, ais[0]["name"]))
        if require_both_sides and {player.ally for player in match_players} != {0, 1}:
            raise headless.MatchError("At least one AI is required on each side.")
        return match_players

    @staticmethod
    def _validate_series(series: object, default_map: str, content: dict) -> dict | None:
        if series is None:
            return None
        if not isinstance(series, dict):
            raise headless.MatchError("Series settings must be an object.")
        games = series.get("games", 1)
        if type(games) is not int or not 1 <= games <= 100:
            raise headless.MatchError("Series game count must be an integer between 1 and 100.")
        swap_sides = series.get("swap_sides", False)
        if type(swap_sides) is not bool:
            raise headless.MatchError("swap_sides must be a boolean.")
        raw_maps = series.get("maps")
        if raw_maps is None:
            maps = [default_map]
        else:
            if not isinstance(raw_maps, list) or not raw_maps:
                raise headless.MatchError("Series map pool must be a non-empty list.")
            installed_maps = {item["name"] for item in content["maps"]}
            if any(not isinstance(item, str) or item not in installed_maps for item in raw_maps):
                raise headless.MatchError("Select installed maps for the benchmark series.")
            maps = list(raw_maps)
        raw_pairs = series.get("faction_pairs")
        if raw_pairs is None:
            faction_pairs: list[list[str]] = []
        else:
            if not isinstance(raw_pairs, list):
                raise headless.MatchError("faction_pairs must be a list.")
            faction_pairs = []
            for pair in raw_pairs:
                if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                        or any(not isinstance(item, str) or item not in _VALID_FACTIONS for item in pair)):
                    raise headless.MatchError("Each faction pair must list two valid factions.")
                faction_pairs.append([pair[0], pair[1]])
        return {"games": games, "swap_sides": swap_sides, "maps": maps, "faction_pairs": faction_pairs}

    def _capture_project_snapshots(
        self, players: list[headless.Player], data: Path, engine: str,
    ) -> list[dict]:
        snapshots: list[dict] = []
        seen: set[str] = set()
        for player in players:
            key = player.short_name
            if key in seen:
                continue
            seen.add(key)
            project_dir = self.projects_root / key
            try:
                if (installation._link(project_dir) or not project_dir.is_dir()
                        or not (project_dir / "project.json").is_file()):
                    continue
            except OSError:
                continue
            hist = project_history.status_summary(project_dir)
            try:
                inst = installation.inspect_status(project_dir, data, engine)
            except (installation.InstallationError, OSError) as error:
                inst = {"status": "error", "message": str(error)}
            checkpoint = hist.get("checkpoint")
            dirty = hist.get("dirty")
            install_status = inst.get("status")
            snapshots.append({
                "project": key,
                "checkpoint": checkpoint,
                "checkpoint_description": hist.get("checkpoint_description"),
                "uncheckpointed_changes": dirty,
                "history_error": hist.get("error"),
                "install_status": install_status,
                "install_drift": install_status != "up_to_date",
                "clean": bool(checkpoint) and (dirty is False) and (install_status == "up_to_date"),
            })
        return snapshots

    def _matches_dir(self) -> Path:
        matches_dir = self.local_data / "matches"
        installation._check_links(self.root, self.local_data)
        installation._check_links(self.root, matches_dir)
        return matches_dir

    def _relative_run_dir(self, path: Path | None) -> str | None:
        if path is None:
            return None
        matches_dir = (self.local_data / "matches").resolve()
        try:
            candidate = path.resolve()
            if installation._link(candidate) or not candidate.is_relative_to(matches_dir):
                return None
            relative = candidate.relative_to(matches_dir)
            if not relative.parts:
                return None
            installation._check_links(matches_dir, candidate)
            return relative.as_posix()
        except (OSError, ValueError, installation.InstallationError):
            return None

    def _enrich_result_json(self, path: Path | None, metadata: dict) -> None:
        if path is None:
            return
        rel = self._relative_run_dir(path)
        if rel is None:
            return
        result_file = path / "result.json"
        if installation._link(result_file) or not result_file.is_file():
            return
        try:
            existing = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(existing, dict):
            return
        existing.update(metadata)
        descriptor, temp_name = tempfile.mkstemp(prefix=".result-", dir=path)
        temporary = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(existing, output, indent=2, ensure_ascii=False)
                output.write("\n")
            os.replace(temporary, result_file)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_history_entries_unlocked(self) -> list[dict]:
        matches_dir = self._matches_dir()
        history_path = matches_dir / "history.json"
        installation._check_links(self.root, history_path)
        if not history_path.exists():
            return []
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise headless.MatchError(f"Cannot read match history {history_path}: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise headless.MatchError(f"Invalid match history format in {history_path}.")
        return [entry for entry in data["entries"] if isinstance(entry, dict)]

    def _append_history(self, entry: dict) -> None:
        with self._history_lock:
            matches_dir = self._matches_dir()
            self.local_data.mkdir(parents=True, exist_ok=True)
            matches_dir.mkdir(exist_ok=True)
            history_path = matches_dir / "history.json"
            try:
                entries = self._load_history_entries_unlocked()
            except headless.MatchError:
                entries = []
            entries.insert(0, entry)
            descriptor, temp_name = tempfile.mkstemp(prefix=".history-", dir=matches_dir)
            temporary = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump({"version": 1, "entries": entries}, output, indent=2, ensure_ascii=False)
                    output.write("\n")
                os.replace(temporary, history_path)
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _parse_legacy_script(script_path: Path) -> tuple[str, str, list[dict]]:
        if installation._link(script_path) or not script_path.is_file():
            return "", "", []
        try:
            text = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", "", []
        map_match = re.search(r"(?m)^\s*MapName=([^;\r\n]+);", text)
        game_match = re.search(r"(?m)^\s*GameType=([^;\r\n]+);", text)
        map_name = map_match.group(1).strip() if map_match else ""
        game_name = game_match.group(1).strip() if game_match else ""
        ais: dict[int, dict] = {}
        teams: dict[int, dict] = {}
        for match in re.finditer(r"\[(AI|TEAM)(\d+)\]\s*\{(.*?)\n\}", text, re.DOTALL):
            kind, idx, body = match.group(1), int(match.group(2)), match.group(3)
            if kind == "AI":
                short_m = re.search(r"(?m)^\s*ShortName=([^;\r\n]+);", body)
                ver_m = re.search(r"(?m)^\s*Version=([^;\r\n]+);", body)
                prof_m = re.search(r"(?m)^\s*profile=([^;\r\n]+);", body)
                team_m = re.search(r"(?m)^\s*Team=(\d+);", body)
                ais[idx] = {
                    "short_name": short_m.group(1).strip() if short_m else "",
                    "version": ver_m.group(1).strip() if ver_m else "",
                    "profile": prof_m.group(1).strip() if prof_m else "",
                    "team": int(team_m.group(1)) if team_m else idx,
                }
            else:
                ally_m = re.search(r"(?m)^\s*AllyTeam=(\d+);", body)
                side_m = re.search(r"(?m)^\s*Side=([^;\r\n]+);", body)
                teams[idx] = {
                    "ally": int(ally_m.group(1)) if ally_m else 0,
                    "faction": side_m.group(1).strip() if side_m else "",
                }
        players = []
        for idx in sorted(ais):
            ai = ais[idx]
            team = teams.get(ai["team"], {})
            players.append({
                "short_name": ai["short_name"],
                "version": ai["version"],
                "profile": ai["profile"],
                "faction": team.get("faction", ""),
                "ally": team.get("ally", 0),
                "name": ai["short_name"],
            })
        return map_name, game_name, players

    def _legacy_entry_from_dir(self, directory: Path) -> dict | None:
        result_file = directory / "result.json"
        if installation._link(result_file) or not result_file.is_file():
            return None
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        script_map, script_game, script_players = self._parse_legacy_script(directory / "script.txt")
        mtime = datetime.fromtimestamp(result_file.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        timestamp = payload.get("timestamp") if isinstance(payload.get("timestamp"), str) else mtime
        game_name = payload.get("game") if isinstance(payload.get("game"), str) else script_game
        map_name = payload.get("map") if isinstance(payload.get("map"), str) else script_map
        players = payload.get("players") if isinstance(payload.get("players"), list) else script_players
        projects = payload.get("projects") if isinstance(payload.get("projects"), list) else []
        elapsed = payload.get("elapsed_seconds") if isinstance(payload.get("elapsed_seconds"), (int, float)) else None
        engine = payload.get("engine") if isinstance(payload.get("engine"), str) else ""
        if "smoke_passed" in payload:
            passed = bool(payload.get("smoke_passed"))
            outcome = payload.get("outcome") or ("Smoke test passed." if passed else "Smoke test failed.")
            state = "completed" if passed else "failed"
            summary = "\n".join(payload.get("failures", [])) or outcome
        else:
            summary = str(payload.get("result", ""))
            first_line = summary.splitlines()[0] if summary else ""
            outcome = payload.get("outcome") or first_line or "Unknown"
            if "Stopped by user" in summary:
                state = "cancelled"
            elif any(marker in summary for marker in _FAILURE_MARKERS):
                state = "failed"
            else:
                state = "completed"
        return {
            "id": directory.name,
            "kind": "match",
            "timestamp": timestamp,
            "state": state,
            "engine": engine,
            "game": game_name,
            "map": map_name,
            "maps": [map_name] if map_name else [],
            "minutes": None,
            "skip_smoke": directory.name.startswith("match-"),
            "outcome": outcome,
            "elapsed_seconds": elapsed,
            "summary": summary,
            "run_dir": directory.name,
            "players": players,
            "projects": projects,
            "series": None,
        }

    def history(
        self, *, project: str | None = None, profile: str | None = None,
        map_name: str | None = None, checkpoint: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """Return persistent match and series history entries, safely bounded within local_data."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise headless.MatchError("History limit must be an integer between 1 and 500.")
        with self._history_lock:
            entries = self._load_history_entries_unlocked()
            matches_dir = self._matches_dir()
            indexed_dirs: set[str] = set()
            for entry in entries:
                if isinstance(entry.get("run_dir"), str) and entry["run_dir"]:
                    indexed_dirs.add(entry["run_dir"])
                series_info = entry.get("series")
                if isinstance(series_info, dict):
                    for game_item in series_info.get("games", []):
                        if isinstance(game_item, dict) and isinstance(game_item.get("run_dir"), str):
                            indexed_dirs.add(game_item["run_dir"])
            if matches_dir.is_dir():
                resolved_matches = matches_dir.resolve()
                for child in sorted(matches_dir.iterdir()):
                    if child.name in indexed_dirs or not child.name.startswith(("match-", "smoke-")):
                        continue
                    installation._check_links(matches_dir, child)
                    if not child.is_dir() or not child.resolve().is_relative_to(resolved_matches):
                        continue
                    legacy = self._legacy_entry_from_dir(child)
                    if legacy is not None:
                        entries.append(legacy)

        filtered = []
        for entry in entries:
            entry_players = [p for p in entry.get("players", []) if isinstance(p, dict)]
            entry_projects = [p for p in entry.get("projects", []) if isinstance(p, dict)]
            entry_maps = [m for m in entry.get("maps", []) if isinstance(m, str) and m]
            if not entry_maps and isinstance(entry.get("map"), str) and entry["map"]:
                entry_maps = [entry["map"]]
            if project:
                target = project.casefold()
                if not (
                    any(str(p.get("short_name", "")).casefold() == target for p in entry_players)
                    or any(str(p.get("project", "")).casefold() == target for p in entry_projects)
                ):
                    continue
            if profile:
                target = profile.casefold()
                if not any(str(p.get("profile", "")).casefold() == target for p in entry_players):
                    continue
            if map_name:
                target = map_name.casefold()
                if not any(item.casefold() == target for item in entry_maps):
                    continue
            if checkpoint:
                target = checkpoint.casefold()
                if not any(str(p.get("checkpoint") or "").casefold().startswith(target) for p in entry_projects):
                    continue
            filtered.append(entry)

        filtered.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return copy.deepcopy(filtered[:limit])

    def _classify_game_outcome(
        self, summary: str, path: Path | None, timed_out: bool,
        cancelled: bool, swapped: bool, events: list[dict],
    ) -> tuple[str, bool]:
        if cancelled:
            return "cancelled", False
        if timed_out:
            return "timeout", True
        failed = any(event.get("summary") == "Failed" for event in events) or any(
            marker in summary for marker in _FAILURE_MARKERS
        )
        if failed:
            return "failed", True
        relative_run = self._relative_run_dir(path)
        if relative_run and _RUN_ID.fullmatch(relative_run):
            data = self._artifact_metadata(relative_run)
            if data.get("smoke_passed") is False:
                return "failed", True
            winning = data.get("winning_sides")
            if isinstance(winning, list) and all(type(side) is int and side in (0, 1) for side in winning):
                if len(winning) == 1:
                    return ("side2" if ((winning[0] == 0) == swapped) else "side1"), False
                if not winning or len(winning) == 2 and winning[0] != winning[1]:
                    return "draw", False
        winner_match = re.search(r"Winner: side ([12])\.", summary)
        if winner_match:
            winner = int(winner_match.group(1)) - 1
            return ("side2" if ((winner == 0) == swapped) else "side1"), False
        lines = [line.strip() for line in summary.splitlines()]
        if "Draw." in lines:
            return "draw", False
        if "Time limit reached" in summary:
            return "timeout", False
        if "Stopped by user" in summary or "match not started" in summary:
            return "cancelled", False
        return "completed", False

    def _run_game(
        self, engine_dir: Path, data: Path, map_name: str, game: str, players: list[headless.Player],
        minutes: int, *, skip_smoke: bool, record_demo: bool = False, label: str = "",
        real_time_deadline: float | None = None,
    ) -> tuple[str, Path, bool, bool, list[dict]]:
        game_stop = threading.Event()
        with self._lock:
            if self._stop.is_set() or self._state["cancel_requested"]:
                raise headless.MatchError("Stopped before the match started.")
            if real_time_deadline is not None and real_time_deadline <= time.monotonic():
                raise headless.MatchError("Match exceeded its real-time deadline.")
            self._game_stop = game_stop
        self._set_phase("smoke" if not skip_smoke else "match", f"{label}Starting headless match...")
        timed_out = threading.Event()

        def timeout() -> None:
            timed_out.set()
            game_stop.set()

        budget = min(_MAX_MATCH_SECONDS, 600 + minutes * 60)
        if real_time_deadline is not None:
            budget = min(budget, max(0, real_time_deadline - time.monotonic()))
            if budget == 0:
                timeout()
        timer = threading.Timer(budget, timeout)
        timer.daemon = True
        timer.start()

        def progress(event: headless.MatchProgress) -> None:
            with self._lock:
                if event.summary != "Skipped":
                    self._change_phase(event.phase)
                self._state["progress"].append(asdict(event))
                self._state["progress"] = self._state["progress"][-64:]
                if event.run_id and _RUN_ID.fullmatch(event.run_id):
                    key = "smoke_run_id" if event.phase == "smoke" else "run_id"
                    self._state["logs"][key] = event.run_id
                if event.message and not self._state["cancel_requested"]:
                    self._state["message"] = f"{label}{event.message}"

        def process_started(process: subprocess.Popen) -> None:
            with self._lock:
                self._process = process

        try:
            summary, path = headless.run_match(
                engine_dir, data, self.local_data, map_name, game, players, minutes, game_stop,
                progress, skip_smoke=skip_smoke, process_started=process_started, record_demo=record_demo,
            )
        except Exception as error:
            if timed_out.is_set():
                raise headless.MatchError("Match exceeded its real-time deadline.") from error
            raise
        finally:
            timer.cancel()
            with self._lock:
                self._game_stop = None
        with self._lock:
            cancelled = self._state["cancel_requested"]
            events = list(self._state["progress"])
        return summary, path, timed_out.is_set(), cancelled, events

    def start(
        self, bar_root: str | Path, engine: str, game: str, map_name: str,
        players: list[dict], minutes: int, skip_smoke: bool = False,
        series: dict | None = None, *, record_demo: bool = False,
    ) -> dict:
        """Start a match or benchmark series from the last successful scan.

        Each player is an object with short_name, version, profile, faction and
        ally (0 or 1). The AI display name is taken from the scan, not the client.
        """
        if type(record_demo) is not bool:
            raise headless.MatchError("record_demo must be a boolean.")
        players = copy.deepcopy(players)
        series = copy.deepcopy(series)

        def work() -> tuple[dict, dict, str]:
            selected, engine_dir = self._selected(bar_root, engine)
            missing = discovery.engine_readiness(selected, engine)
            if missing:
                raise headless.MatchError("Engine is not ready: " + "; ".join(missing))
            with self._lock:
                content = self._content if self._scan_key == (selected.root, engine) else None
            if content is None:
                content = self.cached_content(selected.root, engine)
            if content is None:
                raise headless.MatchError("Scan installed content for the selected engine first.")
            if not isinstance(game, str) or game not in content["games"]:
                raise headless.MatchError("Select an installed game.")
            if not isinstance(map_name, str) or map_name not in [item["name"] for item in content["maps"]]:
                raise headless.MatchError("Select an installed map.")
            if type(minutes) is not int or type(skip_smoke) is not bool:
                raise headless.MatchError("Minutes must be an integer and skip_smoke must be a boolean.")
            match_players = self._validate_players(players, content)
            series_cfg = self._validate_series(series, map_name, content)
            headless.build_script(map_name, game, match_players, minutes)
            self._prepare_storage(engine_dir, selected.data)
            project_snapshots = self._capture_project_snapshots(match_players, selected.data, engine)
            with self._lock:
                op_id = self._state["id"] or uuid.uuid4().hex
                run_started_mono = self._state.get("_started") or time.monotonic()

            def run_single_game(
                game_map: str, game_players: list[headless.Player], label_prefix: str = "",
            ) -> tuple[str, Path | None, bool, bool, list[dict]]:
                return self._run_game(
                    engine_dir, selected.data, game_map, game, game_players, minutes,
                    skip_smoke=skip_smoke, record_demo=record_demo, label=label_prefix,
                )

            def record_single_history(
                state_name: str, summary_text: str, outcome_label: str, path: Path | None,
                duration: float, *, game_map: str = map_name,
            ) -> None:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                rel_dir = self._relative_run_dir(path)
                player_dicts = [asdict(player) for player in match_players]
                self._enrich_result_json(path, {
                    "timestamp": timestamp,
                    "engine": engine,
                    "game": game,
                    "map": game_map,
                    "players": player_dicts,
                    "outcome": outcome_label,
                    "elapsed_seconds": round(duration, 2),
                    "projects": project_snapshots,
                })
                self._append_history({
                    "id": op_id,
                    "kind": "match",
                    "timestamp": timestamp,
                    "state": state_name,
                    "engine": engine,
                    "game": game,
                    "map": game_map,
                    "maps": [game_map],
                    "minutes": minutes,
                    "skip_smoke": skip_smoke,
                    "outcome": outcome_label,
                    "elapsed_seconds": round(duration, 2),
                    "summary": summary_text,
                    "run_dir": rel_dir,
                    "players": player_dicts,
                    "projects": project_snapshots,
                    "series": None,
                })

            if series_cfg is None:
                try:
                    summary, path, was_timed_out, cancelled, events = run_single_game(map_name, match_players)
                except Exception as error:
                    duration = max(0.0, time.monotonic() - run_started_mono)
                    with self._lock:
                        cancelled = self._stop.is_set() and self._state["cancel_requested"]
                    outcome_label = (
                        "Cancelled" if cancelled
                        else "Timeout" if "real-time deadline" in str(error)
                        else "Failed"
                    )
                    record_single_history(
                        "cancelled" if cancelled else "failed",
                        str(error),
                        outcome_label,
                        None,
                        duration,
                    )
                    raise
                logs, telemetry = self._run_artifacts(path, events)
                result = {"summary": summary, "path": str(path)}
                if telemetry is not None:
                    result["telemetry"] = telemetry
                duration = max(0.0, time.monotonic() - run_started_mono)
                if was_timed_out:
                    result["summary"] += "\nMatch exceeded its real-time deadline."
                    record_single_history("failed", result["summary"], "Timeout", path, duration)
                    return result, logs, "failed"
                if cancelled:
                    record_single_history("cancelled", result["summary"], "Cancelled", path, duration)
                    return result, logs, "cancelled"
                outcome_code, failed = self._classify_game_outcome(summary, path, False, False, False, events)
                final_state = "failed" if failed else "completed"
                outcome_label = summary.splitlines()[0] if summary else outcome_code
                record_single_history(final_state, result["summary"], outcome_label, path, duration)
                return result, logs, final_state

            # Sequential benchmark series
            total_games = series_cfg["games"]
            swap_sides = series_cfg["swap_sides"]
            series_maps = series_cfg["maps"]
            faction_pairs = series_cfg["faction_pairs"]
            series_state = {
                "id": op_id,
                "total_games": total_games,
                "completed_games": 0,
                "current_game": 0,
                "current_map": None,
                "swapped": False,
                "swap_sides": swap_sides,
                "maps": list(series_maps),
                "faction_pairs": [list(pair) for pair in faction_pairs],
                "side1_wins": 0,
                "side2_wins": 0,
                "draws": 0,
                "timeouts": 0,
                "failures": 0,
                "cancelled": 0,
                "games": [],
            }
            with self._lock:
                self._state["series"] = copy.deepcopy(series_state)
            last_path: Path | None = None
            last_logs: dict = {}
            last_telemetry: dict | None = None
            for idx in range(total_games):
                with self._lock:
                    if self._stop.is_set() or self._state["cancel_requested"]:
                        break
                    self._state["progress"] = []
                    self._state["logs"] = {}
                game_map = series_maps[idx % len(series_maps)]
                pair = faction_pairs[idx % len(faction_pairs)] if faction_pairs else None
                swapped = bool(swap_sides and (idx % 2 == 1))
                game_players = [
                    headless.Player(
                        player.short_name,
                        player.version,
                        player.profile,
                        pair[player.ally] if pair is not None else player.faction,
                        (1 - player.ally) if swapped else player.ally,
                        player.name,
                    )
                    for player in match_players
                ]
                headless.build_script(game_map, game, game_players, minutes)
                series_state["current_game"] = idx + 1
                series_state["current_map"] = game_map
                series_state["swapped"] = swapped
                with self._lock:
                    self._state["series"] = copy.deepcopy(series_state)
                game_started = time.monotonic()
                game_path: Path | None = None
                game_summary = ""
                game_outcome = "failed"
                try:
                    game_summary, game_path, game_timed_out, game_cancelled, game_events = run_single_game(
                        game_map, game_players, f"[Game {idx + 1}/{total_games} · {game_map}] ",
                    )
                    if game_timed_out:
                        game_summary = (game_summary + "\nMatch exceeded its real-time deadline.").strip()
                    game_outcome, _ = self._classify_game_outcome(
                        game_summary, game_path, game_timed_out, game_cancelled, swapped, game_events,
                    )
                except Exception as error:
                    with self._lock:
                        game_cancelled = self._state["cancel_requested"] or self._stop.is_set()
                    game_summary = str(error)
                    if game_cancelled:
                        game_outcome = "cancelled"
                    elif not isinstance(error, headless.MatchError):
                        raise
                    else:
                        game_outcome = "timeout" if "real-time deadline" in game_summary else "failed"
                game_elapsed = round(max(0.0, time.monotonic() - game_started), 2)
                if game_path is not None:
                    last_path = game_path
                    last_logs, last_telemetry = self._run_artifacts(game_path, game_events)
                if game_outcome == "side1":
                    series_state["side1_wins"] += 1
                    series_state["completed_games"] += 1
                elif game_outcome == "side2":
                    series_state["side2_wins"] += 1
                    series_state["completed_games"] += 1
                elif game_outcome == "draw":
                    series_state["draws"] += 1
                    series_state["completed_games"] += 1
                elif game_outcome == "timeout":
                    series_state["timeouts"] += 1
                    series_state["completed_games"] += 1
                elif game_outcome == "cancelled":
                    series_state["cancelled"] += 1
                else:
                    series_state["failures"] += 1
                    series_state["completed_games"] += 1

                game_player_dicts = [asdict(player) for player in game_players]
                rel_dir = self._relative_run_dir(game_path)
                game_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._enrich_result_json(game_path, {
                    "timestamp": game_timestamp,
                    "engine": engine,
                    "game": game,
                    "map": game_map,
                    "players": game_player_dicts,
                    "outcome": game_outcome,
                    "elapsed_seconds": game_elapsed,
                    "projects": project_snapshots,
                    "series_id": op_id,
                    "game_index": idx + 1,
                    "swapped": swapped,
                })
                series_state["games"].append({
                    "game_index": idx + 1,
                    "map": game_map,
                    "swapped": swapped,
                    "factions": list(pair) if pair is not None else None,
                    "outcome": game_outcome,
                    "elapsed_seconds": game_elapsed,
                    "summary": game_summary,
                    "run_dir": rel_dir,
                    "players": game_player_dicts,
                })
                with self._lock:
                    self._state["series"] = copy.deepcopy(series_state)
                if game_outcome == "cancelled":
                    break

            with self._lock:
                cancelled_series = self._state["cancel_requested"] or self._stop.is_set()
            overall_state = (
                "cancelled" if cancelled_series
                else "failed" if series_state["failures"] == total_games and total_games > 0
                else "completed"
            )
            tally = (
                f"Side 1: {series_state['side1_wins']}W · "
                f"Side 2: {series_state['side2_wins']}W · "
                f"Draws: {series_state['draws']} · "
                f"Timeouts: {series_state['timeouts']} · "
                f"Failures: {series_state['failures']}"
            )
            header = (
                f"Series cancelled after {len(series_state['games'])}/{total_games} games"
                if cancelled_series
                else f"Series complete ({series_state['completed_games']}/{total_games} games)"
            )
            lines = [f"{header}: {tally}"]
            for g in series_state["games"]:
                lines.append(
                    f"Game {g['game_index']} ({g['map']}{', swapped' if g['swapped'] else ''}): "
                    f"{g['outcome']} ({g['elapsed_seconds']:.1f}s)"
                )
            series_summary_text = "\n".join(lines)
            duration = round(max(0.0, time.monotonic() - run_started_mono), 2)
            series_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._append_history({
                "id": op_id,
                "kind": "series",
                "timestamp": series_timestamp,
                "state": overall_state,
                "engine": engine,
                "game": game,
                "map": ", ".join(dict.fromkeys(series_maps)),
                "maps": list(series_maps),
                "minutes": minutes,
                "skip_smoke": skip_smoke,
                "outcome": tally,
                "elapsed_seconds": duration,
                "summary": series_summary_text,
                "run_dir": self._relative_run_dir(last_path),
                "players": [asdict(player) for player in match_players],
                "projects": project_snapshots,
                "series": copy.deepcopy(series_state),
            })
            result = {
                "summary": series_summary_text,
                "path": str(last_path) if last_path is not None else "",
                "series": copy.deepcopy(series_state),
            }
            if last_telemetry is not None:
                result["telemetry"] = last_telemetry
            return result, last_logs, overall_state

        return self._launch("match", work)
