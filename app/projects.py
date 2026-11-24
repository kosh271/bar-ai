"""Independent editable AI projects and BARbarIAn profile copies."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path


class ProjectError(Exception):
    """A project or profile could not be created or opened."""


_INCLUDE = re.compile(r'(?m)^([ \t]*#[ \t]*include[ \t]*")([^"\r\n]+)(")')
_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}
_RESERVED_PATH = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z", re.I)
MAX_WORKSPACE_FILE_BYTES = 8 * 1024 * 1024
MAX_WORKSPACE_BYTES = 64 * 1024 * 1024
MAX_WORKSPACE_ENTRIES = 5000
MAX_WORKSPACE_PATH = 512


def is_private_path_name(name: str) -> bool:
    value = name.casefold()
    return value == ".git" or value.startswith(".staging-") or value.endswith(".tmp")


def workspace_path_parts(name: str) -> tuple[str, ...]:
    parts = tuple(name.removesuffix("/").split("/"))
    if len(name) > MAX_WORKSPACE_PATH or any(
        not part or part in {".", ".."} or part.endswith((" ", ".")) or is_private_path_name(part)
        or _RESERVED_PATH.fullmatch(part) or re.search(r'[\x00-\x1f\x7f<>:"\\|?*]', part)
        for part in parts
    ):
        raise ProjectError(f"Unsafe workspace path: {name!r}")
    return parts


def check_workspace_size(size: int, total: int, count: int) -> None:
    if size > MAX_WORKSPACE_FILE_BYTES or total > MAX_WORKSPACE_BYTES or count > MAX_WORKSPACE_ENTRIES:
        raise ProjectError("Project files exceed their count or size limit.")


def _key(name: str, *, project: bool) -> str:
    try:
        name.encode("utf-8")
    except UnicodeError as error:
        raise ProjectError("The name must contain valid Unicode characters.") from error
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
    try:
        for text in result.values():
            text.encode("utf-8")
    except UnicodeError as error:
        raise ProjectError(f"Invalid Unicode in metadata: {path}") from error
    return result


def list_projects(root: Path, *, issues: list[dict[str, str]] | None = None) -> list[tuple[str, str]]:
    if _is_link(root):
        raise ProjectError("Projects directory cannot be a link.")
    if not root.exists():
        return []
    result = []
    for path in root.iterdir():
        if path.name.startswith("."):
            continue
        try:
            if not (path.is_dir() or _is_link(path)):
                continue
            metadata = _project_entry(path)
            result.append((path.name, metadata["name"]))
        except (ProjectError, OSError) as error:
            if issues is None:
                raise
            issues.append({"project": path.name, "message": str(error)})
    return sorted(result, key=lambda item: item[1].casefold())


def _profiles_root(project: Path) -> Path:
    root = project / "profiles"
    if _is_link(project) or _is_link(root) or not root.is_dir():
        raise ProjectError(f"Profile directory not found: {root}")
    return root


def list_profiles(project: Path, *, issues: list[dict[str, str]] | None = None) -> list[tuple[str, str]]:
    root = _profiles_root(project)
    result = []
    for path in root.iterdir():
        if path.name.startswith("."):
            continue
        try:
            if not (path.is_dir() or _is_link(path)):
                continue
            metadata = _profile_entry(path)
            result.append((path.name, metadata["name"]))
        except (ProjectError, OSError) as error:
            if issues is None:
                raise
            issues.append({"project": project.name, "profile": path.name, "message": str(error)})
    return sorted(result, key=lambda item: item[1].casefold())


def _project_entry(path: Path) -> dict[str, str]:
    if _is_link(path) or _is_link(path / "project.json"):
        raise ProjectError(f"Project paths cannot contain links: {path}")
    metadata = project_metadata(path)
    if path.name != metadata["short_name"]:
        raise ProjectError(f"Project folder does not match its short name: {path}")
    if _key(path.name, project=True) != path.name:
        raise ProjectError(f"Invalid project folder name: {path.name}")
    return metadata


def _profile_entry(path: Path) -> dict[str, str]:
    if _is_link(path) or _is_link(path / "profile.json"):
        raise ProjectError(f"Profile paths cannot contain links: {path}")
    validate_profile_key(path.name)
    metadata = _read_metadata(path / "profile.json", ("name",))
    for directory in (path / "config", path / "script"):
        if _is_link(directory) or not directory.is_dir():
            raise ProjectError(f"Required regular profile directory not found: {directory}")
    return metadata


def validate_deploy_name(short_name: str) -> None:
    if _key(short_name, project=True) != short_name or short_name.casefold() == "barb":
        raise ProjectError(f"Invalid AI identifier or conflict with the stock BARbarIAn: {short_name}")


def validate_profile_key(key: str) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", key) or len(key) > 64:
        raise ProjectError(f"Invalid profile folder name: {key}")


def validate_deploy_metadata(metadata: dict[str, str], profile_keys: set[str]) -> None:
    validate_deploy_name(metadata["short_name"])
    _key(metadata["name"], project=True)
    if metadata["default_profile"] not in profile_keys:
        raise ProjectError("The project's default profile does not exist.")
    for key in sorted(profile_keys):
        validate_profile_key(key)


def _files(root: Path, extension: str) -> list[Path]:
    if _is_link(root):
        raise ProjectError(f"Symbolic links are not supported: {root}")
    if not root.is_dir():
        raise ProjectError(f"Required directory not found: {root}")

    def walk_error(error: OSError) -> None:
        raise error

    result = []
    for directory, subdirs, filenames in os.walk(root, onerror=walk_error):
        parent = Path(directory)
        for name in subdirs + filenames:
            if _is_link(parent / name):
                raise ProjectError(f"Symbolic links are not supported: {parent / name}")
        result.extend(parent / name for name in filenames
                      if Path(name).suffix.lower() == extension and (parent / name).is_file())
    return sorted(result)


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
    validate_deploy_name(key)
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
    root = _profiles_root(project)
    key = _key(name, project=False)
    target = _unique_child(root, key)
    if stock is None:
        source = _profile_path(project, source_key)
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


def project_metadata(project: Path) -> dict[str, str]:
    path = project / "project.json"
    if _is_link(path):
        raise ProjectError("Project metadata cannot be a link.")
    return _read_metadata(path, ("name", "short_name", "default_profile"))


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _existing_child(parent: Path, key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key):
        raise ProjectError("Invalid project or profile key.")
    path = parent / key
    if (_is_link(parent) or _is_link(path) or not path.is_dir()
            or path.resolve().parent != parent.resolve()
            or not any(child.name == key for child in parent.iterdir())):
        raise ProjectError(f"Not a regular workspace directory: {key}")
    return path


def _profile_path(project: Path, key: str) -> Path:
    path = _existing_child(_profiles_root(project), key)
    _profile_entry(path)
    return path


def project_path(root: Path, key: str) -> Path:
    path = _existing_child(root, key)
    _project_entry(path)
    return path


def _replace_metadata(path: Path, data: dict[str, str]) -> None:
    if _is_link(path):
        raise ProjectError(f"Metadata cannot be a link: {path}")
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=path.parent) as directory:
        staged = Path(directory) / path.name
        _write_metadata(staged, data)
        staged.replace(path)


def rename_project(project: Path, name: str) -> None:
    _key(name, project=True)
    _replace_metadata(project / "project.json", {**project_metadata(project), "name": name.strip()})


def rename_profile(project: Path, profile_key: str, name: str) -> None:
    _key(name, project=False)
    path = _profile_path(project, profile_key) / "profile.json"
    if _is_link(path):
        raise ProjectError("Profile metadata cannot be a link.")
    _replace_metadata(path, {**_read_metadata(path, ("name",)), "name": name.strip()})


def set_default_profile(project: Path, profile_key: str) -> None:
    profile = _profile_path(project, profile_key)
    metadata = profile / "profile.json"
    if _is_link(metadata):
        raise ProjectError("Profile metadata cannot be a link.")
    _read_metadata(metadata, ("name",))
    _replace_metadata(project / "project.json", {**project_metadata(project), "default_profile": profile_key})


def _delete_tree(path: Path) -> None:
    def walk_error(error: OSError) -> None:
        raise error

    def remove_readonly(operation, filename, exc_info) -> None:
        error = exc_info[1]
        file = Path(filename)
        if (os.name != "nt" or not isinstance(error, PermissionError)
                or _is_link(file) or not file.resolve().is_relative_to(path.resolve())
                or file.stat().st_mode & stat.S_IWRITE):
            raise error
        file.chmod(stat.S_IWRITE)
        operation(filename)

    try:
        for root, directories, files in os.walk(path, onerror=walk_error):
            for name in directories + files:
                if _is_link(Path(root) / name):
                    raise ProjectError(f"Cannot delete a workspace containing links: {name}")
        shutil.rmtree(path, onerror=remove_readonly)
    except OSError as error:
        raise ProjectError(f"Cannot delete {path.name}: {error}") from error


def delete_profile(project: Path, profile_key: str) -> None:
    """Delete a profile from a project."""
    path = _profile_path(project, profile_key)
    if len(list_profiles(project, issues=[])) <= 1:
        raise ProjectError("A project must keep at least one profile.")
    if project_metadata(project)["default_profile"].casefold() == profile_key.casefold():
        raise ProjectError("Choose another default profile before deleting this one.")
    _delete_tree(path)


def delete_project(root: Path, project_key: str) -> None:
    """Delete an entire project and all its profiles."""
    path = _existing_child(root, project_key)
    if project_metadata(path)["short_name"] != path.name:
        raise ProjectError("Project metadata does not match the workspace directory.")
    _delete_tree(path)
