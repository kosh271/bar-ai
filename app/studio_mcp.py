"""Expose a running local BAR AI Studio instance to MCP clients over stdio."""

import argparse
import json
import re
import threading
from http.client import HTTPConnection, HTTPException
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import uuid4


DEFAULT_PORT = 8765


class StudioError(Exception):
    """An unavailable Studio instance or a rejected Studio API request."""


class StudioClient:
    def __init__(self, port: int) -> None:
        self.port = port
        self.client_id = f"mcp-{uuid4().hex}"
        self._stop = threading.Event()
        self._heartbeat = threading.Thread(target=self._keep_alive, daemon=True)
        self._connected = False

    def __enter__(self) -> "StudioClient":
        self._heartbeat.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._heartbeat.join()
        if self._connected:
            try:
                self.request("/api/client/close", {"client_id": self.client_id})
            except StudioError:
                pass

    def _keep_alive(self) -> None:
        while not self._stop.is_set():
            try:
                self.request("/api/client/heartbeat", {"client_id": self.client_id})
            except StudioError:
                self._connected = False
            else:
                self._connected = True
            self._stop.wait(20 if self._connected else 2)

    def request(self, path: str, payload: dict | None = None, **query: str | int) -> dict:
        # The browser uses this same local bootstrap. Never evaluate its JavaScript
        # or follow redirects, and keep the token out of MCP results and logs.
        host = f"127.0.0.1:{self.port}"
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        sending_request = False
        try:
            connection.request("GET", "/session.js")
            with connection.getresponse() as response:
                body = response.read(512)
                expected = (
                    rb'if \(location.origin === "http://' + re.escape(host.encode("ascii"))
                    + rb'"\) window.WORKSHOP_TOKEN = "([A-Za-z0-9_-]{43})";\n'
                )
                token = re.fullmatch(expected, body) if response.status == 200 else None
            if token is None:
                raise StudioError(f"No BAR AI Studio session was found at http://{host}/.")
            headers = {"X-Workshop-Token": token[1].decode("ascii")}
            if query:
                path += "?" + urlencode(query)
            if payload is None:
                connection.request("GET", path, headers=headers)
            else:
                # Starting work and writing profiles can include disk-heavy synchronous preparation.
                connection.timeout = 5 if path.startswith("/api/client/") else 60
                if connection.sock is not None:
                    connection.sock.settimeout(connection.timeout)
                headers["Content-Type"] = "application/json"
                sending_request = True
                connection.request("POST", path, body=json.dumps(payload, ensure_ascii=True), headers=headers)
            with connection.getresponse() as response:
                try:
                    result = json.loads(response.read())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise StudioError(f"Studio returned an invalid response (HTTP {response.status}).") from None
                if not isinstance(result, dict):
                    raise StudioError("Studio returned an invalid response.")
                if response.status != 200:
                    raise StudioError(result.get("error") or f"Studio returned HTTP {response.status}.")
                return result
        except (OSError, HTTPException):
            if sending_request:
                raise StudioError(
                    "Studio did not confirm the request. Check the operation or workspace state before retrying."
                ) from None
            raise StudioError(
                f"Could not reach BAR AI Studio at http://{host}/. Start it with python -m app --port {self.port}."
            ) from None
        finally:
            connection.close()

    def installation(self) -> dict:
        settings = self.request("/api/state").get("settings")
        if settings is None:
            raise StudioError("Choose and save a BAR installation in Studio before using this tool.")
        return {"bar_root": settings["bar_root"], "engine": settings["engine"]}

    def profile_files(self, project: str, profile: str) -> list[dict]:
        return self.request("/api/profile", project=project, profile=profile)["files"]


