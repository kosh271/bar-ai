"""Prepare and run isolated headless matches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .settings import _content_valid


class MatchError(Exception):
    """A match or its content could not be prepared."""


UDP_RECEIVE_SIZE = 65535
SMOKE_MINUTES = 2
SMOKE_TIMEOUT = 600
GAME_OVER_GRACE = 30


@dataclass(frozen=True)
class Player:
    short_name: str
    version: str
    profile: str
    faction: str
    ally: int
    name: str = ""


@dataclass(frozen=True)
class MatchProgress:
    phase: Literal["smoke", "match"]
    summary: str
    seconds: float | None = None
    message: str = ""


def artifact_stat(base: Path, *parts: str, directory: bool = False) -> os.stat_result:
    """Stat a regular artifact beneath a trusted base without accepting links."""
    path = base
    for index, part in enumerate(("", *parts)):
        if index:
            if not part or part in {".", ".."} or Path(part).name != part:
                raise MatchError("Invalid artifact path component.")
            path /= part
        metadata = path.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise MatchError(f"Linked run or BAR artifact paths are not supported: {path.name}")
        is_directory = index < len(parts) or directory
        expected_type = stat.S_ISDIR if is_directory else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            raise MatchError(f"Unexpected artifact type: {path.name}")
    return metadata


@contextmanager
def open_artifact(base: Path, *parts: str):
    artifact_stat(base, *parts)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(base.joinpath(*parts), flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(opened, artifact_stat(base, *parts)) or opened.st_nlink > 1:
            raise MatchError("The artifact changed or is linked.")
        yield stream


def replay_files(run: Path) -> list[dict]:
    replays = []
    try:
        artifact_stat(run, "demos", directory=True)
        for item in sorted((run / "demos").iterdir()):
            if item.suffix.casefold() != ".sdfz":
                continue
            try:
                with open_artifact(run, "demos", item.name) as source:
                    size = os.fstat(source.fileno()).st_size
                if size:
                    replays.append({"path": f"demos/{item.name}", "size_bytes": size})
            except (OSError, MatchError):
                continue
    except (OSError, MatchError):
        pass
    return replays


def archive_cache_path(local_data: Path, engine: Path, data: Path) -> Path:
    identity = f"{engine.resolve()}\0{data.resolve()}".casefold()
    return local_data / "archive-cache" / hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _copy_archive_cache(source: Path, destination: Path, *, atomic: bool = False) -> None:
    if not source.is_dir():
        return
    for cache_file in source.iterdir():
        if not cache_file.is_file() or not re.fullmatch(r"ArchiveCache\d+\.lua", cache_file.name):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        if not atomic:
            shutil.copy2(cache_file, destination / cache_file.name)
            continue
        descriptor, name = tempfile.mkstemp(prefix=".archive-", dir=destination)
        os.close(descriptor)
        temporary = Path(name)
        try:
            shutil.copyfile(cache_file, temporary)
            os.replace(temporary, destination / cache_file.name)
        finally:
            temporary.unlink(missing_ok=True)


def scan_content(
    engine: Path, data: Path, write: Path, stop: threading.Event, *, archive_cache: Path | None = None,
) -> dict:
    command = [sys.executable, "-m", "app.content_scan", str(engine), str(data), str(write), str(write / "content.json")]
    return _content_query(command, write, "content.json", stop, archive_cache, "Content scan", _content_valid)


def scan_units(
    engine: Path, data: Path, write: Path, game: str, stop: threading.Event, *, archive_cache: Path | None = None,
) -> dict:
    command = [sys.executable, "-m", "app.unit_catalog", str(engine), str(data), str(write), str(write / "units.json"), game]
    from .unit_catalog import valid_catalog
    return _content_query(command, write, "units.json", stop, archive_cache, "Unit catalog",
                          lambda result: isinstance(result, dict) and valid_catalog(result) and result["game"] == game)


def _content_query(
    command: list[str], write: Path, filename: str, stop: threading.Event, archive_cache: Path | None,
    label: str, valid: Callable[[object], bool],
) -> dict:
    if archive_cache is not None:
        _copy_archive_cache(archive_cache, write / "cache")
    output = write / filename
    process: subprocess.Popen | None = None
    try:
        with (write / "scan-output.txt").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, cwd=Path(__file__).resolve().parent.parent,
                stdout=log, stderr=subprocess.STDOUT,
            )
            started = time.monotonic()
            while process.poll() is None:
                if stop.wait(0.25):
                    raise MatchError(f"{label} cancelled.")
                if time.monotonic() - started > 600:
                    raise MatchError(f"{label} timed out after 10 minutes.")
            code = process.wait()
    except (OSError, subprocess.SubprocessError) as error:
        raise MatchError(f"Cannot run {label.lower()}: {error}") from error
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    if code or not output.is_file():
        details = (write / "scan-output.txt").read_text(encoding="utf-8", errors="replace")
        raise MatchError(f"{label} failed (exit {code}):\n{details[-3000:]}")
    try:
        content = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MatchError(f"Cannot read {label.lower()} result: {error}") from error
    if not valid(content):
        raise MatchError(f"The engine returned an invalid {label.lower()} result.")
    if archive_cache is not None:
        _copy_archive_cache(write / "cache", archive_cache, atomic=True)
    return content


def _value(value: str) -> str:
    if not value or any(char in value for char in ";\r\n[]{}"):
        raise MatchError(f"Invalid start script value: {value!r}")
    return value


def _section(name: str, values: dict[str, str | int | dict]) -> str:
    lines = [f"[{name}]", "{"]
    for key, value in values.items():
        if isinstance(value, dict):
            lines.append(_section(key, value))
        else:
            lines.append(f"{key}={value};")
    return "\n".join([*lines, "}"])


def build_script(map_name: str, game: str, players: list[Player], minutes: int, record_demo: bool = False) -> str:
    if type(record_demo) is not bool:
        raise MatchError("record_demo must be a boolean.")
    if not 0 < minutes <= 2147483647 // 1800 or not players or {p.ally for p in players} != {0, 1}:
        raise MatchError("Enter a positive time limit and at least one AI on each side.")
    lines = [
        "[GAME]", "{", f"MapName={_value(map_name)};", f"GameType={_value(game)};",
        "StartPosType=0;", "GameStartDelay=0;", "IsHost=1;", "OnlyLocal=1;", "HostIP=127.0.0.1;",
        "HostPort=0;", "NumPlayers=1;", "NumUsers=1;", "NoHelperAIs=0;",
        "MyPlayerName=EditorHost;", f"RecordDemo={int(record_demo)};",
        _section("PLAYER0", {"Name": "EditorHost", "Spectator": 1}),
    ]
    for number, player in enumerate(players):
        ai: dict[str, str | int | dict] = {
            "Name": f"AI{number}", "ShortName": _value(player.short_name),
            "Version": _value(player.version), "Team": number, "Host": 0,
        }
        if player.profile:
            ai["OPTIONS"] = {"profile": _value(player.profile)}
        lines.append(_section(f"AI{number}", ai))
        lines.append(_section(f"TEAM{number}", {
            "TeamLeader": 0, "AllyTeam": player.ally, "Side": _value(player.faction),
            "RGBColor": "0.2 0.4 1" if player.ally == 0 else "1 0.2 0.2",
        }))
    for ally in (0, 1):
        lines.append(_section(f"ALLYTEAM{ally}", {"NumAllies": 0}))
    lines.append(_section("MODOPTIONS", {
        "MinSpeed": 20, "MaxSpeed": 20, "experimentallegionfaction": 1,
        "debugcommands": f"{minutes * 60 * 30}:quitforce",
    }))
    return "\n".join([*lines, "}", ""])


_IGNORED_SHUTDOWN = (
    'Error in Shutdown(): [string "LuaUI/Widgets/gui_sensor_ranges_jammer_preview.lua"]:1581: '
    "attempt to index field 'sheetShader' (a nil value)"
)


def _diagnostic_message(line: str) -> str:
    return re.sub(r"^(?:\[t=[^\]]+\]\s*)?(?:\[f=[^\]]+\]\s*)?", "", line.strip())


def _errors(text: str) -> list[str]:
    result = []
    dll_information = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if ("Error in Shutdown()" in line and index + 1 < len(lines)
                and _diagnostic_message(line) == "Error in Shutdown()"
                and _diagnostic_message(lines[index + 1]) == _IGNORED_SHUTDOWN):
            continue
        lower = line.lower()
        if "warning: dll information:" in lower:
            dll_information = True
            result.append(line.strip()[:500])
            continue
        if dll_information and re.search(r"\bError:\s+0x[0-9a-f]+\s+\S+\s*$", line, re.IGNORECASE):
            continue
        dll_information = False
        if (re.search(r"\b(?:warning|deprecated):", lower)
                or re.search(r"\berror in\s+\w+\(", lower)
                or "shader error" in lower
                or re.search(r"\(\d+, \d+\) : ERR\b", line, re.IGNORECASE)
                or "fix compilation errors" in lower
                or "script: unable to add section" in lower
                or ("ai script:" in lower and "is missing!" in lower)
                or re.search(r"Skirmish AI \S+ not found!", line, re.IGNORECASE)
                or "problem with a skirmish ai" in lower or "has crashed" in lower
                or (("skirmishai" in lower or "skirmish ai" in lower)
                    and ("error" in lower or "exception" in lower))
                or "[error]" in lower or re.search(r"\berror:", lower)
                or "sync error" in lower or ("desync" in lower and "loading widget" not in lower)):
            result.append(line.strip()[:500])
    return result


_IGNORED_DIAGNOSTICS = {
    "Error: [StoreSkirmishAILibInfo] required AI Interface for Skirmish AI   not found",
    'Error: [SetConfigInt] key "AdvSky" is deprecated',
    "[weapondefs.lua] Error: removed ceg_test_projectile_unit_ceg_test_projectile weaponDef, missing model",
    "Error: Could not finalize groundFX texture atlas. Use fewer/smaller textures.",
    "ERROR: PBR enabler: gl.CreateShader is nil",
    'Error in Initialize(): [string "luaui/Widgets/gui_pip/gui_pip.lua"]:9371: '
    "attempt to call field 'CreateShader' (a nil value)",
    "LuaShader: [AoE Napalm Shader] shader error(s):",
    "LuaShader: [Reclaim Field GL4] shader error(s):",
    _IGNORED_SHUTDOWN,
}
_IGNORED_FEATURE = re.compile(
    r'Error: \[GetFeatureDef\] could not find FeatureDef "allpinesb_ad0_'
    r'(?:brown|green)_[abc]_(?:xs|s|m|l|xl|xxl)"'
)


def _diagnostics(log: str, output: str) -> list[str]:
    logged = _errors(log)
    remaining = Counter(re.sub(r"^\[t=[^\]]+\]\s*", "", line) for line in logged)
    result = list(logged)
    for line in _errors(output):
        key = re.sub(r"^\[t=[^\]]+\]\s*", "", line)
        if remaining[key]:
            remaining[key] -= 1
        else:
            result.append(line)
    filtered = []
    for line in result:
        message = _diagnostic_message(line)
        if message not in _IGNORED_DIAGNOSTICS and not _IGNORED_FEATURE.fullmatch(message):
            filtered.append(line)
    return filtered


def _diagnostic_preview(diagnostics: list[str], limit: int) -> str:
    errors = [line for line in diagnostics if not re.search(r"\b(?:warning|deprecated):", line, re.IGNORECASE)]
    warnings = [line for line in diagnostics if re.search(r"\b(?:warning|deprecated):", line, re.IGNORECASE)]
    return "\n".join((errors + warnings)[:limit])


_AI_TAG = re.compile(r"Skirmish AI <([^>]+)>:\s*(.*)", re.IGNORECASE)
_AI_COMPILE_ERROR = re.compile(r"\(\d+,\s*\d+\)\s*:\s*ERR\b|fix compilation errors|"
                              r"script: unable to add section", re.IGNORECASE)
_AI_SCRIPT_WARNING = re.compile(r"\(\d+,\s*\d+\)\s*:\s*WARN\b|(?:angel\s*script|script)\b.*\bwarn\w*\b",
                                re.IGNORECASE)
_AI_RUNTIME_ERROR = re.compile(
    r"\b(?:angel\s*script|script)\b.*\b(?:error|exception|failed|missing)\b|"
    r"\b(?:exception|runtime error)\b", re.IGNORECASE,
)
_ENGINE_NOISE = re.compile(r"\b(?:yardmap|weapondefs?|shader)\b.*\b(?:error|warn\w*|invalid|missing|incorrect|failed)\b|"
                           r"\b(?:error|warning)\b.*\b(?:yardmap|weapondefs?|shader)\b", re.IGNORECASE)


def diagnostic_category(line: str) -> str:
    message = _diagnostic_message(line)
    if message in _IGNORED_DIAGNOSTICS or _IGNORED_FEATURE.fullmatch(message) or _ENGINE_NOISE.search(message):
        return "engine_warning"
    if (_AI_TAG.search(message) or _AI_COMPILE_ERROR.search(message) or _AI_SCRIPT_WARNING.search(message)
            or re.search(r"skirmish\s*ai|angel\s*script", message, re.IGNORECASE)):
        return "ai_script"
    if re.search(r"\b(?:warning|deprecated):", message, re.IGNORECASE):
        return "engine_warning"
    return "engine_error" if _errors(message) else "other"


_FRAMED_LINE = re.compile(r"^(?:\[t=[^\]]+\]\s*)?\[f=(\d{1,10})\]")
_END_GAME = re.compile(r"\bgame\s*over\b|\bgame ended\b|\bexecuting debugcommand\s*,?\s+quitforce\b",
                       re.IGNORECASE)
_DEFEAT = re.compile(r"\bteam\s+\d+\b.{0,32}\bdefeated\b", re.IGNORECASE)
_TEAM_FIELD = re.compile(r"^\[?team(?:\s+|[:=]\s*)(\d{1,4})(?:\]|:)?\s*", re.IGNORECASE)
_AI_INITIALIZED = re.compile(r"\((\d{1,4})\)\s+initialized!\s*$", re.IGNORECASE)
_FACTORY = re.compile(r"^(?:selected factory|factory selected|initial factory)\s*[:=]\s*(.+)", re.IGNORECASE)
_FACTORY_SWITCH = re.compile(r"^(?:switch(?:ed|ing)? factory|factory switch(?:ed)?)\s*[:=]\s*(.+)", re.IGNORECASE)
_OPENER = re.compile(r"^(?:selected opener|opener(?: queue| chosen)?)\s*[:=]\s*(.+)", re.IGNORECASE)
_TEAM_STATS = re.compile(r"^team\s+(\d{1,4})\s+stats\s*:\s*(.*)", re.IGNORECASE)
_STAT_FIELDS = {
    "metalproduced": "metal_produced", "energyproduced": "energy_produced", "metalused": "metal_used",
    "energyused": "energy_used", "metalexcess": "metal_excess", "energyexcess": "energy_excess",
    "unitsproduced": "units_produced", "unitskilled": "units_killed", "unitsdied": "units_died",
    "unitslost": "units_died", "unitcount": "unit_count", "damagedealt": "damage_dealt",
    "damagereceived": "damage_received", "metal": "metal", "energy": "energy",
}


def _stats_sample(value: dict) -> dict:
    stats = {}
    for key, amount in value.items():
        field = _STAT_FIELDS.get(str(key).replace("_", "").replace(" ", "").casefold())
        if field and type(amount) in (int, float) and -1e18 <= amount <= 1e18:
            stats[field] = amount
    return stats


def _end_stats(run: Path) -> dict[int, dict]:
    try:
        with open_artifact(run, "LuaUI", "Config", "teamstats.json") as stream:
            data = json.loads(stream.read(2 * 1024 * 1024))
    except (OSError, ValueError, RecursionError, MatchError):
        return {}
    if isinstance(data, dict):
        data = data.get("teams", data)
    if not isinstance(data, (list, dict)):
        return {}
    teams = data.items() if isinstance(data, dict) else enumerate(data)
    result = {}
    for index, samples in teams:
        if isinstance(samples, dict):
            index = samples.get("team_id", samples.get("team", index))
            samples = samples.get("samples", samples)
        if not re.fullmatch(r"[0-9]{1,4}", str(index)):
            continue
        if isinstance(samples, list):
            samples = next((sample for sample in reversed(samples) if isinstance(sample, dict)), {})
        if isinstance(samples, dict):
            result[int(index)] = _stats_sample(samples)
    return result


def _ai_telemetry() -> dict:
    return {"script_path": None, "profile_loaded": None, "factory": None, "opener": None,
            "factory_switches": [], "ai_script_errors": 0, "ai_script_warnings": 0}


def _count_script_diagnostic(team: dict, message: str) -> None:
    if _AI_COMPILE_ERROR.search(message) or _AI_RUNTIME_ERROR.search(message):
        team["ai_script_errors"] += 1
    elif _AI_SCRIPT_WARNING.search(message):
        team["ai_script_warnings"] += 1


def _ai_event(team: dict, message: str) -> None:
    if message.casefold().startswith("load script:"):
        script = message.split(":", 1)[1].strip().replace("\\", "/")[:1000]
        team["script_path"] = script
        parts = script.split("/")
        if len(parts) >= 2:
            team["profile_loaded"] = parts[-2]
    elif factory := _FACTORY.match(message):
        team["factory"] = factory[1][:500]
    elif opener := _OPENER.match(message):
        team["opener"] = opener[1][:500]
    elif factory_switch := _FACTORY_SWITCH.match(message):
        if len(team["factory_switches"]) < 100:
            team["factory_switches"].append(factory_switch[1][:500])
    _count_script_diagnostic(team, message)


def match_telemetry(run: Path, players: list[Player], log: str, output: str) -> dict:
    teams = [
        {**_ai_telemetry(), "team": index, "side": player.ally + 1, "ai": player.name or player.short_name,
         "profile_requested": player.profile, "faction": player.faction, "stats": {}}
        for index, player in enumerate(players)
    ]
    identities: dict[str, set[int]] = {}
    for index, player in enumerate(players):
        tag = f"{player.name or player.short_name}-{player.version}".casefold()
        identities.setdefault(tag, set()).add(index)
    unassigned = _ai_telemetry()
    pending: dict[str, dict] = {}
    terminal = defeated = last = None
    end_stats = _end_stats(run)
    timestamp = re.compile(r"^\[t=[^\]]+\]\s*")
    logged = Counter(timestamp.sub("", line.strip()) for line in log.splitlines())
    for source, text in enumerate((log, output)):
        source_started = False
        for line in text.splitlines():
            message = _diagnostic_message(line)
            key = timestamp.sub("", line.strip())
            if source and logged[key]:
                logged[key] -= 1
                # Duplicate framed lines still provide end-frame evidence below.
                duplicate = True
            else:
                duplicate = False
            if frame_text := _FRAMED_LINE.match(line):
                frame = int(frame_text[1])
                source_started = True
                last = frame if last is None else max(last, frame)
                if _END_GAME.search(message):
                    terminal = frame if terminal is None else min(terminal, frame)
                if _DEFEAT.search(message):
                    defeated = frame if defeated is None else max(defeated, frame)
            if duplicate:
                continue
            if stats_line := _TEAM_STATS.match(message):
                fields = dict(re.findall(r"([\w ]+?)\s*[:=]\s*(-?\d+(?:\.\d+)?)", stats_line[2]))
                team_id = int(stats_line[1])
                if team_id < len(teams):
                    teams[team_id]["stats"].update(_stats_sample({k: float(v) for k, v in fields.items()}))
            tagged = _AI_TAG.search(message)
            if not tagged:
                if _AI_COMPILE_ERROR.search(message) or _AI_SCRIPT_WARNING.search(message) or (
                    _AI_RUNTIME_ERROR.search(message) and diagnostic_category(line) == "ai_script"
                ):
                    _count_script_diagnostic(unassigned, message)
                continue
            tag, detail = tagged[1].casefold(), tagged[2].strip()
            if explicit := _TEAM_FIELD.match(detail):
                team_id = int(explicit[1])
                _ai_event(teams[team_id] if team_id < len(teams) else unassigned, detail[explicit.end():])
            elif initialized := _AI_INITIALIZED.search(detail):
                team_id = int(initialized[1])
                events = pending.pop(tag, {})
                if events:
                    team = teams[team_id] if team_id < len(teams) else unassigned
                    errors = team["ai_script_errors"] + events["ai_script_errors"]
                    warnings = team["ai_script_warnings"] + events["ai_script_warnings"]
                    team.update(events, ai_script_errors=errors, ai_script_warnings=warnings)
            elif (candidates := identities.get(tag, set())) and len(candidates) == 1:
                _ai_event(teams[next(iter(candidates))], detail)
            else:
                if detail.casefold().startswith("load script:") and not source_started:
                    pending.setdefault(tag, _ai_telemetry())
                _ai_event(pending.get(tag, unassigned), detail)
    for events in pending.values():
        unassigned["ai_script_errors"] += events["ai_script_errors"]
        unassigned["ai_script_warnings"] += events["ai_script_warnings"]
        if not unassigned["script_path"]:
            unassigned.update(script_path=events["script_path"], profile_loaded=events["profile_loaded"])
    for team in teams:
        team["stats"].update(end_stats.get(team["team"], {}))
    end_frame = next((frame for frame in (terminal, defeated, last) if frame is not None), None)
    duration = None
    if end_frame is not None:
        minutes, seconds = divmod(end_frame // 30, 60)
        duration = f"{minutes:02d}:{seconds:02d}" if minutes < 60 else f"{minutes // 60:02d}:{minutes % 60:02d}:{seconds:02d}"
    end_frame_source = "game_end" if terminal is not None else "team_defeat" if defeated is not None else "last_log_frame"
    return {"end_frame": end_frame, "end_frame_source": end_frame_source if end_frame is not None else None,
            "duration": duration, "teams": teams, "unassigned_ai": unassigned,
            "ai_script_errors": sum(team["ai_script_errors"] for team in [*teams, unassigned]),
            "ai_script_warnings": sum(team["ai_script_warnings"] for team in [*teams, unassigned])}


def _smoke_load_errors(text: str, players: list[Player]) -> list[str]:
    tags: dict[str, set[str]] = {}
    expected: set[tuple[str, str]] = set()
    errors = []
    for player in players:
        if not player.name or not player.profile:
            errors.append(f"{player.short_name}:{player.version}: no verifiable AI name or profile.")
            continue
        tag = f"{player.name}-{player.version}".casefold()
        tags.setdefault(tag, set()).add(player.short_name.casefold())
        expected.add((tag, player.profile.casefold()))
    for tag, names in tags.items():
        if len(names) > 1:
            errors.append(f"Multiple AIs share the log identity {tag}; their loads cannot be distinguished.")

    loads: set[tuple[str, str]] = set()
    for match in re.finditer(r"Skirmish AI <([^>\r\n]+)>: Load script:[ \t]*([^\r\n]+)", text, re.IGNORECASE):
        path = match.group(2).strip().replace("\\", "/").casefold().split("/")
        if len(path) >= 2:
            loads.add((match.group(1).casefold(), path[-2]))
    for tag, profile in sorted(expected):
        if (tag, profile) not in loads:
            errors.append(f"{tag}, profile {profile}: no script load was found.")
    return errors


def _run_smoke(
    engine: Path, data: Path, local_data: Path, map_name: str, game: str,
    players: list[Player], stop: threading.Event, *,
    process_started: Callable[[subprocess.Popen], None] | None = None,
) -> tuple[bool, str, Path]:
    script = build_script(map_name, game, players, SMOKE_MINUTES)
    if not (engine / "spring-headless.exe").is_file():
        raise MatchError(f"Headless engine not found: {engine}")
    runs = local_data / "matches"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="smoke-", dir=runs)).resolve()
    (run / "script.txt").write_text(script, encoding="utf-8")
    archive_cache = archive_cache_path(local_data, engine, data)
    _copy_archive_cache(archive_cache, run / "cache")
    config = run / "headless.cfg"
    shutil.copy2(Path(__file__).with_name("headless.cfg"), config)
    command = [str(engine / "spring-headless.exe"), "--write-dir", str(run),
               "--config", str(config), str(run / "script.txt")]
    env = {**os.environ, "SPRING_DATADIR": str(data), "SPRING_WRITEDIR": str(run)}
    stopped = timed_out = False
    process: subprocess.Popen | None = None
    try:
        with (run / "stdout.txt").open("w", encoding="utf-8") as output:
            if stop.is_set():
                raise MatchError(f"Stopped before the smoke engine started; files: {run}")
            process = subprocess.Popen(command, cwd=engine, env=env, stdout=output, stderr=subprocess.STDOUT)
            if process_started is not None:
                process_started(process)
            started = time.monotonic()
            while process.poll() is None:
                if stop.wait(0.25):
                    stopped = True
                    break
                if time.monotonic() - started >= SMOKE_TIMEOUT:
                    timed_out = True
                    break
            if stopped or timed_out:
                process.terminate()
                process.wait(timeout=10)
            code = process.wait()
    except (OSError, subprocess.SubprocessError) as error:
        raise MatchError(f"Smoke run failed; files: {run}\n{error}") from error
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    if code == 0:
        _copy_archive_cache(run / "cache", archive_cache, atomic=True)

    log_path = run / "infolog.txt"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    output = (run / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    failures = []
    if stopped or stop.is_set():
        failures.append("Stopped by user.")
    if timed_out:
        failures.append(f"Smoke test exceeded {SMOKE_TIMEOUT} seconds of real time.")
    if code != 0 and not stopped and not timed_out:
        failures.append(f"Engine exited with code {code}.")
    if not log:
        failures.append("The engine produced no infolog.")
    else:
        missing = re.findall(r"Error: Skirmish AI (\S+) not found!", log)
        if missing:
            failures.append("The engine could not find: " + ", ".join(sorted(set(missing))))
        compile_errors = re.findall(
            r"[^\n]{0,40}\(?\d+, \d+\) : ERR[^\n]{0,90}|Fix compilation errors[^\n]{0,60}", log
        )
        if compile_errors:
            failures.append("AngelScript errors:\n" + "\n".join(sorted(set(compile_errors))[:10]))
        if "has crashed" in log or "problem with a skirmish AI" in log:
            failures.append("The engine reported an engine or AI crash.")
        failures.extend(_smoke_load_errors(log, players))
    diagnostics = _diagnostics(log, output)
    warning = ""
    if diagnostics:
        warning = f"Smoke log contains {len(diagnostics)} engine diagnostics; inspect its logs.\n" + \
                  _diagnostic_preview(diagnostics, 3)
    passed = not failures
    summary = ("Smoke test passed: selected profiles loaded and no compilation errors were found."
               if passed else "Smoke test failed; match not started:\n" + "\n".join(failures))
    if warning:
        summary += "\n" + warning
    (run / "result.json").write_text(json.dumps({
        "smoke_passed": passed, "engine_exit": code, "failures": failures,
        "engine_diagnostics": diagnostics, "telemetry": match_telemetry(run, players, log, output),
    }, indent=2) + "\n", encoding="utf-8")
    return passed, summary, run


def _run_match(
    engine: Path, data: Path, local_data: Path, map_name: str, game: str,
    players: list[Player], minutes: int, stop: threading.Event, *, archive_cache: Path,
    process_started: Callable[[subprocess.Popen], None] | None = None, record_demo: bool = False,
) -> tuple[str, Path]:
    script = build_script(map_name, game, players, minutes, record_demo)
    if not (engine / "spring-headless.exe").is_file():
        raise MatchError(f"Headless engine not found: {engine}")
    runs = local_data / "matches"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="match-", dir=runs)).resolve()
    (run / "script.txt").write_text(script, encoding="utf-8")
    _copy_archive_cache(archive_cache, run / "cache")
    env = {**os.environ, "SPRING_DATADIR": str(data), "SPRING_WRITEDIR": str(run)}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as host:
        host.bind(("127.0.0.1", 0))
        host.settimeout(0.25)
        config = run / "headless.cfg"
        config.write_text(
            f"AutohostIP = 127.0.0.1\nAutohostPort = {host.getsockname()[1]}\n"
            "ThreadPinPolicy = 0\nLogFlushLevel = 0\n", encoding="utf-8",
        )
        command = [str(engine / "spring-headless.exe"), "--write-dir", str(run),
                   "--config", str(config), str(run / "script.txt")]
        process: subprocess.Popen | None = None
        was_stopped = False
        winner: list[int] | None = None
        forced = False
        limit_failure = False
        try:
            with (run / "stdout.txt").open("w", encoding="utf-8") as output:
                if stop.is_set():
                    raise MatchError(f"Stopped before the engine started; files: {run}")
                process = subprocess.Popen(command, cwd=engine, env=env, stdout=output, stderr=subprocess.STDOUT)
                if process_started is not None:
                    process_started(process)
                peer = None
                termination_started = None
                game_over_at = None
                game_started = None
                checked_at = 0.0
                limit_registered = False
                while process.poll() is None:
                    try:
                        packet, address = host.recvfrom(UDP_RECEIVE_SIZE)
                    except socket.timeout:
                        packet, address = b"", None
                    if address and address[0] == "127.0.0.1" and packet:
                        if peer is None and packet == b"\x00":
                            peer = address
                        if address == peer and packet[0] == 2:
                            game_started = time.monotonic()
                        if address == peer and packet[0] == 3 and len(packet) >= 3 and packet[1] == len(packet):
                            winner = list(packet[3:])
                    now = time.monotonic()
                    if winner is not None and game_over_at is None:
                        game_over_at = now
                    if game_started is not None and not limit_registered and now - checked_at >= 2:
                        checked_at = now
                        text = (run / "stdout.txt").read_text(encoding="utf-8", errors="replace")
                        if (run / "infolog.txt").is_file():
                            text += (run / "infolog.txt").read_text(encoding="utf-8", errors="replace")
                        limit_registered = bool(re.search(
                            rf"Adding debug command\s*,?\s+{minutes * 1800}\s*,?\s+quitforce\b", text,
                        ))
                        if not limit_registered and now - game_started >= 120:
                            limit_failure = True
                    grace_expired = game_over_at is not None and now - game_over_at >= GAME_OVER_GRACE
                    if (stop.is_set() or limit_failure or grace_expired) and termination_started is None:
                        was_stopped = stop.is_set() and winner is None
                        termination_started = now
                        if peer is not None:
                            host.sendto(b"/kill", peer)
                    if termination_started is not None and time.monotonic() - termination_started > 10:
                        process.terminate()
                        forced = True
                        process.wait(timeout=10)
                code = process.wait()
                host.setblocking(False)
                while True:
                    try:
                        packet, address = host.recvfrom(UDP_RECEIVE_SIZE)
                    except BlockingIOError:
                        break
                    if peer is None and address[0] == "127.0.0.1" and packet == b"\x00":
                        peer = address
                    if address == peer and packet and packet[0] == 3 and len(packet) >= 3 and packet[1] == len(packet):
                        winner = list(packet[3:])
        except (OSError, subprocess.SubprocessError) as error:
            raise MatchError(f"Headless run failed; files: {run}\n{error}") from error
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    log_path = run / "infolog.txt"
    try:
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        output = (run / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise MatchError(f"Cannot read match logs in {run}: {error}") from error
    text = log + "\n" + output
    errors = _diagnostics(log, output)
    if was_stopped:
        result = "Stopped by user."
    elif limit_failure:
        result = "Stopped: the game did not confirm the requested time limit."
    elif winner is not None and (any(side not in (0, 1) for side in winner) or len(set(winner)) != len(winner)):
        result = f"Invalid winning sides reported: {winner}"
    elif winner is not None:
        result = "Draw." if not winner or len(winner) == 2 else f"Winner: side {winner[0] + 1}."
    elif re.search(r"Executing debugcommand\s*,?\s+quitforce\b", text):
        result = f"Time limit reached ({minutes} game minutes)."
    elif code and not forced:
        result = f"Engine failed (exit {code}); inspect the logs."
    else:
        result = f"Match ended without a confirmed result (engine exit {code})."
    if code and not forced and not was_stopped and not limit_failure and (
        winner is not None or re.search(r"Executing debugcommand\s*,?\s+quitforce\b", text)
    ):
        result += f"\nEngine failed (exit {code}); inspect the logs."
    if errors:
        result += f"\nMatch reported {len(errors)} diagnostics:\n" + _diagnostic_preview(errors, 10)
    if forced:
        result += "\nThe engine did not shut down gracefully and was terminated."
    (run / "result.json").write_text(json.dumps({
        "result": result, "engine_exit": code, "winning_sides": winner, "errors": errors,
        "telemetry": match_telemetry(run, players, log, output),
        "replays": replay_files(run) if record_demo else [],
    }, indent=2) + "\n", encoding="utf-8")
    return result, run


def run_match(
    engine: Path, data: Path, local_data: Path, map_name: str, game: str,
    players: list[Player], minutes: int, stop: threading.Event,
    progress: Callable[[MatchProgress], None] | None = None, *, skip_smoke: bool = False,
    process_started: Callable[[subprocess.Popen], None] | None = None, record_demo: bool = False,
) -> tuple[str, Path]:
    build_script(map_name, game, players, minutes, record_demo)
    if stop.is_set():
        raise MatchError("Stopped before the match started.")
    if skip_smoke:
        summary = "Smoke test skipped; AI loading and compilation were not verified."
        cache = archive_cache_path(local_data, engine, data)
        if progress is not None:
            progress(MatchProgress("smoke", "Skipped", message=summary))
    else:
        started = time.monotonic()
        try:
            passed, summary, smoke_path = _run_smoke(
                engine, data, local_data, map_name, game, players, stop, process_started=process_started,
            )
        except (MatchError, OSError, ValueError) as error:
            if progress is not None:
                progress(MatchProgress("smoke", "Stopped" if stop.is_set() else "Failed",
                                       time.monotonic() - started, str(error)))
            raise
        summary += f"\nSmoke logs: {smoke_path}"
        if progress is not None:
            outcome = "Passed" if passed else "Stopped" if stop.is_set() else "Failed"
            progress(MatchProgress("smoke", outcome, time.monotonic() - started, summary))
        if not passed:
            return summary, smoke_path
        cache = smoke_path / "cache"
    if stop.is_set():
        if progress is not None:
            progress(MatchProgress("match", "Not started (stopped)"))
        if skip_smoke:
            raise MatchError("Stopped before the match started.")
        return f"{summary}\nStopped after the smoke test; match not started.", smoke_path
    if progress is not None:
        progress(MatchProgress("match", "Running", message=f"{summary}\nStarting headless match..."))
    started = time.monotonic()
    try:
        result, match_path = _run_match(engine, data, local_data, map_name, game, players, minutes, stop,
                                        archive_cache=cache, process_started=process_started, record_demo=record_demo)
    except (MatchError, OSError, ValueError) as error:
        if progress is not None:
            progress(MatchProgress("match", "Stopped" if stop.is_set() else "Failed",
                                   time.monotonic() - started, f"{summary}\n{error}"))
        raise
    if progress is not None:
        lines = result.splitlines()
        outcome = lines[0]
        if len(lines) > 1 and lines[1].startswith("Engine failed"):
            outcome += " (engine failed; see log)"
        progress(MatchProgress("match", outcome, time.monotonic() - started, f"{summary}\n{result}"))
    return f"{summary}\n{result}", match_path
