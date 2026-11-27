"""Expose a running local BAR AI Studio instance to MCP clients over stdio."""

import argparse
import asyncio
import json
import re
import threading
from collections import Counter
from functools import wraps
from http.client import HTTPConnection, HTTPException
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import uuid4


DEFAULT_PORT = 8765
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_UNKNOWN_OUTCOME = (
    "Studio did not confirm the request. Check the operation or workspace state before retrying."
)


class StudioError(Exception):
    """An unavailable Studio instance or a rejected Studio API request."""


class StudioClient:
    def __init__(self, port: int) -> None:
        self.port = port
        self.client_id = f"mcp-{uuid4().hex}"
        self._stop = threading.Event()
        self._heartbeat = threading.Thread(target=self._keep_alive, daemon=True)
        self._registered = False

    def __enter__(self) -> "StudioClient":
        self._heartbeat.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._heartbeat.join()
        if self._registered:
            try:
                self.request("/api/client/close", {"client_id": self.client_id})
            except StudioError:
                pass

    def _keep_alive(self) -> None:
        while not self._stop.is_set():
            try:
                self.request("/api/client/heartbeat", {"client_id": self.client_id})
            except StudioError:
                interval = 2
            else:
                self._registered = True
                interval = 20
            self._stop.wait(interval)

    @staticmethod
    def _session_token(connection: HTTPConnection, host: str) -> str:
        connection.request("GET", "/mcp/session")
        session = None
        with connection.getresponse() as response:
            if response.status == 200 and response.headers.get_content_type() == "application/json":
                body = response.read(513)
                if len(body) <= 512:
                    try:
                        session = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
        if isinstance(session, dict) and type(session.get("version")) is int and session["version"] == 1:
            token = session.get("token")
            if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                return token
        raise StudioError(
            f"No compatible BAR AI Studio MCP session was found at http://{host}/. "
            "Restart Studio from the same checkout as the MCP adapter."
        )

    def request(self, path: str, /, payload: dict | None = None, **query: str | int) -> dict:
        host = f"127.0.0.1:{self.port}"
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        mutation_sent = False
        try:
            headers = {"X-Workshop-Token": self._session_token(connection, host)}
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
                try:
                    body = json.dumps(payload, ensure_ascii=True, allow_nan=False)
                except ValueError as error:
                    raise StudioError("Studio tool arguments must contain valid JSON values.") from error
                mutation_sent = path != "/api/match/cache"
                connection.request("POST", path, body=body, headers=headers)
            with connection.getresponse() as response:
                try:
                    body = response.read(_MAX_RESPONSE_BYTES + 1)
                    result = json.loads(body) if len(body) <= _MAX_RESPONSE_BYTES else None
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                    result = None
                if not isinstance(result, dict):
                    if mutation_sent:
                        raise StudioError(_UNKNOWN_OUTCOME)
                    raise StudioError(f"Studio returned an invalid response (HTTP {response.status}).")
                if response.status != 200:
                    if mutation_sent and not 400 <= response.status < 500:
                        raise StudioError(_UNKNOWN_OUTCOME)
                    error = result.get("error")
                    raise StudioError(error if isinstance(error, str) and error else f"Studio returned HTTP {response.status}.")
                return result
        except (OSError, HTTPException):
            if mutation_sent:
                raise StudioError(_UNKNOWN_OUTCOME) from None
            raise StudioError(
                f"Could not reach BAR AI Studio at http://{host}/. Start it with python -m app --port {self.port}."
            ) from None
        finally:
            connection.close()

    def installation(self) -> dict:
        settings = self.request("/api/settings").get("settings")
        if settings is None:
            raise StudioError("Choose and save a BAR installation in Studio before using this tool.")
        return {"bar_root": settings["bar_root"], "engine": settings["engine"]}