def create_server(studio: StudioClient):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    from pydantic import BaseModel, ConfigDict

    class Player(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        short_name: str
        version: str = ""
        profile: str = ""
        faction: Literal["Armada", "Cortex", "Legion", "Rotate", "Random"]
        ally: Literal[0, 1]

    class Sweep(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        project: str
        profile: str
        path: str
        line: int
        column: int
        values: list[int | float]
        sha256: str | None = None

    mcp = FastMCP(
        "BAR AI Studio",
        log_level="WARNING",
        instructions=(
            "Work with local BAR AI projects and headless game simulations. Use list_projects "
            "for workspace profile keys and get_installed_content for match AI identities, "
            "installed game names, and map names. Poll get_run_status after starting a scan, "
            "match, or experiment."
        ),
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    starts_work = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    changes_state = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    @mcp.tool(annotations=read_only)
    def list_projects() -> dict[str, Any]:
        """List Studio projects, profile keys and names, and workspace issues."""
        state = studio.request("/api/state")
        return {key: state[key] for key in ("projects", "project_issues")}

    @mcp.tool(annotations=read_only)
    def list_profile_files(project: str, profile: str) -> dict[str, Any]:
        """List the editable file paths and revision hashes in a workspace profile."""
        files = studio.profile_files(project, profile)
        return {"files": [{"path": file["path"], "sha256": file["sha256"]} for file in files]}

    @mcp.tool(annotations=read_only)
    def read_profile_file(project: str, profile: str, path: str,
                          start_line: int = 1, line_count: int = 100) -> dict[str, Any]:
        """Read a profile file by path, with one-based line paging and its full-file revision hash."""
        if start_line < 1 or not 1 <= line_count <= 500:
            raise StudioError("Use a positive start_line and a line_count from 1 to 500.")
        file = next((file for file in studio.profile_files(project, profile) if file["path"] == path), None)
        if file is None:
            raise StudioError("The path is not an editable file in the selected project profile.")
        lines = re.findall(r"[^\r\n]*(?:\r\n?|\n)|[^\r\n]+", file["text"])
        next_line = start_line + line_count
        return {
            "path": path, "sha256": file["sha256"], "text": "".join(lines[start_line - 1:next_line - 1]),
            "total_lines": len(lines), "next_line": next_line if next_line <= len(lines) else None,
        }

    @mcp.tool(annotations=changes_state)
    def save_profile_file(project: str, profile: str, path: str, sha256: str, text: str) -> dict[str, Any]:
        """Replace an existing workspace profile file with complete text, given its last-read SHA-256 revision."""
        edited = {"path": path, "sha256": sha256, "text": text}
        result = studio.request("/api/save", {"project": project, "profile": profile, "files": [edited]})
        file = next(file for file in result["files"] if file["path"] == path)
        return {"path": path, "sha256": file["sha256"]}

    @mcp.tool(annotations=read_only)
    def get_installed_content() -> dict[str, Any]:
        """Get games, maps, and installed AI variants from Studio's saved scan for the current engine."""
        installation = studio.installation()
        result = studio.request("/api/match/cache", installation)
        return {"engine": installation["engine"], "scan_required": result["content"] is None, **result}

    @mcp.tool(annotations=starts_work)
    def scan_installed_content() -> dict[str, Any]:
        """Start a BAR content scan using Studio's saved installation. Poll get_run_status for completion."""
        return _status(studio.request("/api/match/scan", studio.installation()))

    @mcp.tool(annotations=read_only)
    def find_units(query: str, limit: int = 20) -> dict[str, Any]:
        """Search unit IDs and names in Studio's most recently built unit catalog."""
        if not 1 <= limit <= 100:
            raise StudioError("Use a limit from 1 to 100.")
        catalog = studio.request("/api/catalog/latest")["catalog"]
        if catalog is None:
            return {"game": None, "units": [], "catalog_available": False}
        needle = query.casefold()
        matches = [
            unit for unit in catalog["units"]
            if needle in unit["id"].casefold() or needle in unit["name"].casefold()
        ]
        return {
            "game": catalog["game"], "units": matches[:limit],
            "total_matches": len(matches), "catalog_available": True,
        }

    @mcp.tool(annotations=starts_work)
    def build_unit_catalog(game: str) -> dict[str, Any]:
        """Build Studio's searchable unit catalog for an installed game."""
        return _status(studio.request("/api/catalog/start", {**studio.installation(), "game": game}))

    @mcp.tool(annotations=read_only)
    def get_run_status() -> dict[str, Any]:
        """Check current or last Studio operation, including run ID, progress, and errors."""
        return _status(studio.request("/api/match/status"))

    @mcp.tool(annotations=read_only)
    def list_matches(offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """List saved test match results and their run directory IDs."""
        return studio.request("/api/match/history", offset=offset, limit=limit)

    @mcp.tool(annotations=read_only)
    def list_experiments(offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """List saved tournament and parameter sweep summaries, most recent first."""
        return studio.request("/api/experiment/history", offset=offset, limit=limit)

    @mcp.tool(annotations=read_only)
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        """Read the standings, per-game results, and provenance for a saved experiment ID."""
        return studio.request("/api/experiment/history", id=experiment_id)

    @mcp.tool(annotations=starts_work)
    def start_test_match(game: str, map_name: str, players: list[Player], game_minutes: int,
                         record_demo: bool = False) -> dict[str, Any]:
        """Start a headless test match with installed players. Each player needs short_name, version, profile,
        faction (Armada, Cortex, or Legion), and ally (0 or 1). game_minutes is simulated game time."""
        return _status(studio.request("/api/match/start", {
            **studio.installation(), "game": game, "map_name": map_name,
            "players": [player.model_dump() for player in players],
            "minutes": game_minutes, "skip_smoke": False, "record_demo": record_demo,
        }))

    @mcp.tool(annotations=starts_work)
    def start_experiment(
        game: str, maps: list[str], players: list[Player], game_minutes: int,
        game_limit: int, real_time_seconds: int, mode: Literal["tournament", "sweep"],
        sweep: Sweep | None = None, seed: int | None = None,
    ) -> dict[str, Any]:
        """Start a round-robin tournament or numeric parameter sweep. Players use short_name, version, profile,
        faction, and ally (0 or 1). Tournaments also support Rotate and Random factions. A sweep uses a first
        player, an installed opponent, and a sweep object with workspace project, profile, path, one-based
        line and column, and numeric values. Supply the last-read file sha256 to guard the literal
        selection against concurrent edits. game_minutes is simulated per game, real_time_seconds is a budget
        for the whole run. Poll get_run_status for preparation errors, progress, and the experiment ID."""
        return _status(studio.request("/api/experiment/start", {
            **studio.installation(), "game": game, "maps": maps,
            "players": [player.model_dump() for player in players],
            "minutes": game_minutes, "game_limit": game_limit, "seconds_limit": real_time_seconds,
            "mode": mode, "sweep": sweep.model_dump(exclude_none=True) if sweep else None, "seed": seed,
        }))

    @mcp.tool(annotations=changes_state)
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Ask Studio to cancel the active operation with this exact run ID."""
        return _status(studio.request("/api/match/cancel", {"id": run_id}))

    @mcp.tool(annotations=changes_state)
    def set_experiment_paused(experiment_id: str, paused: bool) -> dict[str, Any]:
        """Request that the specified active experiment pause after in-flight games or resume."""
        return _status(studio.request("/api/experiment/pause", {"id": experiment_id, "paused": paused}))

    return mcp


def _status(state: dict) -> dict:
    """Keep status focused on progress. Saved detail is available separately."""
    fields = (
        "id", "operation", "state", "phase", "message", "scan_progress", "active_games",
        "active_matchups", "elapsed_seconds", "error",
    )
    result = {field: state[field] for field in fields if field in state}
    if experiment := state.get("experiment"):
        result["experiment"] = {key: value for key, value in experiment.items() if key not in ("games", "projects", "provenance")}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port of the running Studio server (default 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        import mcp.server.fastmcp
    except ModuleNotFoundError as error:
        if error.name != "mcp":
            raise
        parser.exit(1, "Install the MCP dependency with python -m pip install -r requirements-mcp.txt.\n")
    with StudioClient(args.port) as studio:
        create_server(studio).run(transport="stdio")


if __name__ == "__main__":
    main()
