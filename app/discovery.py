"""Read-only discovery of a local Beyond All Reason installation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


class DiscoveryError(Exception):
    """The installation cannot be inspected."""


@dataclass(frozen=True)
class Installation:
    root: Path
    data: Path
    engines: tuple[str, ...]
    active_engine: str | None
    active_engine_message: str
    game_directory: Path | None


def default_root() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "Programs" / "Beyond-All-Reason"


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"Cannot read {path}: {error}") from error


def _active_engine(data: Path, engines: tuple[str, ...]) -> tuple[str | None, str]:
    launcher = data / "launcher_cfg.json"
    if not launcher.is_file():
        return None, "Launcher configuration not found; verify the selected engine."
    launcher_config = _read_json(launcher)
    if not isinstance(launcher_config, dict):
        raise DiscoveryError(f"Invalid launcher configuration: {launcher}")
    setup_id = launcher_config.get("config")
    if setup_id is None or setup_id == "":
        return None, "Launcher does not identify its active setup; verify the selected engine."
    if not isinstance(setup_id, str):
        raise DiscoveryError(f"Invalid active setup in {launcher}")

    config_path = data / "config.json"
    config = _read_json(config_path)
    if not isinstance(config, dict) or not isinstance(config.get("setups"), list):
        raise DiscoveryError(f"Invalid setup list in {config_path}")
    matches = [
        setup
        for setup in config["setups"]
        if isinstance(setup, dict)
        and isinstance(setup.get("package"), dict)
        and setup["package"].get("id") == setup_id
    ]
    if len(matches) != 1:
        raise DiscoveryError(f"Active setup {setup_id!r} is not uniquely defined in {config_path}")
    launch = matches[0].get("launch")
    engine = launch.get("engine") if isinstance(launch, dict) else None
    if not isinstance(engine, str) or not engine:
        raise DiscoveryError(f"Active setup {setup_id!r} has no engine in {config_path}")
    if engine not in engines:
        raise DiscoveryError(f"Active engine {engine!r} is not installed under {data / 'engine'}")
    return engine, f"Launcher reports active engine: {engine}"


def _engine_sort_key(name: str) -> tuple[tuple[int, str | int], ...]:
    return tuple(
        (1, int(part)) if part.isdecimal() else (0, part)
        for part in re.split(r"([0-9]+)", name.casefold())
    )


def discover(root: Path) -> Installation:
    root = root.expanduser().resolve()
    data = root / "data"
    engine_dir = data / "engine"
    if not data.is_dir():
        raise DiscoveryError(f"BAR data directory not found: {data}")
    if not engine_dir.is_dir():
        raise DiscoveryError(f"BAR engine directory not found: {engine_dir}")
    try:
        engines = tuple(sorted(
            (path.name for path in engine_dir.iterdir() if path.is_dir()),
            key=_engine_sort_key,
        ))
    except OSError as error:
        raise DiscoveryError(f"Cannot inspect {engine_dir}: {error}") from error
    if not engines:
        raise DiscoveryError(f"No installed engines found in {engine_dir}")
    active, message = _active_engine(data, engines)
    game_directory = data / "games" / "BAR.sdd"
    return Installation(
        root=root,
        data=data,
        engines=engines,
        active_engine=active,
        active_engine_message=message,
        game_directory=game_directory if game_directory.is_dir() else None,
    )


def engine_readiness(installation: Installation, engine: str) -> tuple[str, ...]:
    if engine not in installation.engines:
        raise DiscoveryError(f"Select an installed engine; {engine!r} is not available.")
    engine_dir = installation.data / "engine" / engine
    missing = []
    for label, path in (
        ("headless engine", engine_dir / "spring-headless.exe"),
        ("BARbarIAn DLL", engine_dir / "AI" / "Skirmish" / "BARb" / "stable" / "SkirmishAI.dll"),
    ):
        if not path.is_file():
            missing.append(f"{label}: {path}")
    return tuple(missing)
