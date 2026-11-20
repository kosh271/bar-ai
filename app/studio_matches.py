"""HTTP-independent, asynchronous headless match service for the workshop.

Create one service per server, passing the repository root to ``MatchService``.
``scan`` and ``start`` return a status snapshot immediately; poll ``status``
until its state is terminal.  Synchronous admission errors raise MatchError;
worker errors are reported in the status.  All snapshots are JSON-serializable.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import discovery, headless, installation, settings
from .unit_catalog import valid_catalog


_operation_lock = threading.Lock()
_MAX_MATCH_SECONDS = 6 * 60 * 60


class MatchService:
    """Run at most one scan or match at a time across service instances."""

    def __init__(self, repository_root: str | Path) -> None:
        self.root = Path(repository_root).expanduser().resolve()
        if not self.root.is_dir():
            raise headless.MatchError(f"Repository root not found: {self.root}")
        self.local_data = self.root / "data" / "studio-matches"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: dict = self._empty_state()
        self._process: subprocess.Popen | None = None
        self._content: dict | None = None
        self._scan_key: tuple[Path, str] | None = None

    @staticmethod
    def _empty_state() -> dict:
        return {
            "id": None, "operation": None, "state": "idle", "phase": None,
            "message": "", "progress": [], "result": None, "error": None,
            "logs": {}, "elapsed_seconds": None, "cancel_requested": False,
            "phase_elapsed_seconds": None, "engine": None,
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
        return self.status()

    def _launch(self, operation: str, work: Callable[[], tuple[dict, dict, str]]) -> dict:
        with self._lock:
            if self._state["state"] in ("running", "cancelling"):
                raise headless.MatchError("A scan or match is already running.")
            if not _operation_lock.acquire(blocking=False):
                raise headless.MatchError("Another workshop scan or match is already running.")
            self._stop = threading.Event()
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
                try:
                    result, logs, outcome = work()
                    with self._lock:
                        if outcome == "completed" and self._state["cancel_requested"]:
                            outcome = "cancelled"
                        if operation == "catalog" and outcome == "completed":
                            self._save_catalog(result["catalog"])
                        if operation == "scan" and outcome != "completed":
                            self._content = None
                            self._scan_key = None
                        self._state.update(result=result, logs=logs, state=outcome)
                        if outcome == "failed":
                            self._state["error"] = result.get("summary", "Operation failed.")
                        self._state["message"] = result.get("summary", outcome.capitalize())
                except Exception as error:
                    with self._lock:
                        cancelled = self._stop.is_set() and self._state["cancel_requested"]
                        self._state.update(
                            state="cancelled" if cancelled else "failed",
                            error=f"{type(error).__name__}: {error}",
                            message=str(error) or type(error).__name__,
                        )
                finally:
                    with self._lock:
                        started = self._state.pop("_started")
                        self._state["elapsed_seconds"] = max(0.0, time.monotonic() - started)
                        phase_started = self._state.pop("_phase_started", None)
                        if phase_started is not None:
                            self._state["phase_elapsed_seconds"] = max(0.0, time.monotonic() - phase_started)
                        if self._state["state"] in ("running", "cancelling"):
                            self._state.update(
                                state="failed", error="Operation worker stopped unexpectedly.",
                                message="Operation worker stopped unexpectedly.",
                            )
                        self._process = None
                        _operation_lock.release()

            try:
                threading.Thread(target=worker, name=f"studio-{operation}", daemon=True).start()
            except Exception:
                _operation_lock.release()
                self._state.update(state="failed", error="Cannot start worker thread.")
                self._state.pop("_started", None)
                raise
        return self.status()

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
            with self._lock:
                if self._state["cancel_requested"]:
                    return {"summary": "Content scan cancelled."}, logs, "cancelled"
                self._content = copy.deepcopy(content)
                self._scan_key = (selected.root, engine)
            return {"summary": "Content scan complete.", "content": content}, logs, "completed"

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

    def start(
        self, bar_root: str | Path, engine: str, game: str, map_name: str,
        players: list[dict], minutes: int, skip_smoke: bool = False,
    ) -> dict:
        """Start a match from the last successful scan for the same installation.

        Each player is an object with short_name, version, profile, faction and
        ally (0 or 1). The AI display name is taken from the scan, not the client.
        """
        players = copy.deepcopy(players)

        def work() -> tuple[dict, dict, str]:
            selected, engine_dir = self._selected(bar_root, engine)
            missing = discovery.engine_readiness(selected, engine)
            if missing:
                raise headless.MatchError("Engine is not ready: " + "; ".join(missing))
            with self._lock:
                content = copy.deepcopy(self._content)
                key = self._scan_key
            if content is None or key != (selected.root, engine):
                raise headless.MatchError("Scan installed content for the selected engine first.")
            if not isinstance(game, str) or game not in content["games"]:
                raise headless.MatchError("Select an installed game.")
            if not isinstance(map_name, str) or map_name not in [item["name"] for item in content["maps"]]:
                raise headless.MatchError("Select an installed map.")
            if type(minutes) is not int or type(skip_smoke) is not bool:
                raise headless.MatchError("Minutes must be an integer and skip_smoke must be a boolean.")
            if not isinstance(players, list):
                raise headless.MatchError("Players must be a list.")
            match_players = []
            for player in players:
                if not isinstance(player, dict):
                    raise headless.MatchError("Each player must be an object.")
                short_name, version = player.get("short_name"), player.get("version")
                profile, faction, ally = player.get("profile"), player.get("faction"), player.get("ally")
                if (not isinstance(short_name, str) or not isinstance(version, str)
                        or not isinstance(profile, str) or not isinstance(faction, str)
                        or type(ally) is not int or ally not in (0, 1)
                        or faction not in ("Armada", "Cortex", "Legion")):
                    raise headless.MatchError("Invalid player fields or faction.")
                ais = [ai for ai in content["ais"] if ai["short_name"] == short_name and ai["version"] == version]
                if len(ais) != 1:
                    raise headless.MatchError(f"AI {short_name}:{version} is not uniquely installed.")
                profiles = ais[0]["profiles"]
                if profile not in [item["key"] for item in profiles] and (profile or profiles):
                    raise headless.MatchError(f"Profile {profile!r} is not installed for {short_name}:{version}.")
                match_players.append(headless.Player(short_name, version, profile, faction, ally, ais[0]["name"]))
            headless.build_script(map_name, game, match_players, minutes)
            self._prepare_storage(engine_dir, selected.data)
            if self._stop.is_set():
                raise headless.MatchError("Stopped before the match started.")
            self._set_phase("smoke" if not skip_smoke else "match", "Starting headless match...")
            timed_out = threading.Event()

            def timeout() -> None:
                timed_out.set()
                self._stop.set()

            timer = threading.Timer(min(_MAX_MATCH_SECONDS, 600 + minutes * 60), timeout)
            timer.daemon = True
            timer.start()

            def progress(event: headless.MatchProgress) -> None:
                with self._lock:
                    if event.summary != "Skipped":
                        self._change_phase(event.phase)
                    self._state["progress"].append(asdict(event))
                    self._state["progress"] = self._state["progress"][-64:]
                    if event.message and not self._state["cancel_requested"]:
                        self._state["message"] = event.message

            def process_started(process: subprocess.Popen) -> None:
                with self._lock:
                    self._process = process

            try:
                summary, path = headless.run_match(
                    engine_dir, selected.data, self.local_data, map_name, game, match_players,
                    minutes, self._stop, progress, skip_smoke=skip_smoke, process_started=process_started,
                )
            except Exception as error:
                if timed_out.is_set():
                    raise headless.MatchError("Match exceeded its real-time deadline.") from error
                raise
            finally:
                timer.cancel()
            logs = {
                "directory": str(path), "output": str(path / "stdout.txt"),
                "infolog": str(path / "infolog.txt"), "result": str(path / "result.json"),
            }
            result = {"summary": summary, "path": str(path)}
            with self._lock:
                cancelled = self._state["cancel_requested"]
                events = list(self._state["progress"])
            for event in events:
                if event["phase"] == "smoke":
                    for line in event["message"].splitlines():
                        if line.startswith("Smoke logs: "):
                            logs["smoke_directory"] = line[len("Smoke logs: "):]
            if timed_out.is_set():
                result["summary"] += "\nMatch exceeded its real-time deadline."
                return result, logs, "failed"
            if cancelled:
                return result, logs, "cancelled"
            failed = any(event["summary"] == "Failed" for event in events)
            failed = failed or any(
                marker in summary for marker in (
                    "Engine failed (exit", "Stopped: the game did not confirm",
                    "Invalid winning sides reported:",
                )
            )
            return result, logs, "failed" if failed else "completed"

        return self._launch("match", work)
