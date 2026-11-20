"""Independent editable AI projects and BARbarIAn profile copies."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path


class ProjectError(Exception):
    """A project or profile could not be created or opened."""


_INCLUDE = re.compile(r'(?m)^([ \t]*#[ \t]*include[ \t]*")([^"\r\n]+)(")')
_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _key(name: str, *, project: bool) -> str:
    if not name.strip() or "\n" in name or "\r" in name:
        raise ProjectError("Enter a non-empty name on one line.")
    if project:
        key = re.sub(r"[^A-Za-z0-9]", "", name)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", key):
            raise ProjectError("AI name must contain a letter followed by at most 63 letters or digits.")
    else:
        key = re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-").lower()
        if not key or len(key) > 64:
            raise ProjectError("Profile name must contain letters or digits and fit within 64 characters.")
    if key.upper() in _DEVICES:
        raise ProjectError(f"{name!r} is a reserved Windows name.")
    return key


def _unique_child(parent: Path, key: str) -> Path:
    if parent.exists() and any(child.name.casefold() == key.casefold() for child in parent.iterdir()):
        raise ProjectError(f"{key!r} already exists in {parent}.")
    return parent / key


def _write_metadata(path: Path, data: dict[str, str]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_metadata(path: Path, fields: tuple[str, ...]) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError(f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict) or any(
        not isinstance(value.get(field), str) or not value[field] for field in fields
    ):
        raise ProjectError(f"Invalid metadata: {path}")
    result = {field: value[field] for field in fields}
    if "source" in value:
        if not isinstance(value["source"], str):
            raise ProjectError(f"Invalid source metadata: {path}")
        result["source"] = value["source"]
    return result


def list_projects(root: Path) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    result = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if path.is_symlink():
            raise ProjectError(f"Project cannot be a symbolic link: {path}")
        metadata = _read_metadata(path / "project.json", ("name", "short_name"))
        if path.name != metadata["short_name"]:
            raise ProjectError(f"Project folder does not match its short name: {path}")
        result.append((path.name, metadata["name"]))
    return sorted(result, key=lambda item: item[1].casefold())


def list_profiles(project: Path) -> list[tuple[str, str]]:
    root = project / "profiles"
    if not root.is_dir():
        raise ProjectError(f"Profile directory not found: {root}")
    result = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if path.is_symlink():
            raise ProjectError(f"Profile cannot be a symbolic link: {path}")
        metadata = _read_metadata(path / "profile.json", ("name",))
        result.append((path.name, metadata["name"]))
    return sorted(result, key=lambda item: item[1].casefold())


def _files(root: Path, extension: str) -> list[Path]:
    if root.is_symlink():
        raise ProjectError(f"Symbolic links are not supported: {root}")
    if not root.is_dir():
        raise ProjectError(f"Required directory not found: {root}")
    paths = list(root.rglob("*"))
    link = next((path for path in paths if path.is_symlink()), None)
    if link:
        raise ProjectError(f"Symbolic links are not supported: {link}")
    return sorted(path for path in paths if path.is_file() and path.suffix.lower() == extension)


def _copy_profile(
    stock: Path, destination: Path, profile_key: str, name: str, source_label: str = "",
) -> None:
    config_root = stock / "config"
    script_root = stock / "script"
    profile_config = config_root / profile_key
    profile_script = script_root / profile_key
    if not profile_config.is_dir() or not profile_script.is_dir():
        raise ProjectError(f"Profile {profile_key!r} not found under {stock}")
    if not (profile_script / "init.as").is_file() or not (profile_script / "main.as").is_file():
        raise ProjectError(f"Profile {profile_key!r} is missing init.as or main.as: {profile_script}")

    config = destination / "config"
    config.mkdir(parents=True)
    for path in sorted(config_root.glob("*.json")):
        if path.is_symlink():
            raise ProjectError(f"Symbolic links are not supported: {path}")
        shutil.copy2(path, config / path.name)
    for path in _files(profile_config, ".json"):
        target = config / path.relative_to(profile_config)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if not any(config.rglob("*.json")):
        raise ProjectError(f"No JSON configuration found under {config_root}")

    scripts = destination / "script"
    scripts.mkdir()
    profile_scripts = _files(profile_script, ".as")
    if any(path.relative_to(profile_script).parts[0].casefold() == "__shared__" for path in profile_scripts):
        raise ProjectError(f"Reserved script directory already exists in {profile_script}")
    shared_scripts = sorted(script_root.glob("*.as"))
    if any(path.is_symlink() for path in shared_scripts):
        raise ProjectError(f"Symbolic links are not supported in {script_root}")
    initial = profile_scripts + shared_scripts
    pending = list(initial)
    processed: set[Path] = set()
    root = script_root.resolve()
    profile = profile_script.resolve()

    def destination_for(source: Path) -> Path:
        if source.is_relative_to(profile):
            return scripts / source.relative_to(profile)
        return scripts / "__shared__" / source.relative_to(root)

    while pending:
        source = pending.pop().resolve()
        if source in processed:
            continue
        if not source.is_relative_to(root) or not source.is_file() or source.suffix.lower() != ".as":
            raise ProjectError(f"Script include is missing or outside the stock script tree: {source}")
        processed.add(source)
        target = destination_for(source)
        text = source.read_text(encoding="utf-8")

        def replace_include(match: re.Match[str]) -> str:
            include = match.group(2)
            include_path = Path(include)
            if include_path.is_absolute() or include_path.drive:
                raise ProjectError(f"Absolute script include is not supported in {source}: {include}")
            resolved = (source.parent / include_path).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.suffix.lower() != ".as":
                raise ProjectError(f"Script include not found inside {root}: {source}: {include}")
            pending.append(resolved)
            relative = os.path.relpath(destination_for(resolved), target.parent).replace("\\", "/")
            return match.group(1) + relative + match.group(3)

        rewritten = _INCLUDE.sub(replace_include, text)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewritten, encoding="utf-8", newline="")

    metadata = {"name": name}
    if source_label:
        metadata["source"] = source_label
    _write_metadata(destination / "profile.json", metadata)


def create_project(root: Path, name: str, stock: Path, *, source_label: str = "") -> Path:
    key = _key(name, project=True)
    root.mkdir(parents=True, exist_ok=True)
    target = _unique_child(root, key)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary:
        staged = Path(temporary) / key
        profile = staged / "profiles" / "hard"
        _copy_profile(stock, profile, "hard", "Hard", source_label)
        _write_metadata(staged / "project.json", {"name": name.strip(), "short_name": key, "default_profile": "hard"})
        staged.rename(target)
    return target


def create_profile_from_hard(project: Path, name: str, stock: Path, *, source_label: str = "") -> Path:
    return _create_profile(project, name, stock=stock, source_label=source_label)


def clone_profile(project: Path, name: str, source_key: str) -> Path:
    return _create_profile(project, name, source_key=source_key)


def _create_profile(
    project: Path, name: str, *, stock: Path | None = None,
    source_key: str = "", source_label: str = "",
) -> Path:
    root = project / "profiles"
    if not root.is_dir():
        raise ProjectError(f"Profile directory not found: {root}")
    key = _key(name, project=False)
    target = _unique_child(root, key)
    if stock is None:
        available = dict(list_profiles(project))
        if source_key not in available:
            raise ProjectError(f"Source profile is not available: {source_key}")
        source = root / source_key
        _files(source, ".as")
        _files(source / "config", ".json")
        _files(source / "script", ".as")
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary:
        staged = Path(temporary) / key
        if stock is not None:
            _copy_profile(stock, staged, "hard", name.strip(), source_label)
        else:
            shutil.copytree(source, staged)
            metadata = _read_metadata(staged / "profile.json", ("name",))
            metadata["name"] = name.strip()
            _write_metadata(staged / "profile.json", metadata)
        staged.rename(target)
    return target


def profile_files(profile: Path) -> list[Path]:
    return [
        *[path.relative_to(profile) for path in _files(profile / "config", ".json")],
        *[path.relative_to(profile) for path in _files(profile / "script", ".as")],
    ]


def delete_profile(project: Path, profile_key: str) -> None:
    """Delete a profile from a project."""
    root = project / "profiles"
    if not root.is_dir():
        raise ProjectError(f"Profile directory not found: {root}")
    profile_path = root / profile_key
    if not profile_path.is_dir():
        raise ProjectError(f"Profile not found: {profile_key}")
    if profile_path.is_symlink():
        raise ProjectError(f"Profile is a symbolic link and cannot be deleted: {profile_key}")
    try:
        shutil.rmtree(profile_path)
    except OSError as error:
        raise ProjectError(f"Cannot delete profile {profile_key}: {error}") from error


def delete_project(root: Path, project_key: str) -> None:
    """Delete an entire project and all its profiles."""
    project_path = root / project_key
    if not project_path.is_dir():
        raise ProjectError(f"Project not found: {project_key}")
    if project_path.is_symlink():
        raise ProjectError(f"Project is a symbolic link and cannot be deleted: {project_key}")
    try:
        shutil.rmtree(project_path)
    except OSError as error:
        raise ProjectError(f"Cannot delete project {project_key}: {error}") from error
