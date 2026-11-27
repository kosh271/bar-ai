"""HTTP-independent, asynchronous headless match service for the workshop.

Create one service per server, passing the repository root to ``MatchService``.
Poll ``status`` after launching a scan or submitting games to the test queue.
Admission errors can raise MatchError, and worker errors appear in the status.
All snapshots are JSON-serializable.
"""

from __future__ import annotations

import codecs
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack, closing, nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from shutil import copyfileobj, rmtree
from typing import Callable

from . import discovery, headless, installation, project_history, settings, studio_experiments, studio_storage
from .unit_catalog import valid_catalog
from .system_capacity import game_capacity


_operation_lock = threading.Lock()
_MAX_MATCH_SECONDS = 6 * 60 * 60
_MAX_QUEUE_GAMES = 500
_SHUTDOWN_GRACE_SECONDS = 15
_SHUTDOWN_REAP_SECONDS = 4
# The raw-byte and row limits keep even a fully JSON-escaped page below 256 KiB.
_LOG_PAGE_BYTES = 32 * 1024
_LOG_PAGE_LINES = 512
_RESULT_METADATA_BYTES = 8 * 1024 * 1024
_LOG_FILES = {"infolog.txt", "stdout.txt", "result.json"}
_RUN_ID = re.compile(r"(?:match|smoke)-[a-zA-Z0-9_-]{1,64}")
_REPLAY_PATH = re.compile(r"demos/([^/\\:\x00-\x1f]+\.sdfz)", re.IGNORECASE)
_VALID_FACTIONS = settings.FACTIONS
_FAILURE_MARKERS = (
    "Engine failed (exit",
    "Stopped: the game did not confirm",
    "Invalid winning sides reported:",
    "Smoke test failed",
    "Match ended without a confirmed result",
)


@dataclass
class MatchBatch:
    id: str
    engine_dir: Path
    data: Path
    engine: str
    game: str
    minutes: int
    skip_smoke: bool
    record_demo: bool
    extra_units: bool
    start_options: dict | None
    series: dict | None
    projects: list[dict]
    players: list[headless.Player]
    provenance: dict = field(default_factory=dict)
    submitted: float = field(default_factory=time.monotonic)
    games: list[QueuedGame] = field(default_factory=list)
    completion: tuple[dict, dict, str] | None = None
    history_error: str | None = None


@dataclass(kw_only=True)
class GameExecution:
    row: dict = field(default_factory=dict)
    stop: headless.ProcessStop = field(default_factory=headless.ProcessStop)
    cancel_requested: bool = False
    started: float | None = None
    phase: str | None = None
    phase_started: float | None = None
    process: subprocess.Popen | None = None
    events: list[dict] = field(default_factory=list)
    logs: dict = field(default_factory=dict)
    live: dict | None = None
    terminal: headless.MatchTerminal | None = None
    finish_cause: str | None = None


@dataclass
class QueuedGame(GameExecution):
    batch: MatchBatch
    players: list[headless.Player]
    factions: list[str] | None
    path: Path | None = None
    outcome: str | None = None