def create_server(studio: StudioClient):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    from pydantic import BaseModel, ConfigDict, Field

    FixedFaction = Literal["Armada", "Cortex", "Legion"]

    class Player(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        short_name: str
        version: str = ""
        profile: str = ""
        faction: Literal["Armada", "Cortex", "Legion", "Rotate", "Random"]
        ally: Literal[0, 1]

    class MatchPlayer(Player):
        faction: FixedFaction

    class BenchmarkSeries(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        games: int = Field(default=1, ge=1, le=100)
        maps: list[str] | None = Field(default=None, min_length=1)
        swap_sides: bool = False
        faction_pairs: list[Annotated[list[FixedFaction], Field(min_length=2, max_length=2)]] | None = None

    class Sweep(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        project: str
        profile: str
        path: str
        line: int
        column: int
        values: list[int | float]
        sha256: str | None = None

    class StartOptions(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        starting_metal: int = Field(ge=0, le=10_000)
        starting_energy: int = Field(ge=0, le=10_000)
        starter_base: bool
        build_turret: bool

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

    def register_tool(*, annotations: ToolAnnotations):
        def decorate(function):
            @wraps(function)
            async def run(**arguments):
                return await asyncio.to_thread(function, **arguments)

            return mcp.tool(annotations=annotations)(run)

        return decorate

    @register_tool(annotations=read_only)
    def list_projects() -> dict[str, Any]:
        """List Studio projects, profile keys and names, and workspace issues."""
        return studio.request("/api/projects")

    @register_tool(annotations=read_only)
    def list_profile_files(project: str, profile: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Page through editable file paths and revision hashes in a workspace profile."""
        if offset < 0 or not 1 <= limit <= 200:
            raise StudioError("Use a nonnegative offset and a limit from 1 to 200.")
        files = studio.request("/api/profile", project=project, profile=profile, metadata=1)["files"]
        end = offset + limit
        return {"files": files[offset:end], "total_files": len(files), "next_offset": end if end < len(files) else None}

    @register_tool(annotations=read_only)
    def read_profile_file(project: str, profile: str, path: str,
                          start_line: int = 1, line_count: int = 100,
                          max_chars: int = 12000, start_column: int = 1) -> dict[str, Any]:
        """Read bounded text with a full-file revision hash. Continue at one-based next_line and next_column."""
        if start_line < 1 or start_column < 1 or not 1 <= line_count <= 500 or not 1 <= max_chars <= 32000:
            raise StudioError("Use positive line and column numbers, 1 to 500 lines, and 1 to 32000 characters.")
        file = studio.request("/api/profile", project=project, profile=profile, path=path)["files"][0]
        lines = re.findall(r"[^\r\n]*(?:\r\n?|\n)|[^\r\n]+", file["text"])
        if start_line <= len(lines) and start_column > len(lines[start_line - 1]) + 1:
            raise StudioError("start_column is beyond the requested line.")
        parts = []
        next_index = min(start_line - 1, len(lines))
        column = start_column - 1
        end = min(next_index + line_count, len(lines))
        while next_index < end and max_chars:
            part = lines[next_index][column:column + max_chars]
            parts.append(part)
            max_chars -= len(part)
            column += len(part)
            if column == len(lines[next_index]):
                next_index += 1
                column = 0
        return {
            "path": path, "sha256": file["sha256"], "text": "".join(parts), "total_lines": len(lines),
            "next_line": next_index + 1 if next_index < len(lines) else None,
            "next_column": column + 1 if next_index < len(lines) else None,
        }

    @register_tool(annotations=changes_state)
    def save_profile_file(project: str, profile: str, path: str, sha256: str, text: str) -> dict[str, Any]:
        """Replace an existing workspace profile file with complete text, given its last-read SHA-256 revision."""
        edited = {"path": path, "sha256": sha256, "text": text}
        result = studio.request("/api/save", {"project": project, "profile": profile, "files": [edited], "changed_only": True})
        file = next(file for file in result["files"] if file["path"] == path)
        return {"path": path, "sha256": file["sha256"]}

    @register_tool(annotations=read_only)
    def get_project_install_status(project: str) -> dict[str, Any]:
        """Check whether a workspace project is installed and up to date for Studio's selected engine."""
        return studio.request("/api/install-status", project=project)

    @register_tool(annotations=changes_state)
    def install_project(project: str, confirmations: list[str] | None = None) -> dict[str, Any]:
        """Install the saved workspace project and all its profiles into Studio's selected engine.

        Start without confirmations. If Studio returns a confirmation prompt, show its exact text to
        the user. Retry with that text only after the user accepts it. Retain previously accepted
        prompts for the same installation attempt while handling any further prompts.
        """
        return studio.request("/api/install", {
            **studio.installation(), "project": project, "confirmations": confirmations or [],
        })

    @register_tool(annotations=read_only)
    def get_installed_content() -> dict[str, Any]:
        """Get games, maps, and installed AI variants from Studio's saved scan for the current engine."""
        installation = studio.installation()
        result = studio.request("/api/match/cache", installation)
        return {"engine": installation["engine"], "scan_required": result["content"] is None, **result}

    @register_tool(annotations=starts_work)
    def scan_installed_content() -> dict[str, Any]:
        """Start a BAR content scan using Studio's saved installation. Poll get_run_status for completion."""
        return _status(studio.request("/api/match/scan", studio.installation()))

    @register_tool(annotations=read_only)
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

    @register_tool(annotations=starts_work)
    def build_unit_catalog(game: str) -> dict[str, Any]:
        """Build Studio's searchable unit catalog for an installed game."""
        return _status(studio.request("/api/catalog/start", {**studio.installation(), "game": game}))

    @register_tool(annotations=read_only)
    def get_run_status(run_id: str | None = None) -> dict[str, Any]:
        """Check the current or most recent Studio operation. Pass its ID to avoid reading a different run."""
        status = studio.request("/api/match/status")
        if run_id is not None and status.get("id") != run_id:
            raise StudioError("That run is no longer the current Studio operation. Check saved match or experiment history.")
        return _status(status)

    @register_tool(annotations=read_only)
    def list_queue_games(run_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """List games in the current or most recent test queue by its exact operation ID.

        Use the one-based game_index to cancel a game. For logs, pass the game's logs.run_id or
        logs.smoke_run_id to read_match_log. Follow next_offset for additional games.
        """
        if not 0 <= offset <= 2**31 - 1 or not 1 <= limit <= 100:
            raise StudioError("Use a nonnegative offset up to 2147483647 and a limit from 1 to 100.")
        snapshot = studio.request("/api/match/status")
        if snapshot.get("operation") != "match" or snapshot.get("id") != run_id:
            raise StudioError("The requested queue is no longer current. Use list_matches for saved results.")
        games = snapshot["games"]
        end = offset + limit
        fields = (
            "game_index", "batch_id", "batch_game", "batch_size", "game", "map", "players",
            "state", "elapsed_seconds", "winner_side", "summary", "logs",
        )
        return {
            "id": run_id,
            "games": [{key: game[key] for key in fields if key in game} for game in games[offset:end]],
            "total_games": len(games),
            "next_offset": end if end < len(games) else None,
        }

    @register_tool(annotations=read_only)
    def read_match_log(
        run_id: str, file: Literal["infolog.txt", "stdout.txt", "result.json"], offset: int = 0,
    ) -> dict[str, Any]:
        """Read a bounded page of a match artifact by its run directory ID and byte offset.

        Use a run directory ID from a game or saved result. Follow next_offset for additional lines.
        """
        return studio.request("/api/match/logs", run_id=run_id, file=file, offset=offset)

    @register_tool(annotations=read_only)
    def list_matches(
        offset: int = 0, limit: int = 20, project: str | None = None,
        profile: str | None = None, map_name: str | None = None, checkpoint: str | None = None,
    ) -> dict[str, Any]:
        """List saved matches and their run directory IDs, optionally filtered by workspace or map."""
        filters = {"project": project, "profile": profile, "map": map_name, "checkpoint": checkpoint}
        return studio.request(
            "/api/match/history", offset=offset, limit=limit,
            **{key: value for key, value in filters.items() if value is not None},
        )

    @register_tool(annotations=read_only)
    def list_experiments(offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """List saved tournament and parameter sweep summaries, most recent first."""
        return studio.request("/api/experiment/history", offset=offset, limit=limit)

    @register_tool(annotations=read_only)
    def get_experiment(experiment_id: str, game_offset: int = 0, game_limit: int = 20) -> dict[str, Any]:
        """Read standings, provenance, and a page of an experiment's schedule and game results."""
        if game_offset < 0 or not 1 <= game_limit <= 100:
            raise StudioError("Use a nonnegative game offset and a game limit from 1 to 100.")
        saved = studio.request("/api/experiment/history", id=experiment_id)["experiment"]
        experiment = dict(saved)
        end = game_offset + game_limit
        total = 0
        for field in ("games", "schedule"):
            if isinstance(rows := saved.get(field), list):
                total = max(total, len(rows))
                experiment[field] = rows[game_offset:end]
        return {"experiment": experiment, "total_games": total, "next_game_offset": end if end < total else None}

    @register_tool(annotations=read_only)
    def preview_sweep_literal(
        project: str, profile: str, path: str, line: int, column: int, sha256: str,
    ) -> dict[str, Any]:
        """Validate a numeric sweep selection against a file revision and return the literal's column and length.

        Pass the SHA-256 from read_profile_file and a one-based line and Unicode column.
        """
        return studio.request("/api/experiment/literal", {
            "project": project, "profile": profile, "path": path, "line": line, "column": column, "sha256": sha256,
        })

    @register_tool(annotations=starts_work)
    def promote_sweep_candidate(experiment_id: str, candidate_id: str, name: str) -> dict[str, Any]:
        """Create a new workspace profile from an eligible candidate in a completed sweep."""
        return studio.request("/api/experiment/promote", {"id": experiment_id, "candidate_id": candidate_id, "name": name})

    @register_tool(annotations=starts_work)
    def start_test_match(game: str, map_name: str, players: list[MatchPlayer], game_minutes: int,
                         record_demo: bool = False, parallel_games: int | None = None,
                         start_options: StartOptions | None = None,
                         series: BenchmarkSeries | None = None) -> dict[str, Any]:
        """Queue a headless test match with installed players. Each player needs short_name, version, profile,
        faction (Armada, Cortex, or Legion), and ally (0 or 1). game_minutes is simulated game time.
        Supply series for repeated benchmark games, optional map and faction rotations, and swapped sides.
        If supplied, parallel_games changes the shared limit for concurrent games."""
        return _status(studio.request("/api/match/start", {
            **studio.installation(), "game": game, "map_name": map_name,
            "players": [player.model_dump() for player in players],
            "minutes": game_minutes, "skip_smoke": False, "record_demo": record_demo,
            "parallel_games": parallel_games, "start_options": start_options.model_dump() if start_options else None,
            "series": series.model_dump(exclude_none=True) if series else None,
        }))

    @register_tool(annotations=starts_work)
    def start_experiment(
        game: str, maps: list[str], players: list[Player], game_minutes: int,
        game_limit: int, real_time_seconds: int, mode: Literal["tournament", "sweep"],
        sweep: Sweep | None = None, seed: int | None = None,
        parallel_games: int | None = None, start_options: StartOptions | None = None,
    ) -> dict[str, Any]:
        """Start a round-robin tournament or numeric parameter sweep. Players use short_name, version, profile,
        faction, and ally (0 or 1). Tournaments also support Rotate and Random factions. A sweep uses a first
        player, an installed opponent, and a sweep object with workspace project, profile, path, one-based
        line and column, and numeric values. Supply the last-read file sha256 to guard the literal
        selection against concurrent edits. game_minutes is simulated per game, real_time_seconds is a budget
        for the whole run. If supplied, parallel_games changes the shared limit for concurrent games.
        Poll get_run_status for preparation errors, progress, and the experiment ID."""
        return _status(studio.request("/api/experiment/start", {
            **studio.installation(), "game": game, "maps": maps,
            "players": [player.model_dump() for player in players],
            "minutes": game_minutes, "game_limit": game_limit, "seconds_limit": real_time_seconds,
            "mode": mode, "sweep": sweep.model_dump(exclude_none=True) if sweep else None, "seed": seed,
            "parallel_games": parallel_games, "start_options": start_options.model_dump() if start_options else None,
        }))

    @register_tool(annotations=changes_state)
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel the operation with this exact ID. A test queue ID cancels the entire shared queue."""
        return _status(studio.request("/api/match/cancel", {"id": run_id}))

    @register_tool(annotations=changes_state)
    def cancel_match_game(run_id: str, game_index: int) -> dict[str, Any]:
        """Cancel one pending or running game by queue operation ID and one-based game_index."""
        return _status(studio.request("/api/match/cancel-game", {"id": run_id, "game_index": game_index}))

    @register_tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    def set_parallel_games(parallel_games: int) -> dict[str, Any]:
        """Set Studio's shared concurrent game limit. Zero pauses new games without stopping in-flight games."""
        return _status(studio.request("/api/match/parallel", {"parallel_games": parallel_games}))

    @register_tool(annotations=changes_state)
    def set_experiment_paused(experiment_id: str, paused: bool) -> dict[str, Any]:
        """Request that the specified active experiment pause after in-flight games or resume."""
        return _status(studio.request("/api/experiment/pause", {"id": experiment_id, "paused": paused}))

    return mcp


def _status(state: dict) -> dict:
    """Keep status focused on progress. Saved detail is available separately."""
    fields = (
        "id", "operation", "state", "phase", "message", "scan_progress", "active_games",
        "parallel_games", "elapsed_seconds", "error", "submission",
    )
    result = {field: state[field] for field in fields if field in state}
    if state.get("operation") == "match":
        result["game_counts"] = dict(Counter(row["state"] for row in state.get("games", [])))
    for field in ("progress", "history_errors", "active_matchups"):
        if state.get(field):
            result[field] = state[field][-10:]
    if (completed := state.get("result")) and completed.get("summary"):
        result["summary"] = completed["summary"]
    if experiment := state.get("experiment"):
        fields = ("id", "mode", "state", "game", "maps", "scheduled_games", "completed_games",
                  "stop_reason", "paused", "pause_requested", "elapsed_seconds", "coverage", "best_candidate_id")
        result["experiment"] = {field: experiment[field] for field in fields if field in experiment}
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
