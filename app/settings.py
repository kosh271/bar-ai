"""Portable application settings."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class SettingsError(Exception):
    """Settings could not be read or saved."""


@dataclass(frozen=True)
class Settings:
    bar_root: str
    engine: str


@dataclass(frozen=True)
class EditorState:
    project: str
    profile: str
    file: str
    source: str = ""
    source_suggested: bool = False


def load_settings(path: Path) -> Settings | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"Cannot read settings {path}: {error}") from error
    if not isinstance(data, dict):
        raise SettingsError(f"Invalid settings in {path}: expected an object.")
    root, engine = data.get("bar_root"), data.get("engine")
    if not isinstance(root, str) or not root or not isinstance(engine, str) or not engine:
        raise SettingsError(f"Invalid settings in {path}: BAR root and engine are required.")
    return Settings(root, engine)


def load_editor_state(path: Path) -> EditorState | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"Cannot read editor state {path}: {error}") from error
    if not isinstance(data, dict):
        raise SettingsError(f"Invalid editor state in {path}: expected an object.")
    project, profile, file = data.get("project"), data.get("profile"), data.get("file")
    source = data.get("source", "")
    source_suggested = data.get("source_suggested", False)
    if any(not isinstance(value, str) for value in (project, profile, file, source)):
        raise SettingsError(f"Invalid editor state in {path}: selection fields must be strings.")
    if not isinstance(source_suggested, bool):
        raise SettingsError(f"Invalid editor state in {path}: source_suggested must be a boolean.")
    if (profile and not project) or (file and not profile):
        raise SettingsError(f"Invalid editor state in {path}: incomplete selection.")
    if source_suggested and not source:
        raise SettingsError(f"Invalid editor state in {path}: suggested source is missing.")
    return EditorState(project, profile, file, source, source_suggested)


def _save_json(path: Path, data: dict[str, object], label: str) -> None:
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as output:
            temporary = output.name
            json.dump(data, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise SettingsError(f"Cannot save {label} {path}: {error}") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def save_settings(path: Path, settings: Settings) -> None:
    _save_json(path, {"bar_root": settings.bar_root, "engine": settings.engine}, "settings")


def save_editor_state(path: Path, state: EditorState) -> None:
    _save_json(path, {
        "project": state.project, "profile": state.profile, "file": state.file,
        "source": state.source, "source_suggested": state.source_suggested,
    }, "editor state")


def load_match_map(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"Cannot read match state {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("map"), str) or not data["map"]:
        raise SettingsError(f"Invalid match state in {path}: map must be a nonempty string.")
    return data["map"]


def save_match_map(path: Path, map_name: str) -> None:
    if not map_name:
        raise SettingsError("Cannot save an empty match map.")
    _save_json(path, {"map": map_name}, "match state")


def _content_valid(content: object) -> bool:
    if not isinstance(content, dict):
        return False
    maps, games, ais, warnings = (content.get(key) for key in ("maps", "games", "ais", "warnings"))
    if (not isinstance(maps, list) or not isinstance(games, list)
            or not isinstance(ais, list) or not isinstance(warnings, list)):
        return False
    if not all(
        isinstance(item, dict) and isinstance(item.get("name"), str) and bool(item["name"])
        and type(item.get("width")) is int and item["width"] >= 0
        and type(item.get("height")) is int and item["height"] >= 0
        for item in maps
    ):
        return False
    if not all(isinstance(value, str) and value for value in games):
        return False
    if not all(isinstance(value, str) for value in warnings):
        return False
    return all(
        isinstance(ai, dict)
        and all(isinstance(ai.get(key), str) and ai[key] for key in ("short_name", "version", "name"))
        and isinstance(ai.get("profiles"), list)
        and all(
            isinstance(profile, dict)
            and all(isinstance(profile.get(key), str) and profile[key] for key in ("key", "name"))
            for profile in ai["profiles"]
        )
        for ai in ais
    )


def load_content_cache(path: Path, data_dir: Path, engine: str) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"Cannot read content cache {path}: {error}") from error
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("data_dir"), str)
            or os.path.normcase(data["data_dir"]) != os.path.normcase(str(data_dir))
            or not isinstance(data.get("engine"), str) or data["engine"].casefold() != engine.casefold()
            or not _content_valid(data.get("content"))):
        raise SettingsError(f"Invalid content cache {path}; refresh installed content.")
    return data["content"]


def save_content_cache(path: Path, data_dir: Path, engine: str, content: dict) -> None:
    if not _content_valid(content):
        raise SettingsError("Invalid content scan result; it was not cached.")
    _save_json(path, {"version": 1, "data_dir": str(data_dir), "engine": engine, "content": content}, "content cache")