class MatchService:
    """Share the engine operation slot between scans, experiments, and a test queue."""

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
        self._experiment_changed = threading.Condition(self._lock)
        self._queue_changed = threading.Condition(self._lock)
        self._submission_lock = threading.Lock()
        self._pending_games: deque[QueuedGame] = deque()
        self._active_games: dict[int, GameExecution] = {}
        self._batches: list[MatchBatch] = []
        self._processor_cores, self._memory_bytes = game_capacity()
        self._parallel_games = 1
        self._accept_matches = False
        self._admitting_to_queue: str | None = None
        self._history_lock = threading.Lock()
        self._legacy_run_index: dict[Path, tuple[tuple, dict]] = {}
        self._artifact_lock = threading.Lock()
        self._closed = False
        self._operation_done = threading.Event()
        self._operation_done.set()
        self._stop = headless.ProcessStop()
        self._state: dict = self._empty_state()
        self._content: dict | None = None
        self._scan_key: tuple[Path, str] | None = None
        self._invalidated_scans: set[tuple[Path, str]] = set()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "id": None, "operation": None, "state": "idle", "phase": None,
            "message": "", "progress": [], "result": None, "error": None,
            "logs": {}, "elapsed_seconds": None, "cancel_requested": False,
            "phase_elapsed_seconds": None, "engine": None, "series": None, "experiment": None,
            "scan_progress": None, "games": [],
        }

    def is_busy(self, operation: str | None = None) -> bool:
        with self._lock:
            return (self._state["state"] in ("running", "cancelling")
                    and (operation is None or self._state["operation"] == operation))

    def capacity(self) -> dict:
        with self._lock:
            return {"processor_cores": self._processor_cores, "memory_bytes": self._memory_bytes,
                    "parallel_games": self._parallel_games}

    def _validate_parallel_games(self, count: int) -> None:
        if type(count) is not int or not 0 <= count <= self._processor_cores:
            raise headless.MatchError(f"Parallel games must be an integer between 0 and {self._processor_cores}.")

    def set_parallel_games(self, count: int) -> dict:
        self._validate_parallel_games(count)
        with self._lock:
            self._parallel_games = count
            self._queue_changed.notify_all()
            self._experiment_changed.notify_all()
        return self.status()

    def status(self) -> dict:
        """Return an independent snapshot of the current or most recent operation."""
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            snapshot["parallel_games"] = self._parallel_games
            snapshot["active_games"] = len(self._active_games)
            snapshot["active_matchups"] = []
            started = snapshot.pop("_started", None)
            if started is not None:
                snapshot["elapsed_seconds"] = max(0.0, time.monotonic() - started)
                if snapshot["experiment"] and snapshot["experiment"]["state"] == "running":
                    snapshot["experiment"]["elapsed_seconds"] = round(snapshot["elapsed_seconds"], 2)
            phase_started = snapshot.pop("_phase_started", None)
            if phase_started is not None:
                snapshot["phase_elapsed_seconds"] = max(0.0, time.monotonic() - phase_started)
            if self._batches:
                snapshot["history_errors"] = [batch.history_error for batch in self._batches if batch.history_error]
                snapshot["series_scores"] = [
                    self._series_summary(batch, include_games=False) for batch in self._batches if batch.series
                ]
                if len(self._batches) == 1 and snapshot["result"] is None:
                    snapshot["series"] = copy.deepcopy(self._series_summary(self._batches[0]))
                elif snapshot["result"] is not None:
                    snapshot["series"] = snapshot["result"].get("series")
            now = time.monotonic()
            first = next(iter(self._active_games), None)
            for index, task in self._active_games.items():
                code = task.process.poll() if task.process is not None else None
                engine_state = "starting" if task.process is None else "running" if code is None else "exited"
                elapsed = max(0.0, now - task.started)
                state = (
                    "stopping" if task.cancel_requested else "smoke" if task.phase == "smoke"
                    else {"exited": "finalizing"}.get(engine_state, engine_state)
                )
                live = copy.deepcopy(task.live)
                if self._batches:
                    row = snapshot["games"][index - 1]
                    row.update(elapsed_seconds=elapsed, state=state, live=live)
                    if task.process is None:
                        row["pid"] = None
                    row["logs"] = copy.deepcopy(task.logs)
                else:
                    snapshot["active_matchups"].append({
                        **task.row, "index": index, "elapsed_seconds": elapsed, "state": state, "live": live,
                    })
                if index == first:
                    snapshot.update(
                        phase=task.phase, progress=copy.deepcopy(task.events), logs=copy.deepcopy(task.logs),
                        phase_elapsed_seconds=max(0.0, now - task.phase_started) if task.phase_started else None,
                        engine={"state": engine_state, "exit_code": code},
                    )
            return snapshot

    def cancel(self, expected_id: str | None = None) -> dict:
        """Request cooperative cancellation; poll status for the final outcome."""
        with self._lock:
            if self._state["state"] in ("running", "cancelling"):
                experiment = self._state["experiment"]
                if experiment is not None and experiment["state"] != "running":
                    raise headless.MatchError("The experiment has already finished.")
                if expected_id is not None and self._state["id"] != expected_id:
                    raise headless.MatchError("The requested operation is no longer running.")
                self._request_cancel_locked()
            elif not (expected_id == self._state["id"] and self._state["operation"] == "scan"):
                raise headless.MatchError("No scan or match is running.")
        return self.status()

    def _request_cancel_locked(self) -> None:
        self._state.update(state="cancelling", cancel_requested=True, message="Cancelling...")
        self._stop.set()
        while self._pending_games:
            task = self._pending_games.popleft()
            task.outcome = "cancelled"
            task.row["state"] = "not_run"
        for task in self._active_games.values():
            if task.terminal is None:
                task.cancel_requested = True
            task.stop.set()
        self._queue_changed.notify_all()
        self._experiment_changed.notify_all()

    def shutdown(self) -> bool:
        """Close admission and wait for the active operation and its native children."""
        with self._lock:
            self._closed = True
            if self._state["state"] in ("running", "cancelling"):
                self._request_cancel_locked()
            children = [self._stop, *(task.stop for task in self._active_games.values())]
        if self._operation_done.wait(_SHUTDOWN_GRACE_SECONDS):
            return True
        for stop in children:
            try:
                stop.kill_child()
            except OSError as error:
                print(f"Could not stop a Studio engine process: {error}", file=sys.stderr)
        return self._operation_done.wait(_SHUTDOWN_REAP_SECONDS)

    def cancel_game(self, queue_id: str, index: int) -> dict:
        with self._lock:
            if self._state["id"] != queue_id or not 1 <= index <= len(self._state["games"]):
                raise headless.MatchError("The requested game is no longer in this test queue.")
            task = self._active_games.get(index)
            if task is not None:
                if task.terminal is not None:
                    raise headless.MatchError("The requested game has already finished.")
                task.cancel_requested = True
                task.stop.set()
            else:
                task = next((item for item in self._pending_games if item.row["game_index"] == index), None)
                if task is None:
                    raise headless.MatchError("The requested game has already finished.")
                self._pending_games.remove(task)
                task.outcome = "cancelled"
                task.row.update(state="cancelled", summary="Cancelled before launch.")
                self._queue_changed.notify_all()
        return self.status()

    def _launch(self, operation: str, work: Callable[[], tuple[dict, dict, str]]) -> dict:
        with self._lock:
            self._launch_locked(operation, work)
        return self.status()

    def _acquire_operation_lease(self) -> ExitStack:
        with ExitStack() as stack:
            if not _operation_lock.acquire(blocking=False):
                raise headless.MatchError("Another workshop scan or match is already running.")
            stack.callback(_operation_lock.release)
            stack.enter_context(studio_storage.operation_lease(self.root, self.local_data))
            return stack.pop_all()

    def _launch_locked(self, operation: str, work: Callable[[], tuple[dict, dict, str]]) -> None:
        if self._closed:
            raise headless.MatchError("Studio is shutting down.")
        if self._state["state"] in ("running", "cancelling"):
            raise headless.MatchError("A scan or match is already running.")
        stop = headless.ProcessStop()
        state = self._empty_state()
        state.update(
            id=uuid.uuid4().hex, operation=operation, state="running",
            phase="preparing", message=f"Preparing {operation}...",
            _started=time.monotonic(),
        )
        lease = self._acquire_operation_lease()
        self._stop, self._state = stop, state
        self._operation_done.clear()
        self._batches.clear()
        self._accept_matches = False
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
                        cancelled = self._state["cancel_requested"]
                        scan_cancelled = cancelled and operation == "scan"
                        self._state.update(
                            state="cancelled" if cancelled else "failed",
                            error=None if scan_cancelled else f"{type(error).__name__}: {error}",
                            message="Content scan cancelled." if scan_cancelled else str(error) or type(error).__name__,
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
                        try:
                            lease.close()
                        finally:
                            self._operation_done.set()
                            if operation == "match":
                                self._queue_changed.notify_all()

        try:
            threading.Thread(target=worker, name=f"studio-{operation}", daemon=True).start()
        except BaseException:
            try:
                lease.close()
            finally:
                self._operation_done.set()
                self._state.update(state="failed", error="Cannot start worker thread.")
                self._state.pop("_started", None)
            raise

    def _publish_result(self, operation: str, result: dict, logs: dict, outcome: str) -> None:
        """Publish the result while holding the service lock."""
        if outcome == "completed" and self._state["cancel_requested"] and operation != "match":
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

    def storage(self, *, offset: int = 0, limit: int = 25) -> dict:
        studio_storage.check_page(offset, limit)
        # New runs do not publish an ID until their phase produces an artifact.
        active = self.is_busy()
        with self._artifact_lock:
            folder = self._matches_dir()
            rows = [studio_storage.inspect_run(child, active=active)
                    for child in sorted(folder.iterdir(), key=lambda path: path.name)
                    if _RUN_ID.fullmatch(child.name) and not installation._link(child) and child.is_dir()
                    ] if folder.is_dir() else []
        return {"total_bytes": sum(row["bytes"] for row in rows), "total_runs": len(rows),
                "protected_runs": sum(row["protected"] for row in rows), "runs": rows[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def pin_run(self, run_id: str, pinned: bool) -> dict:
        if type(pinned) is not bool:
            raise headless.MatchError("Pinned must be true or false.")
        run_id = self._run_id(run_id)
        with studio_storage.artifact_mutation_lease(self.root, self.local_data), self._artifact_lock:
            run = studio_storage.run_directory(self._matches_dir(), run_id)
            studio_storage.set_pin(run, pinned)
        return {"id": run_id, "pinned": pinned}

    def cleanup_runs(self, run_ids: list[str]) -> dict:
        if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= 100:
            raise headless.MatchError("Select 1 to 100 run directories to clean up.")
        run_ids = list(dict.fromkeys(self._run_id(run_id) for run_id in run_ids))
        with (self._submission_lock, self._acquire_operation_lease(),
              studio_storage.artifact_mutation_lease(self.root, self.local_data), self._artifact_lock):
            folder = self._matches_dir()
            paths = [studio_storage.run_directory(folder, run_id) for run_id in run_ids]
            return studio_storage.cleanup(paths)

    def read_log(self, run_id: str, filename: str, offset: int = 0) -> dict:
        run_id = self._run_id(run_id)
        if (not isinstance(filename, str) or filename not in _LOG_FILES
                or type(offset) is not int or not 0 <= offset <= 2**63 - 1):
            raise headless.MatchError("Invalid log file or byte offset.")
        try:
            with self._artifact_lock, headless.open_artifact(
                self.root, "data", "studio-matches", "matches", run_id, filename,
            ) as stream:
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
            with self._artifact_lock, headless.open_artifact(
                self.root, "data", "studio-matches", "matches", run_id, "result.json",
            ) as stream:
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
            if self._scan_key == (root, engine) and self._content is not None:
                return copy.deepcopy(self._content)
            data = root / "data"
            path = self._content_cache_path(data, engine)
            installation._check_links(self.root, path)
            return settings.load_content_cache(path, data, engine)

    def sync_installed_ai(
        self, bar_root: str | Path, engine: str, short_name: str,
        version: str, display_name: str, profiles: list[dict[str, str]],
    ) -> bool:
        """Update the scan's AI choices after a successful installation."""
        return self._sync_ai(bar_root, engine, short_name, version, {
            "short_name": short_name, "version": version, "name": display_name, "profiles": profiles,
        })

    def sync_removed_ai(self, bar_root: str | Path, engine: str, short_name: str, version: str) -> bool:
        return self._sync_ai(bar_root, engine, short_name, version, None)

    def _sync_ai(
        self, bar_root: str | Path, engine: str, short_name: str, version: str,
        installed: dict | None,
    ) -> bool:
        if not isinstance(bar_root, (str, Path)) or not str(bar_root).strip():
            raise headless.MatchError("BAR root is required.")
        if not isinstance(engine, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", engine) or engine in (".", ".."):
            raise headless.MatchError("Invalid engine folder.")
        root = Path(bar_root).expanduser().resolve()
        data = root / "data"
        cache_path = self._content_cache_path(data, engine)
        installation._check_links(self.root, cache_path)
        with self._lock:
            key = (root, engine)
            if key in self._invalidated_scans:
                return False
            current_scan = self._content is not None and self._scan_key == key
            if current_scan:
                content = copy.deepcopy(self._content)
            else:
                content = settings.load_content_cache(cache_path, data, engine)
            if content is None:
                return False
            ais = content["ais"]
            matches = [index for index, ai in enumerate(ais)
                       if (ai["short_name"], ai["version"]) == (short_name, version)]
            if installed is None and not matches:
                return False
            if matches:
                for index in reversed(matches):
                    del ais[index]
            if installed is not None:
                ais.insert(matches[0] if matches else len(ais), copy.deepcopy(installed))
            if not settings._content_valid(content):
                raise headless.MatchError("Updated AI profile metadata is invalid.")
            scan_result = self._state["result"] if current_scan and self._state["operation"] == "scan" else None
            if current_scan:
                self._content = content
                if scan_result and "content" in scan_result:
                    scan_result["content"] = content
            try:
                settings.save_content_cache(cache_path, data, engine, content)
            except settings.SettingsError:
                if not current_scan:
                    raise
                if scan_result and "content" in scan_result:
                    scan_result["summary"] += " Updated AI choices could not be saved for the next session."
                    self._state["message"] = scan_result["summary"]
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
        def progress(checked: int, total: int) -> None:
            with self._lock:
                self._state["scan_progress"] = {"checked": checked, "total": total}

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
            content = headless.scan_content(
                engine_dir, selected.data, scan_dir, self._stop, archive_cache=cache, progress=progress,
            )
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
        with self._artifact_lock:
            try:
                with headless.open_artifact(
                    self.root, "data", "studio-matches", "matches", rel, "result.json",
                ) as source:
                    raw = source.read(_RESULT_METADATA_BYTES + 1)
            except FileNotFoundError:
                return
            existing = json.loads(raw) if len(raw) <= _RESULT_METADATA_BYTES else None
            if not isinstance(existing, dict):
                raise headless.MatchError(f"Invalid result metadata in {rel}.")
            existing.update(metadata)
            descriptor, temp_name = tempfile.mkstemp(prefix=".result-", dir=path)
            temporary = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(existing, output, indent=2, ensure_ascii=False)
                    output.write("\n")
                os.replace(temporary, path / "result.json")
            finally:
                temporary.unlink(missing_ok=True)

    def _result_annotation_warning(self, path: Path | None, metadata: dict) -> str | None:
        try:
            self._enrich_result_json(path, metadata)
        except (OSError, ValueError, RecursionError, headless.MatchError) as error:
            return f"Could not annotate result for {path.name}: {error}"
        return None

    def _load_history_entries_unlocked(self) -> list[dict]:
        matches_dir = self._matches_dir()
        history_path = matches_dir / "history.json"
        installation._check_links(self.root, history_path)
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise headless.MatchError(f"Cannot read match history {history_path}: {error}") from error
        if (not isinstance(data, dict) or data.get("version", 1) != 1 or not isinstance(data.get("entries"), list)
                or any(not isinstance(entry, dict) for entry in data["entries"])):
            raise headless.MatchError(f"Invalid match history format in {history_path}.")
        return data["entries"]

    def _append_history(self, entry: dict) -> None:
        with self._history_lock:
            matches_dir = self._matches_dir()
            self.local_data.mkdir(parents=True, exist_ok=True)
            matches_dir.mkdir(exist_ok=True)
            history_path = matches_dir / "history.json"
            entries = self._load_history_entries_unlocked()
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
            with self._artifact_lock:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
                mtime = datetime.fromtimestamp(result_file.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        script_map, script_game, script_players = self._parse_legacy_script(directory / "script.txt")
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
        if payload.get("experiment_id") and isinstance(outcome, str):
            if outcome == "cancelled":
                state = "cancelled"
            elif outcome == "failed" or (outcome == "timeout" and not payload.get("simulated_limit")):
                state = "failed"
            elif outcome in {"side1", "side2", "draw", "timeout"}:
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
            "sweep_source": payload.get("sweep_source"),
            "start_options": payload.get("start_options"),
            "provenance": payload.get("provenance"),
            "series": None,
        }

    def history(
        self, *, project: str | None = None, profile: str | None = None,
        map_name: str | None = None, checkpoint: str | None = None, limit: int = 100,
        offset: int | None = None,
    ) -> list[dict] | dict:
        """Return persistent match and series history entries, safely bounded within local_data."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise headless.MatchError("History limit must be an integer between 1 and 500.")
        if offset is not None:
            studio_storage.check_page(offset, limit, maximum=500)
        with self._history_lock:
            entries = self._load_history_entries_unlocked()
            matches_dir = self._matches_dir()
            legacy_index: dict[Path, tuple[tuple, dict]] = {}
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
                    try:
                        result = headless.artifact_stat(child, "result.json")
                    except (OSError, headless.MatchError):
                        continue
                    try:
                        script = headless.artifact_stat(child, "script.txt")
                    except (OSError, headless.MatchError):
                        script = None
                    revision = (result.st_mtime_ns, result.st_size,
                                script.st_mtime_ns if script else None, script.st_size if script else None)
                    cached = self._legacy_run_index.get(child)
                    legacy = cached[1] if cached and cached[0] == revision else self._legacy_entry_from_dir(child)
                    if legacy is not None:
                        legacy_index[child] = (revision, legacy)
                        entries.append(legacy)
            self._legacy_run_index = legacy_index

        filtered = []
        for entry in entries:
            entry_players = [p for p in entry.get("players", []) if isinstance(p, dict)]
            entry_projects = [p for p in entry.get("projects", []) if isinstance(p, dict)]
            source = entry.get("sweep_source")
            source = source if isinstance(source, dict) else {}
            entry_maps = [m for m in entry.get("maps", []) if isinstance(m, str) and m]
            if not entry_maps and isinstance(entry.get("map"), str) and entry["map"]:
                entry_maps = [entry["map"]]
            if project:
                target = project.casefold()
                if not (
                    any(str(p.get("short_name", "")).casefold() == target for p in entry_players)
                    or any(str(p.get("project", "")).casefold() == target for p in entry_projects)
                    or str(source.get("project", "")).casefold() == target
                ):
                    continue
            if profile:
                target = profile.casefold()
                if (str(source.get("profile", "")).casefold() != target
                        and not any(str(p.get("profile", "")).casefold() == target for p in entry_players)):
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
        if offset is None:
            return copy.deepcopy(filtered[:limit])
        end = offset + limit
        return {"history": copy.deepcopy(filtered[offset:end]),
                "next_offset": end if end < len(filtered) else None}

    def _classify_game_outcome(
        self, summary: str, path: Path | None, timed_out: bool,
        cancelled: bool, swapped: bool, events: list[dict], *, terminal: headless.MatchTerminal | None = None,
    ) -> tuple[str, bool]:
        if cancelled:
            return "cancelled", False
        if timed_out:
            return "timeout", True
        if terminal is not None:
            if terminal.cause == "game_over":
                winning = terminal.winning_sides
                if winning is not None and len(winning) == 1:
                    return ("side2" if ((winning[0] == 0) == swapped) else "side1"), False
                return "draw", False
            if terminal.cause == "simulated_limit":
                return "timeout", False
            return terminal.cause, terminal.cause == "failed"
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
        minutes: int, *, skip_smoke: bool, task: GameExecution, record_demo: bool = False,
        extra_units: bool = False, real_time_deadline: float | None = None,
        start_options: dict | None = None,
    ) -> tuple[str, Path, bool, bool, list[dict]]:
        game_stop = task.stop
        phase = "match" if skip_smoke else "smoke"
        with self._lock:
            if self._stop.is_set() or game_stop.is_set():
                raise headless.MatchError("Stopped before the match started.")
            if real_time_deadline is not None and real_time_deadline <= time.monotonic():
                raise headless.MatchError("Match exceeded its real-time deadline.")
            task.phase = phase
            task.phase_started = time.monotonic()
        timed_out = threading.Event()

        def timeout() -> None:
            timed_out.set()
            game_stop.set()

        deadline = time.monotonic() + min(_MAX_MATCH_SECONDS, 600 + minutes * 60)
        if real_time_deadline is not None:
            deadline = min(deadline, real_time_deadline)
        budget = max(0.0, deadline - time.monotonic())
        if budget == 0:
            timeout()
        timer = threading.Timer(budget, timeout)
        timer.daemon = True
        timer.start()
        def progress(event: headless.MatchProgress) -> None:
            with self._lock:
                if event.terminal is not None:
                    task.terminal = event.terminal
                    return
                if event.live is not None:
                    task.live = event.live
                    return
                if event.summary != "Skipped" and event.phase != task.phase:
                    task.phase = event.phase
                    task.phase_started = time.monotonic()
                    task.process = None
                task.events.append(asdict(event))
                del task.events[:-64]
                if event.run_id and _RUN_ID.fullmatch(event.run_id):
                    task.logs["smoke_run_id" if event.phase == "smoke" else "run_id"] = event.run_id

        def process_started(process: subprocess.Popen) -> None:
            with self._lock:
                task.process = process
                if type(process.pid) is int:
                    task.row["pid"] = process.pid

        try:
            summary, path = headless.run_match(
                engine_dir, data, self.local_data, map_name, game, players, minutes, game_stop,
                progress, skip_smoke=skip_smoke, process_started=process_started, record_demo=record_demo,
                extra_units=extra_units, capture_stats=True, start_options=start_options,
            )
        except Exception as error:
            if timed_out.is_set():
                task.finish_cause = "wall_timeout"
                raise headless.MatchError("Match exceeded its real-time deadline.") from error
            raise
        finally:
            timer.cancel()
        with self._lock:
            terminal = task.terminal
            wall_timeout = terminal.finished_at >= deadline if terminal is not None else timed_out.is_set()
            if wall_timeout:
                task.finish_cause = "wall_timeout"
            elif terminal is not None:
                task.finish_cause = terminal.cause
            elif "Time limit reached (" in summary:
                task.finish_cause = "simulated_limit"
            return summary, path, wall_timeout, task.cancel_requested, list(task.events)

    def _experiment_workspace(self, experiment_id: str) -> Path:
        if not isinstance(experiment_id, str) or not re.fullmatch(r"[0-9a-f]{32}", experiment_id):
            raise headless.MatchError("Invalid experiment ID.")
        workspace = self.local_data / "experiments" / experiment_id
        installation._check_links(self.root, workspace)
        return workspace

    def _save_experiment(self, workspace: Path, experiment: dict, promotion: dict | None) -> None:
        with self._history_lock:
            path = workspace / "experiment.json"
            installation._check_links(self.root, path)
            descriptor, filename = tempfile.mkstemp(prefix=".experiment-", dir=workspace)
            temporary = Path(filename)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"experiment": experiment, "promotion": promotion}, stream, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            summary = workspace / "summary.json"
            try:
                installation._check_links(self.root, summary)
                settings._save_json(summary, {"record_mtime_ns": path.stat().st_mtime_ns,
                                              "entry": self._experiment_summary(experiment)}, "experiment summary")
            except (OSError, installation.InstallationError, settings.SettingsError):
                # The full experiment is authoritative and can supply a missing summary.
                pass

    def _read_experiment(self, experiment_id: str) -> dict | None:
        self._experiment_workspace(experiment_id)
        try:
            with headless.open_artifact(
                self.root, "data", "studio-matches", "experiments", experiment_id, "experiment.json",
            ) as stream:
                raw = stream.read(_RESULT_METADATA_BYTES + 1)
            saved = json.loads(raw) if len(raw) <= _RESULT_METADATA_BYTES else None
        except (OSError, ValueError, RecursionError, headless.MatchError):
            return None
        if (isinstance(saved, dict) and isinstance(saved.get("experiment"), dict)
                and saved["experiment"].get("id") == experiment_id):
            return saved
        return None

    def _experiment_history_entry(self, experiment_id: str, active: str | None) -> dict | None:
        saved = self._read_experiment(experiment_id)
        if saved is None:
            return None
        entry = saved["experiment"]
        if entry.get("state") == "running" and experiment_id != active:
            entry.update(state="interrupted", stop_reason="interrupted", paused=False, pause_requested=False,
                         promotable=False, best_candidate_id=None)
        return entry

    def experiment_details(self, experiment_id: str) -> dict:
        with self._lock:
            active = self._state["id"] if self._state["state"] in ("running", "cancelling") else None
        with self._history_lock:
            entry = self._experiment_history_entry(experiment_id, active)
        if entry is None:
            raise headless.MatchError("The saved experiment is unavailable.")
        self._hydrate_experiment_artifacts(entry)
        if entry.get("mode") == "sweep" and "promotion_policy" not in entry:
            self._refresh_legacy_promotion(entry)
        return entry

    def _hydrate_experiment_artifacts(self, entry: dict) -> None:
        # Read legacy artifacts only for the selected experiment.
        games = entry.get("games")
        for game in games[:100] if isinstance(games, list) else ():
            if not isinstance(game, dict):
                continue
            run_id = game.get("run_id")
            if (("side_totals" not in game or (entry.get("mode") == "sweep" and "telemetry_verified" not in game))
                    and isinstance(run_id, str) and _RUN_ID.fullmatch(run_id)):
                metadata = self._artifact_metadata(run_id)
                metrics = studio_experiments.telemetry_metrics(metadata)
                game.setdefault("side_totals", metrics["side_totals"])
                game.setdefault("telemetry_verified", metrics["telemetry_verified"])

    @staticmethod
    def _refresh_legacy_promotion(entry: dict) -> None:
        ranked = []
        for row in entry.get("rankings", []):
            valid_pairs, score = studio_experiments.paired_score(row["id"], entry.get("games", []))
            row["valid_pairs"], row["paired_win_rate"] = valid_pairs, score
            row["promotable"] = (entry.get("state") == "completed" and row.get("promotable") is True
                                 and valid_pairs >= studio_experiments.MIN_PROMOTION_PAIRS
                                 and score is not None and score > 0.5)
            if row["promotable"]:
                ranked.append(row)
        best = max(ranked, key=lambda row: (row["paired_win_rate"], row["valid_pairs"]), default=None)
        entry["best_candidate_id"] = best["id"] if best else None
        entry["promotable"] = best is not None

    def experiment_history(self) -> list[dict]:
        directory = self.local_data / "experiments"
        installation._check_links(self.root, directory)
        if not directory.is_dir():
            return []
        with self._lock:
            active = self._state["id"] if self._state["state"] in ("running", "cancelling") else None
        entries = []
        with self._history_lock:
            for workspace in directory.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", workspace.name) or installation._link(workspace):
                    continue
                entry = self._experiment_history_entry(workspace.name, active)
                if entry is not None:
                    entries.append(entry)
        return sorted(entries, key=lambda entry: str(entry.get("started_at", "")), reverse=True)[:100]

    @staticmethod
    def _experiment_summary(experiment: dict) -> dict:
        keys = ("id", "mode", "state", "started_at", "completed_at", "game", "scheduled_games", "completed_games",
                "coverage", "stop_reason", "best_candidate_id", "promotable")
        return {key: experiment[key] for key in keys if key in experiment}

    def experiment_history_page(self, *, offset: int, limit: int) -> dict:
        studio_storage.check_page(offset, limit)
        directory = self.local_data / "experiments"
        installation._check_links(self.root, directory)
        with self._lock:
            active = self._state["id"] if self._state["state"] in ("running", "cancelling") else None
        entries = []
        with self._history_lock:
            for workspace in directory.iterdir() if directory.is_dir() else ():
                if not re.fullmatch(r"[0-9a-f]{32}", workspace.name) or installation._link(workspace):
                    continue
                try:
                    path = workspace / "experiment.json"
                    installation._check_links(self.root, path)
                    mtime = path.stat().st_mtime_ns
                    with headless.open_artifact(self.root, "data", "studio-matches", "experiments",
                                                workspace.name, "summary.json") as stream:
                        saved = json.loads(stream.read(16384))
                    entry = saved.get("entry") if isinstance(saved, dict) and saved.get("record_mtime_ns") == mtime else None
                    if not isinstance(entry, dict) or entry.get("id") != workspace.name:
                        entry = None
                except (OSError, ValueError, RecursionError, headless.MatchError, installation.InstallationError):
                    entry = None
                if entry is None:
                    full = self._experiment_history_entry(workspace.name, active)
                    if full is None:
                        continue
                    entry = self._experiment_summary(full)
                if entry.get("state") == "running" and workspace.name != active:
                    entry.update(state="interrupted", stop_reason="interrupted", promotable=False, best_candidate_id=None)
                entries.append(entry)
        entries.sort(key=lambda entry: (str(entry.get("started_at", "")), entry["id"]), reverse=True)
        end = offset + limit
        return {"experiments": entries[offset:end], "next_offset": end if end < len(entries) else None}

    def pause_experiment(self, paused: bool, expected_id: str | None = None) -> dict:
        if type(paused) is not bool:
            raise headless.MatchError("paused must be a boolean.")
        with self._experiment_changed:
            experiment = self._state["experiment"]
            if self._state["state"] != "running" or experiment is None or experiment["state"] != "running":
                raise headless.MatchError("No experiment is running.")
            if expected_id is not None and self._state["id"] != expected_id:
                raise headless.MatchError("The requested experiment is no longer running.")
            experiment["pause_requested"] = paused
            self._experiment_changed.notify_all()
        return self.status()

    def delete_experiments(self, experiment_id: str | None = None) -> dict:
        directory = self.local_data / "experiments"
        installation._check_links(self.root, directory)
        with self._lock, ExitStack() as stack:
            current = self._state["id"] if self._state["operation"] == "experiment" else None
            busy = self._state["state"] in ("running", "cancelling")
            active = current if busy else None
            if active is not None and experiment_id in (None, active):
                raise headless.MatchError("Wait for the current experiment to finish before deleting its results.")
            if not busy:
                # Exclude an experiment started in another Studio process using this workspace.
                stack.enter_context(studio_storage.operation_lease(self.root, self.local_data))
            stack.enter_context(self._history_lock)
            if experiment_id is None:
                ids = [workspace.name for workspace in directory.iterdir()
                       if re.fullmatch(r"[0-9a-f]{32}", workspace.name) and not installation._link(workspace)
                       ] if directory.is_dir() else []
            else:
                ids = [experiment_id]
            removed = 0
            failed = []
            for saved_id in ids:
                if self._read_experiment(saved_id) is None:
                    if experiment_id is not None:
                        raise headless.MatchError("The saved experiment is unavailable.")
                    continue
                workspace = self._experiment_workspace(saved_id)
                record = workspace / "experiment.json"
                try:
                    # Keep the history record until all other files are gone so cleanup can be retried.
                    for child in workspace.iterdir():
                        if child == record:
                            continue
                        if not installation._link(child) and child.is_dir():
                            rmtree(child)
                        else:
                            child.unlink()
                    record.unlink()
                except OSError as error:
                    failed.append({"id": saved_id, "error": str(error)})
                    continue
                try:
                    workspace.rmdir()
                except OSError:
                    pass  # An empty directory can remain if another process still has it open.
                removed += 1
                if saved_id == current:
                    self._state = self._empty_state()
        return {"removed": removed, **({"failed": failed} if failed else {})}

    def _experiment_games(self, schedule: list, deadline: float, play: Callable):
        """Yield finished games while keeping eligible tournament slots occupied."""
        pending = deque(enumerate(schedule, 1))

        def changed(_future):
            with self._experiment_changed:
                self._experiment_changed.notify_all()

        with ThreadPoolExecutor(max_workers=max(1, min(len(schedule), self._processor_cores))) as pool:
            futures = {}
            try:
                while pending or futures:
                    submitted = []
                    with self._experiment_changed:
                        completed = [future for future in futures if future.done()]
                        for future in completed:
                            self._active_games.pop(futures.pop(future))
                        experiment = self._state["experiment"]
                        remaining = deadline - time.monotonic()
                        if self._stop.is_set() or remaining <= 0:
                            pending.clear()
                        slots = 0 if experiment["pause_requested"] else self._parallel_games
                        while pending and len(futures) < slots:
                            index, game = pending.popleft()
                            map_name, first, second = game
                            task = GameExecution(started=time.monotonic(), row={
                                "map": map_name, "side1": first.label, "side2": second.label,
                            })
                            future = pool.submit(play, index, *game, task)
                            self._active_games[index] = task
                            futures[future] = index
                            submitted.append(future)
                        experiment["paused"] = bool(pending and not futures and slots == 0)
                        if not submitted and not completed and (pending or futures):
                            self._experiment_changed.wait(remaining if remaining > 0 else None)
                    for future in submitted:
                        future.add_done_callback(changed)
                    for future in completed:
                        yield future.result()
            finally:
                with self._experiment_changed:
                    for index in futures.values():
                        self._active_games[index].stop.set()
                wait(futures)
                with self._experiment_changed:
                    for index in futures.values():
                        self._active_games.pop(index)

    def start_experiment(
        self, bar_root: str | Path, engine: str, game: str, maps: list[str], minutes: int,
        game_limit: int, seconds_limit: int, mode: str, players: list[dict], sweep: dict | None = None,
        *, parallel_games: int | None = None, start_options: dict | None = None, seed: int | None = None,
    ) -> dict:
        if parallel_games is not None:
            self._validate_parallel_games(parallel_games)
        if mode not in ("tournament", "sweep"):
            raise headless.MatchError("Choose tournament or sweep mode.")
        if type(game_limit) is not int or not 1 <= game_limit <= 100:
            raise headless.MatchError("The game count budget must be an integer from 1 to 100.")
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2**32):
            raise headless.MatchError("Choose an experiment seed from 0 to 4294967295.")
        seed = seed if seed is not None else int.from_bytes(os.urandom(4), "big")
        if type(seconds_limit) is not int or not 1 <= seconds_limit <= _MAX_MATCH_SECONDS:
            raise headless.MatchError(f"The real-time budget must be an integer from 1 to {_MAX_MATCH_SECONDS} seconds.")
        if type(minutes) is not int or not 0 < minutes <= 2147483647 // 1800:
            raise headless.MatchError("Choose a valid positive game-time limit.")
        if (not isinstance(maps, list) or not 1 <= len(maps) <= 32
                or any(not isinstance(name, str) or not name for name in maps) or len(set(maps)) != len(maps)):
            raise headless.MatchError("Choose 1 to 32 distinct maps.")
        if not isinstance(players, list) or not 2 <= len(players) <= 16:
            raise headless.MatchError("Choose 2 to 16 AI variants.")
        if mode == "sweep" and (len(players) != 2 or not isinstance(sweep, dict)):
            raise headless.MatchError("Provide a source profile and opponent for the sweep.")
        if start_options is not None:
            settings.validate_start_options(start_options)
            start_options = start_options.copy()
        maps, players, sweep = list(maps), copy.deepcopy(players), copy.deepcopy(sweep)

        def work() -> tuple[dict, dict, str]:
            with self._lock:
                experiment_id = self._state["id"]
                started = self._state["_started"]
            deadline = started + seconds_limit
            stamp = lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds")
            experiment = {
                "id": experiment_id, "mode": mode, "state": "running", "started_at": stamp(), "completed_at": None,
                "maps": maps, "minutes": minutes, "game": game, "engine": engine,
                "game_limit": game_limit, "seconds_limit": seconds_limit, "scheduled_games": 0,
                "completed_games": 0, "elapsed_seconds": 0.0, "paused": False, "pause_requested": False,
                "stop_reason": None, "rankings": [], "games": [], "promotable": False, "best_candidate_id": None,
                "seed": seed,
            }
            if start_options is not None:
                experiment["start_options"] = start_options
            if mode == "sweep":
                experiment["promotion_policy"] = {"minimum_valid_pairs": studio_experiments.MIN_PROMOTION_PAIRS,
                                                  "score_to_beat": 0.5}
            with self._lock:
                self._state["experiment"] = copy.deepcopy(experiment)
            workspace = self._experiment_workspace(experiment_id)
            workspace.mkdir(parents=True)
            promotion = None
            self._save_experiment(workspace, experiment, promotion)
            last_logs: dict = {}
            reason = None
            error_message = ""
            try:
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
                if not set(maps) <= {item["name"] for item in content["maps"]}:
                    raise headless.MatchError("Choose maps from the installed content scan.")
                if mode == "sweep":
                    source = players[0]
                    project, profile = sweep.get("project"), sweep.get("profile")
                    if (not isinstance(source, dict)
                            or not all(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key)
                                       for key in (project, profile))
                            or (source.get("short_name"), source.get("profile")) != (project, profile)
                            or source.get("faction") not in _VALID_FACTIONS
                            or type(source.get("ally")) is not int or source["ally"] not in (0, 1)):
                        raise headless.MatchError("Choose the saved AI project, profile, and faction to sweep.")
                    opponent = self._validate_players(players[1:], content, require_both_sides=False)[0]
                    selected_players = [headless.Player(project, installation.VERSION, profile,
                                                       source["faction"], source["ally"], project), opponent]
                else:
                    # Resolve dynamic choices when building the schedule, after validating the installed profiles.
                    validation_players = [
                        {**player, "faction": _VALID_FACTIONS[0]}
                        if isinstance(player, dict) and player.get("faction") in ("Rotate", "Random") else player
                        for player in players
                    ]
                    selected_players = self._validate_players(validation_players, content, require_both_sides=False)
                    faction_choices = [player["faction"] for player in players]
                    if len({(p.short_name, p.version, p.profile, faction)
                            for p, faction in zip(selected_players, faction_choices)}) != len(selected_players):
                        raise headless.MatchError("Each tournament AI profile and faction combination must be distinct.")
                self._prepare_storage(engine_dir, selected.data)
                snapshots = self._capture_project_snapshots(selected_players, selected.data, engine)
                experiment["projects"] = snapshots
                preparation = nullcontext()
                if mode == "sweep":
                    from . import studio_sweeps
                    preparation = studio_sweeps.prepare(
                        self.projects_root, workspace / "sweep", sweep, selected_players[0], engine_dir,
                        should_stop=lambda: self._stop.is_set() or time.monotonic() >= deadline,
                    )
                with preparation as prepared:
                    if prepared:
                        engine_dir = prepared.engine_dir
                        promotion = prepared.promotion
                        experiment["sweep"] = {
                            **{key: sweep[key] for key in ("project", "profile", "path", "line", "column", "values")},
                            "source_digest": promotion["source_digest"], "ai_sha256": promotion["ai_sha256"],
                        }
                        candidates = [studio_experiments.Candidate(item.id, item.label, item.player, True)
                                      for item in prepared.variants]
                        opponent = selected_players[1]
                        label = f"{opponent.name} · {opponent.profile or 'Default'} · {opponent.faction}"
                        candidates.append(studio_experiments.Candidate("baseline", label, opponent))
                    else:
                        candidates = [
                            studio_experiments.Candidate(
                                f"v{index}", f"{player.name} · {player.profile or 'Default'} · {faction}", player,
                                faction_policy=faction if faction in ("Rotate", "Random") else "",
                            )
                            for index, (player, faction) in enumerate(zip(selected_players, faction_choices), 1)
                        ]
                    total_games = (len(candidates) * (len(candidates) - 1) if mode == "tournament"
                                   else 2 * (len(candidates) - 1)) * len(maps)
                    games_to_schedule = max(1, game_limit - game_limit % 2)
                    schedule = list(islice(studio_experiments.matrix(candidates, maps, mode, seed=seed),
                                           games_to_schedule))
                    experiment["scheduled_games"] = len(schedule)
                    experiment["schedule"] = [
                        {"index": index, "pair_index": (index + 1) // 2, "map": map_name,
                         "side1": first.id, "side2": second.id,
                         "players": [asdict(replace(first.player, ally=0)), asdict(replace(second.player, ally=1))]}
                        for index, (map_name, first, second) in enumerate(schedule, 1)
                    ]
                    experiment["coverage"] = {"full_pairs": total_games // 2, "scheduled_pairs": len(schedule) // 2,
                                              "completed_pairs": 0, "scheduled_games": len(schedule),
                                              "completed_games": 0}
                    launch_provenance = studio_experiments.run_provenance(
                        engine_dir, maps, [candidate.player for candidate in candidates],
                        should_stop=lambda: self._stop.is_set() or time.monotonic() >= deadline,
                    )
                    experiment["provenance"] = copy.deepcopy(launch_provenance)
                    experiment["rankings"] = studio_experiments.rankings(candidates, [])
                    with self._lock:
                        for key in ("scheduled_games", "schedule", "coverage", "provenance", "rankings"):
                            self._state["experiment"][key] = copy.deepcopy(experiment[key])
                    self._save_experiment(workspace, experiment, promotion)

                    def play(index, map_name, first, second, task):
                        match_players = [replace(first.player, ally=0), replace(second.player, ally=1)]
                        summary = ""
                        run = None
                        logs = {}
                        try:
                            summary, run, timed_out, cancelled, events = self._run_game(
                                engine_dir, selected.data, map_name, game, match_players, minutes,
                                skip_smoke=False, real_time_deadline=deadline,
                                task=task, start_options=start_options,
                            )
                            outcome, _ = self._classify_game_outcome(
                                summary, run, timed_out, cancelled, False, events, terminal=task.terminal,
                            )
                        except Exception as error:
                            summary = str(error) or type(error).__name__
                            outcome = ("cancelled" if self._stop.is_set() else "timeout"
                                       if "real-time deadline" in summary else "failed")
                        if outcome == "completed":
                            outcome = "failed"
                        simulated_limit = outcome == "timeout" and task.finish_cause == "simulated_limit"
                        run_id = None
                        metadata = {}
                        if run is not None:
                            logs, _ = self._run_artifacts(run, events)
                            run_id = logs.get("run_id")
                            if run_id:
                                metadata = self._artifact_metadata(run_id)
                            game_provenance = copy.deepcopy(launch_provenance)
                            studio_experiments.record_archive_checksums(game_provenance, map_name,
                                                                       metadata.get("archive_checksums"))
                            history = {
                                "experiment_id": experiment_id, "experiment_game_index": index,
                                "game": game, "map": map_name, "engine": engine,
                                "players": [asdict(player) for player in match_players], "projects": snapshots,
                                "provenance": game_provenance,
                                "outcome": outcome, "simulated_limit": simulated_limit,
                                "elapsed_seconds": round(time.monotonic() - task.started, 2),
                            }
                            if task.finish_cause is not None:
                                history["finish_cause"] = task.finish_cause
                            if prepared:
                                history["sweep_source"] = {"project": sweep["project"], "profile": sweep["profile"]}
                            warning = self._result_annotation_warning(run, history)
                            if warning:
                                summary = f"{summary[:3500]}\n{warning}"
                        metrics = studio_experiments.telemetry_metrics(metadata)
                        if outcome == "failed" and "smoke_passed" not in metadata:
                            metrics["engine_error"] = True
                        return {
                            "index": index, "pair_index": (index + 1) // 2, "map": map_name,
                            "side1": first.id, "side2": second.id,
                            "side_labels": [first.label, second.label],
                            "factions": [player.faction for player in match_players],
                            "archive_checksums": metadata.get("archive_checksums", {}),
                            "outcome": outcome, "run_id": run_id, "summary": summary[:4000],
                            "simulated_limit": simulated_limit,
                            "elapsed_seconds": round(time.monotonic() - task.started, 2), **metrics,
                        }, logs

                    with closing(self._experiment_games(schedule, deadline, play)) as results:
                        for result, logs in results:
                            if logs:
                                last_logs = logs
                            experiment["games"].append(result)
                            experiment["games"].sort(key=lambda row: row["index"])
                            studio_experiments.record_archive_checksums(experiment["provenance"], result["map"],
                                                                       result["archive_checksums"])
                            experiment["completed_games"] += int(result["outcome"] != "cancelled")
                            finished = {row["index"] for row in experiment["games"] if row["outcome"] != "cancelled"}
                            experiment["coverage"].update(
                                completed_games=len(finished),
                                completed_pairs=sum(index + 1 in finished for index in finished if index % 2),
                            )
                            experiment["rankings"] = studio_experiments.rankings(candidates, experiment["games"])
                            experiment["elapsed_seconds"] = round(time.monotonic() - started, 2)
                            self._save_experiment(workspace, experiment, promotion)
                            with self._lock:
                                for key in ("coverage", "provenance", "games", "rankings", "completed_games", "elapsed_seconds"):
                                    self._state["experiment"][key] = copy.deepcopy(experiment[key])
                    if reason is None:
                        reason = ("time_limit" if time.monotonic() >= deadline else
                                  "game_limit" if len(schedule) < total_games else "completed")
                    if all(item["outcome"] == "failed" for item in experiment["games"]) and experiment["games"]:
                        reason = "failed"
                        error_message = experiment["games"][-1]["summary"].splitlines()[0]
            except (headless.MatchError, ValueError, OSError, discovery.DiscoveryError,
                    installation.InstallationError, settings.SettingsError) as error:
                reason = ("cancelled" if self._stop.is_set() else "time_limit" if time.monotonic() >= deadline else "failed")
                error_message = str(error)
            with self._lock:
                if self._stop.is_set():
                    reason = "cancelled"
                outcome = reason if reason in ("cancelled", "failed") else "completed"
                if outcome != "completed":
                    for row in experiment["rankings"]:
                        row["promotable"] = False
                winner = max((row for row in experiment["rankings"] if row["promotable"]),
                             key=lambda row: (row["paired_win_rate"], row["valid_pairs"]), default=None)
                best = winner["id"] if winner else None
                experiment.update(state=outcome, stop_reason=reason, completed_at=stamp(),
                                  elapsed_seconds=round(time.monotonic() - started, 2), paused=False,
                                  pause_requested=False, best_candidate_id=best, promotable=best is not None)
                self._save_experiment(workspace, experiment, promotion)
                self._state["experiment"] = copy.deepcopy(experiment)
            summary = {
                "failed": error_message or "Experiment failed.", "cancelled": "Experiment cancelled.",
                "time_limit": "Experiment reached its real-time budget.",
                "game_limit": "Experiment reached its game-count budget.", "completed": "Experiment complete.",
            }[reason]
            return {"summary": summary, "experiment": experiment}, last_logs, outcome

        with self._lock:
            self._launch_locked("experiment", work)
            if parallel_games is not None:
                self._parallel_games = parallel_games
        return self.status()

    def promote_experiment(self, experiment_id: str, candidate_id: str, name: str) -> dict:
        from . import studio_sweeps
        workspace = self._experiment_workspace(experiment_id)
        with self._history_lock:
            saved = self._read_experiment(experiment_id)
            if saved is None or not isinstance(saved.get("promotion"), dict):
                raise headless.MatchError("The experiment has no saved variant to promote.")
            experiment = saved["experiment"]
            if "promotion_policy" not in experiment:
                self._hydrate_experiment_artifacts(experiment)
            pairs, rate = studio_experiments.paired_score(candidate_id, experiment.get("games", []))
            if pairs < studio_experiments.MIN_PROMOTION_PAIRS or rate is None or rate <= 0.5:
                raise headless.MatchError("Promotion needs a majority from at least two clean mirrored pairs.")
            if experiment.get("state") != "completed" or not any(
                row.get("id") == candidate_id and row.get("promotable") is True
                for row in experiment.get("rankings", [])
            ):
                raise headless.MatchError("Choose an evaluated variant from a completed experiment.")
            profile = studio_sweeps.promote(self.projects_root, workspace / "sweep", saved["promotion"], candidate_id, name)
            return {"project": profile.parent.parent.name, "profile": profile.name}

    def _prepare_batch(
        self, bar_root: str | Path, engine: str, game: str, map_name: str,
        players: list[dict], minutes: int, skip_smoke: bool, series: dict | None,
        record_demo: bool, extra_units: bool, start_options: dict | None,
    ) -> MatchBatch:
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
        roster = self._validate_players(players, content)
        series = self._validate_series(series, map_name, content)
        self._prepare_storage(engine_dir, selected.data)
        projects = self._capture_project_snapshots(roster, selected.data, engine)
        batch = MatchBatch(uuid.uuid4().hex, engine_dir, selected.data, engine, game, minutes,
                           skip_smoke, record_demo, extra_units, start_options, series, projects, roster)
        maps = series["maps"] if series else [map_name]
        batch.provenance = studio_experiments.run_provenance(engine_dir, maps, roster)
        pairs = series["faction_pairs"] if series else []
        for idx in range(series["games"] if series else 1):
            pair = pairs[idx % len(pairs)] if pairs else None
            swapped = bool(series and series["swap_sides"] and idx % 2)
            game_players = [
                replace(player, faction=pair[player.ally] if pair else player.faction,
                        ally=1 - player.ally if swapped else player.ally)
                for player in roster
            ]
            game_map = maps[idx % len(maps)]
            headless.build_script(game_map, game, game_players, minutes, record_demo,
                                  extra_units=extra_units, start_options=start_options)
            row = {
                "batch_id": batch.id, "batch_game": idx + 1, "batch_size": series["games"] if series else 1,
                "game": game, "map": game_map, "swapped": swapped,
                "game_time_limit_seconds": minutes * 60,
                "players": [asdict(player) for player in game_players], "state": "queued",
                "pid": None, "elapsed_seconds": None, "winner_side": None,
            }
            batch.games.append(QueuedGame(batch, game_players, pair, row=row))
        return batch

    def start(
        self, bar_root: str | Path, engine: str, game: str, map_name: str,
        players: list[dict], minutes: int, skip_smoke: bool = False,
        series: dict | None = None, *, record_demo: bool = False, extra_units: bool = False,
        parallel_games: int | None = None, start_options: dict | None = None,
    ) -> dict:
        """Queue a match or benchmark series from the last successful scan."""
        if type(record_demo) is not bool:
            raise headless.MatchError("record_demo must be a boolean.")
        if type(extra_units) is not bool:
            raise headless.MatchError("extra_units must be a boolean.")
        if start_options is not None:
            settings.validate_start_options(start_options)
            start_options = start_options.copy()
        if parallel_games is not None:
            self._validate_parallel_games(parallel_games)
        with self._submission_lock:
            with self._queue_changed:
                while (not self._closed and self._state["operation"] == "match"
                       and self._state["state"] == "running" and not self._accept_matches):
                    self._queue_changed.wait()
                if self._closed:
                    raise headless.MatchError("Studio is shutting down.")
                if self._state["state"] in ("running", "cancelling"):
                    if self._state["operation"] != "match" or self._state["state"] != "running":
                        raise headless.MatchError("Wait for the current operation to finish before queuing games.")
                    self._admitting_to_queue = self._state["id"]
            try:
                try:
                    batch = self._prepare_batch(bar_root, engine, game, map_name, players, minutes, skip_smoke,
                                                series, record_demo, extra_units, start_options)
                except (headless.MatchError, OSError, ValueError) as error:
                    if self._admitting_to_queue or self.is_busy():
                        raise
                    # Preserve async status reporting for a rejected standalone run.
                    def rejected(failure: Exception = error) -> tuple[dict, dict, str]:
                        raise failure

                    return self._launch("match", rejected)

                with self._queue_changed:
                    if self._closed:
                        raise headless.MatchError("Studio is shutting down.")
                    if self._admitting_to_queue:
                        if self._state["id"] != self._admitting_to_queue or self._state["state"] != "running":
                            raise headless.MatchError("The test queue was cancelled before the games could be added.")
                        if len(self._state["games"]) + len(batch.games) > _MAX_QUEUE_GAMES:
                            raise headless.MatchError(f"This queue holds up to {_MAX_QUEUE_GAMES} games.")
                    else:
                        self._launch_locked("match", self._run_queue)
                        self._accept_matches = True
                    if parallel_games is not None:
                        self._parallel_games = parallel_games
                    self._batches.append(batch)
                    submission = {
                        "queue_id": self._state["id"], "batch_id": batch.id,
                        "first_game_index": len(self._state["games"]) + 1, "game_count": len(batch.games),
                    }
                    for task in batch.games:
                        task.row["game_index"] = len(self._state["games"]) + 1
                        self._state["games"].append(task.row)
                        self._pending_games.append(task)
            finally:
                with self._queue_changed:
                    self._admitting_to_queue = None
                    self._queue_changed.notify_all()
        return {**self.status(), "submission": submission}

    def _run_queue(self) -> tuple[dict, dict, str]:
        while True:
            with self._queue_changed:
                while self._pending_games and len(self._active_games) < self._parallel_games:
                    task = self._pending_games.popleft()
                    index = task.row["game_index"]
                    self._active_games[index] = task
                    task.started = time.monotonic()
                    try:
                        threading.Thread(target=self._play_queued_game, args=(task,),
                                         name=f"studio-game-{index}", daemon=True).start()
                    except RuntimeError as error:
                        self._active_games.pop(index)
                        task.outcome = "failed"
                        task.row.update(state="failed", summary=str(error))
                batch = next((batch for batch in self._batches if batch.completion is None
                              and all(task.outcome is not None for task in batch.games)), None)
                if batch is None:
                    if self._pending_games or self._active_games or (self._admitting_to_queue and not self._stop.is_set()):
                        self._queue_changed.wait()
                        continue
                    self._accept_matches = False
                    if len(self._batches) == 1:
                        return self._batches[0].completion
                    outcomes = [batch.completion[2] for batch in self._batches]
                    state = (
                        "cancelled" if self._stop.is_set() or all(outcome == "cancelled" for outcome in outcomes)
                        else "failed" if all(outcome == "failed" for outcome in outcomes)
                        else "completed"
                    )
                    return {"summary": f"Test queue {state}."}, {}, state
            try:
                completion = self._record_batch(batch)
            except Exception as error:
                with self._lock:
                    batch.history_error = f"Could not save match history: {error}"
                completion = {"summary": batch.history_error}, {}, "failed"
            with self._lock:
                batch.completion = completion

    def _play_queued_game(self, task: QueuedGame) -> None:
        batch, row = task.batch, task.row
        path = None
        telemetry = None
        logs = task.logs
        try:
            summary, path, timed_out, cancelled, events = self._run_game(
                batch.engine_dir, batch.data, row["map"], batch.game, task.players, batch.minutes,
                skip_smoke=batch.skip_smoke, record_demo=batch.record_demo, extra_units=batch.extra_units, task=task,
                start_options=batch.start_options,
            )
            if timed_out:
                summary = (summary + "\nMatch exceeded its real-time deadline.").strip()
            outcome, _ = self._classify_game_outcome(
                summary, path, timed_out, cancelled, row["swapped"], events, terminal=task.terminal,
            )
            logs, telemetry = self._run_artifacts(path, events)
        except (Exception, SystemExit) as error:
            summary = str(error) or "Operation worker stopped unexpectedly."
            outcome = ("cancelled" if task.cancel_requested else "timeout"
                       if "real-time deadline" in summary else "failed")
        with self._queue_changed:
            # Cancellation can arrive while the worker reads its final artifacts.
            if task.cancel_requested and outcome != "timeout":
                outcome = "cancelled"
            winner = int(outcome[-1]) if outcome in ("side1", "side2") else None
            row.update(
                state="completed" if winner else outcome,
                winner_side=3 - winner if winner and row["swapped"] else winner,
                elapsed_seconds=max(0.0, time.monotonic() - task.started),
                summary=summary, logs=logs, telemetry=telemetry,
            )
            if task.finish_cause is not None:
                row["finish_cause"] = task.finish_cause
            task.path, task.outcome = path, outcome
            self._active_games.pop(row["game_index"])
            self._state.update(phase=task.phase, progress=task.events, logs=logs)
            if task.phase_started is not None:
                self._state["phase_elapsed_seconds"] = time.monotonic() - task.phase_started
            self._queue_changed.notify_all()

    @staticmethod
    def _series_summary(batch: MatchBatch, *, include_games: bool = True) -> dict | None:
        if batch.series is None:
            return None
        series = batch.series
        counts = {key: 0 for key in ("side1_wins", "side2_wins", "draws", "timeouts", "failures", "cancelled")}
        keys = {"side1": "side1_wins", "side2": "side2_wins", "draw": "draws",
                "timeout": "timeouts", "cancelled": "cancelled"}
        games = []
        current = None
        for task in batch.games:
            row = task.row
            if task.started is not None:
                current = row
            if task.outcome is None or row["state"] == "not_run":
                continue
            counts[keys.get(task.outcome, "failures")] += 1
            if include_games:
                games.append({
                    "game_index": row["batch_game"], "map": row["map"], "swapped": row["swapped"],
                    "factions": task.factions, "outcome": task.outcome,
                    "elapsed_seconds": round(row["elapsed_seconds"] or 0.0, 2),
                    "summary": row.get("summary", ""), "run_dir": row.get("logs", {}).get("run_id"),
                    "players": row["players"],
                })
        completed = sum(counts.values()) - counts["cancelled"]
        if not include_games:
            return {
                "first_game": batch.games[0].row["game_index"], "last_game": batch.games[-1].row["game_index"],
                "total_games": series["games"], "completed_games": completed, "swap_sides": series["swap_sides"],
                **counts,
            }
        return {
            "id": batch.id, "total_games": series["games"], "completed_games": completed,
            "current_game": current["batch_game"] if current else 0, "current_map": current["map"] if current else None,
            "swapped": current["swapped"] if current else False,
            "swap_sides": series["swap_sides"], "maps": series["maps"], "faction_pairs": series["faction_pairs"],
            "games": games, **counts,
        }

    def _record_batch(self, batch: MatchBatch) -> tuple[dict, dict, str]:
        series = self._series_summary(batch)
        last = next((task for task in reversed(batch.games) if task.path is not None), batch.games[0])
        logs = last.row.get("logs", {})
        telemetry = last.row.get("telemetry")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        latest = max((task.started + (task.row["elapsed_seconds"] or 0) for task in batch.games
                      if task.started is not None), default=time.monotonic())
        duration = round(max(0.0, latest - batch.submitted), 2)
        for task in batch.games:
            run_id = self._relative_run_dir(task.path)
            if run_id is not None:
                metadata = self._artifact_metadata(run_id)
                studio_experiments.record_archive_checksums(batch.provenance, task.row["map"],
                                                           metadata.get("archive_checksums"))
        base = {"timestamp": timestamp, "engine": batch.engine, "game": batch.game, "projects": batch.projects,
                "start_options": batch.start_options, "provenance": batch.provenance}
        warnings = []
        if series is not None:
            cancelled = any(task.outcome == "cancelled" for task in batch.games)
            state = "cancelled" if cancelled else "failed" if series["failures"] == len(batch.games) else "completed"
            outcome = (
                f"Side 1: {series['side1_wins']}W · Side 2: {series['side2_wins']}W · Draws: {series['draws']} · "
                f"Timeouts: {series['timeouts']} · Failures: {series['failures']}"
            )
            header = f"Series {state} ({series['completed_games']}/{series['total_games']} games)"
            summary = "\n".join([f"{header}: {outcome}", *(
                f"Game {row['game_index']} ({row['map']}{', swapped' if row['swapped'] else ''}): "
                f"{row['outcome']} ({row['elapsed_seconds']:.1f}s)" for row in series["games"]
            )])
            for task in batch.games:
                if task.path is None:
                    continue
                row = task.row
                warning = self._result_annotation_warning(task.path, {
                    **base, "map": row["map"], "players": row["players"], "outcome": task.outcome,
                    "elapsed_seconds": round(row["elapsed_seconds"], 2), "series_id": batch.id,
                    "game_index": row["batch_game"], "swapped": row["swapped"],
                    **({"finish_cause": task.finish_cause} if task.finish_cause is not None else {}),
                })
                if warning:
                    warnings.append(warning)
            maps = series["maps"]
        else:
            state = (
                "cancelled" if last.outcome == "cancelled"
                else "failed" if last.outcome == "failed" or (
                    last.outcome == "timeout" and "real-time deadline" in last.row.get("summary", "")
                )
                else "completed"
            )
            summary = last.row.get("summary", "Cancelled before launch.")
            outcome = (
                last.outcome.capitalize() if state != "completed" and last.path is None
                else "Timeout" if last.outcome == "timeout" and state == "failed"
                else "Cancelled" if state == "cancelled" else summary.splitlines()[0] if summary else last.outcome
            )
            maps = [last.row["map"]]
            warning = self._result_annotation_warning(last.path, {
                **base, "map": maps[0], "players": last.row["players"], "outcome": outcome, "elapsed_seconds": duration,
                **({"finish_cause": last.finish_cause} if last.finish_cause is not None else {}),
            })
            if warning:
                warnings.append(warning)
        try:
            self._append_history({
                **base, "id": batch.id, "kind": "series" if series is not None else "match", "state": state,
                "map": ", ".join(dict.fromkeys(maps)), "maps": list(maps), "minutes": batch.minutes,
                "skip_smoke": batch.skip_smoke, "outcome": outcome, "elapsed_seconds": duration, "summary": summary,
                "run_dir": self._relative_run_dir(last.path), "players": [asdict(player) for player in batch.players],
                "series": series,
            })
        except (OSError, ValueError, RecursionError, headless.MatchError, installation.InstallationError) as error:
            warnings.append(f"Could not save match history: {error}")
        if warnings:
            with self._lock:
                batch.history_error = "\n".join(warnings)
            summary = "\n".join([summary, *warnings])
        result = {"summary": summary, "path": str(last.path) if last.path is not None else ""}
        if series is not None:
            result["series"] = series
        if telemetry is not None:
            result["telemetry"] = telemetry
        return result, logs, state
