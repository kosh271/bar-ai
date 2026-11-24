"""Install an editable project without changing the stock BARbarIAn."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .projects import (
    ProjectError, _is_link, list_profiles, profile_files, project_metadata,
    validate_deploy_metadata, validate_deploy_name,
)


class InstallationError(Exception):
    """An AI could not be installed or removed safely."""


@dataclass(frozen=True)
class InstallResult:
    path: Path
    changed: bool


VERSION = "1"
OWNER = "BAR AI Editor"
MANIFEST = ".bar-ai-editor.json"
LOBBY_FILE = Path("LuaMenu") / "configs" / "gameConfig" / "byar" / "aiSimpleName.lua"
BEGIN = "-- BEGIN BAR AI EDITOR ENTRIES"
END = "-- END BAR AI EDITOR ENTRIES"
BASE = (
    'local names = VFS.Include("LuaMenu/configs/gameConfig/byar/aiSimpleName.lua", nil, VFS.ZIP)\n'
    "local subnameMap = {}\n"
    "local originalGetAiSimpleName = names.GetAiSimpleName\n"
    "local function GetAiSimpleName(name)\n"
    "    return subnameMap[name] or originalGetAiSimpleName(name)\n"
    "end\n"
    "local simpleAiOrder = names.simpleAiOrder\n"
    "local aiTooltip = names.aiTooltip\n"
    "return { GetAiSimpleName = GetAiSimpleName, simpleAiOrder = simpleAiOrder, aiTooltip = aiTooltip }\n"
)


def _link(path: Path) -> bool:
    return _is_link(path)


def _check_links(root: Path, path: Path) -> None:
    candidate = root
    if _link(candidate):
        raise InstallationError(f"Installation paths cannot contain links: {candidate}")
    for part in path.relative_to(root).parts:
        candidate = candidate / part
        if _link(candidate):
            raise InstallationError(f"Installation paths cannot contain links: {candidate}")


def _lua(value: str) -> str:
    return "'" + "".join(
        "\\%03d" % ord(char) if ord(char) < 32 or ord(char) == 127
        else "\\\\" if char == "\\" else "\\'" if char == "'" else char
        for char in value
    ) + "'"


def _metadata(project: Path) -> tuple[str, str, str]:
    try:
        info = project_metadata(project)
        if project.name != info["short_name"]:
            raise InstallationError("Project folder does not match its short name.")
        profiles = dict(list_profiles(project))
        validate_deploy_metadata(info, set(profiles))
        return info["short_name"], info["name"], info["default_profile"]
    except ProjectError as error:
        raise InstallationError(str(error)) from error


def _hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if _link(path):
            raise InstallationError(f"Symbolic links are not supported: {path}")
        if path.is_file() and path != root / MANIFEST:
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _snapshot(root: Path) -> dict[str, str] | None:
    if not root.exists():
        return None
    if _link(root) or not root.is_dir():
        raise InstallationError(f"The AI installation is not a regular directory: {root}")
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if _link(path):
            raise InstallationError(f"Symbolic links are not supported: {path}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            result[f"directory:{relative}"] = ""
        elif path.is_file():
            result[f"file:{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _managed(target: Path, short_name: str) -> bool:
    if _link(target / MANIFEST):
        raise InstallationError(f"Installation metadata cannot be a link: {target}")
    try:
        info = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallationError(f"Cannot read installation metadata in {target}: {error}") from error
    return isinstance(info, dict) and info.get("owner") == OWNER and info.get("short_name") == short_name


def _unchanged(target: Path) -> bool:
    try:
        info = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
        return isinstance(info, dict) and info.get("files") == _hashes(target)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallationError(f"Cannot check installed files in {target}: {error}") from error


def _info(stock: str, short_name: str, display: str) -> str:
    for key, value in (("shortName", short_name), ("version", VERSION), ("name", display)):
        pattern = rf"(\bkey\s*=\s*['\"]{key}['\"]\s*,\s*value\s*=\s*)['\"][^'\"]*['\"]"
        stock, count = re.subn(pattern, lambda match: match.group(1) + _lua(value), stock)
        if count != 1:
            raise InstallationError(f"Stock AIInfo.lua has an unsupported {key} entry.")
    return stock


def _options(stock: str, profiles: list[tuple[str, str]], default: str) -> str:
    pattern = r"(?m)^return\s+options\s*$"
    if len(re.findall(pattern, stock)) != 1:
        raise InstallationError("Stock AIOptions.lua has an unsupported return statement.")
    entries = ",\n".join(
        f"        {{ key = {_lua(key)}, name = {_lua(name)}, desc = {_lua(name)} }}"
        for key, name in profiles
    )
    addition = (
        "local foundProfile = false\n"
        "local foundGameConfig = false\n"
        "for _, option in ipairs(options) do\n"
        "    if option.key == 'profile' then\n"
        "        foundProfile = true\n"
        f"        option.def = {_lua(default)}\n"
        f"        option.items = {{\n{entries}\n        }}\n"
        "    elseif option.key == 'game_config' then\n"
        "        foundGameConfig = true\n"
        "        option.def = false\n"
        "    end\n"
        "end\n"
        "assert(foundProfile and foundGameConfig, 'Missing BARbarIAn options')\n"
        "return options"
    )
    return re.sub(pattern, lambda _match: addition, stock)


def _expected_hashes(project: Path, stock: Path, short_name: str, name: str, default: str) -> dict[str, str]:
    for filename in ("SkirmishAI.dll", "AIInfo.lua", "AIOptions.lua"):
        source = stock / filename
        if not source.is_file() or _link(source):
            raise InstallationError(f"Stock BARbarIAn file not found or is a link: {source}")
    profiles = list_profiles(project)
    info_text = _info((stock / "AIInfo.lua").read_text(encoding="utf-8"), short_name, name)
    options_text = _options((stock / "AIOptions.lua").read_text(encoding="utf-8"), profiles, default)
    result = {
        "SkirmishAI.dll": hashlib.sha256((stock / "SkirmishAI.dll").read_bytes()).hexdigest(),
        "AIInfo.lua": hashlib.sha256(info_text.replace("\n", os.linesep).encode("utf-8")).hexdigest(),
        "AIOptions.lua": hashlib.sha256(options_text.replace("\n", os.linesep).encode("utf-8")).hexdigest(),
    }
    for key, _name in profiles:
        profile = project / "profiles" / key
        files = profile_files(profile)
        if Path("script") / "init.as" not in files or Path("script") / "main.as" not in files:
            raise InstallationError(f"Profile {key} is missing init.as or main.as.")
        for relative in files:
            destination = (Path(relative.parts[0]) / key / Path(*relative.parts[1:])).as_posix()
            result[destination] = hashlib.sha256((profile / relative).read_bytes()).hexdigest()
    return result


def inspect_status(project: Path, data: Path, engine: str) -> dict[str, object]:
    """Compare a Studio project against its installation in the selected engine without mutating files."""
    short_name, name, default = _metadata(project)
    stock, target = _target(data, engine, short_name)
    lobby = data / LOBBY_FILE
    _check_links(data, lobby)
    if not target.exists():
        return {
            "installed": False,
            "managed": False,
            "status": "not_installed",
            "target": str(target),
            "message": "Not installed for this engine.",
        }
    _snapshot(target)
    if not _managed(target, short_name):
        return {
            "installed": True,
            "managed": False,
            "status": "modified_externally",
            "target": str(target),
            "message": "Unmanaged AI files exist at the target location.",
        }
    if not _unchanged(target):
        return {
            "installed": True,
            "managed": True,
            "status": "modified_externally",
            "target": str(target),
            "message": "Installed AI files were modified outside Studio.",
        }
    try:
        expected = _expected_hashes(project, stock, short_name, name, default)
    except ProjectError as error:
        raise InstallationError(str(error)) from error
    actual = _hashes(target)
    try:
        old_lobby = _read_lobby(lobby)
        new_lobby = _lobby_content(old_lobby, f"{short_name} {VERSION}", name)
    except InstallationError as error:
        return {
            "installed": True,
            "managed": True,
            "status": "modified_externally",
            "target": str(target),
            "message": str(error),
        }
    if actual == expected and old_lobby == new_lobby:
        return {
            "installed": True,
            "managed": True,
            "status": "up_to_date",
            "target": str(target),
            "message": "Installed and up to date.",
        }
    return {
        "installed": True,
        "managed": True,
        "status": "out_of_date",
        "target": str(target),
        "message": "Saved project changes are not yet installed.",
    }


def _prepare(project: Path, stock: Path, target: Path, short_name: str, name: str, default: str) -> None:
    for filename in ("SkirmishAI.dll", "AIInfo.lua", "AIOptions.lua"):
        source = stock / filename
        if not source.is_file() or source.is_symlink():
            raise InstallationError(f"Stock BARbarIAn file not found or is a link: {source}")
    profiles = list_profiles(project)
    target.mkdir()
    shutil.copy2(stock / "SkirmishAI.dll", target / "SkirmishAI.dll")
    (target / "AIInfo.lua").write_text(
        _info((stock / "AIInfo.lua").read_text(encoding="utf-8"), short_name, name), encoding="utf-8"
    )
    (target / "AIOptions.lua").write_text(
        _options((stock / "AIOptions.lua").read_text(encoding="utf-8"), profiles, default),
        encoding="utf-8",
    )
    for key, _name in profiles:
        profile = project / "profiles" / key
        files = profile_files(profile)
        if Path("script") / "init.as" not in files or Path("script") / "main.as" not in files:
            raise InstallationError(f"Profile {key} is missing init.as or main.as.")
        for relative in files:
            destination = target / relative.parts[0] / key / Path(*relative.parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile / relative, destination)
    manifest = {"owner": OWNER, "short_name": short_name, "files": _hashes(target)}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _lobby_entries(text: str) -> tuple[str, dict[str, str]]:
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise InstallationError("The lobby AI-name file has damaged editor markers; no files were changed.")
    if BEGIN not in text:
        if not all(re.search(pattern, text) for pattern in (
            r"\blocal\s+subnameMap\s*=", r"\blocal\s+simpleAiOrder\s*=",
            r"\blocal\s+aiTooltip\s*=", r"(?m)^return\s*\{",
            r"\breturn\s+subnameMap\s*\[\s*name\s*\]",
            r"\bGetAiSimpleName\s*=\s*GetAiSimpleName\b",
        )) or len(re.findall(r"(?m)^return\s*\{", text)) != 1:
            raise InstallationError("The existing lobby AI-name file has an unsupported structure; no files were changed.")
        return text, {}
    start = text.index(BEGIN)
    end = text.index(END, start) + len(END)
    block = text[start:end]
    lines = block.splitlines()
    if len(lines) < 3 or not lines[1].startswith("-- entries: "):
        raise InstallationError("The lobby AI-name editor entries are invalid; no files were changed.")
    try:
        entries = json.loads(lines[1][len("-- entries: "):])
    except json.JSONDecodeError as error:
        raise InstallationError("The lobby AI-name editor entries are invalid; no files were changed.") from error
    if not isinstance(entries, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in entries.items()
    ) or block != _lobby_block(entries):
        raise InstallationError("The lobby AI-name editor entries were modified; no files were changed.")
    after = end + (1 if text[end:end + 1] == "\n" else 0)
    return text[:start] + text[after:], entries


def _lobby_block(entries: dict[str, str]) -> str:
    lines = [BEGIN, "-- entries: " + json.dumps(entries, sort_keys=True, ensure_ascii=True)]
    for key, name in sorted(entries.items()):
        lines.extend((
            f"subnameMap[{_lua(key)}] = {_lua(name)}",
            f"simpleAiOrder[{_lua(key)}] = 2",
            f"aiTooltip[{_lua(key)}] = {_lua(name)}",
        ))
    return "\n".join([*lines, END])


def _lobby_content(current: str | None, key: str, name: str | None) -> str | None:
    base, entries = _lobby_entries(BASE if current is None else current)
    if name is None:
        entries.pop(key, None)
    else:
        if key not in entries and re.search(r"\[\s*['\"]" + re.escape(key) + r"['\"]\s*\]", base):
            raise InstallationError(f"The lobby already contains an unmanaged entry for {key}.")
        entries[key] = name
    if not entries:
        return None if base == BASE else base
    match = re.search(r"(?m)^return\s*\{", base)
    if match is None:
        raise InstallationError("The lobby AI-name file has no supported return statement.")
    return base[:match.start()] + _lobby_block(entries) + "\n" + base[match.start():]


def _read_lobby(path: Path) -> str | None:
    if _link(path):
        raise InstallationError(f"The lobby AI-name file is a symbolic link: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as file:
            return file.read()
    except FileNotFoundError:
        return None


def _write_lobby(path: Path, expected: str | None, content: str | None) -> None:
    if _read_lobby(path) != expected:
        raise InstallationError("The shared lobby AI-name file changed during installation; retry.")
    if content is None:
        if expected is not None:
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as file:
            temporary = file.name
            file.write(content)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _target(data: Path, engine: str, short_name: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", engine) or engine in (".", ".."):
        raise InstallationError("Invalid engine folder.")
    engine_dir = data / "engine" / engine
    if not engine_dir.is_dir() or engine_dir.is_symlink():
        raise InstallationError(f"Engine folder not found or is a link: {engine_dir}")
    root = engine_dir / "AI" / "Skirmish"
    stock = root / "BARb" / "stable"
    parent = root / short_name
    _check_links(data, parent / VERSION)
    if parent.exists():
        for child in parent.iterdir():
            if child.name.startswith(".bar-ai-"):
                raise InstallationError(f"An interrupted installation needs attention: {child}")
            if child.name != VERSION:
                raise InstallationError(f"Another version already uses this AI name: {child}")
    return stock, parent / VERSION


def _other_installation(data: Path, engine: str, short_name: str) -> bool:
    engines = data / "engine"
    for other in engines.iterdir():
        if other.name == engine or not other.is_dir():
            continue
        target = other / "AI" / "Skirmish" / short_name / VERSION
        if target.exists():
            _check_links(data, target)
            if _managed(target, short_name):
                return True
    return False


def list_managed(data: Path, engines: tuple[str, ...]) -> tuple[list[dict[str, str]], list[str]]:
    """List deployments by their on-disk manifests, including deleted projects."""
    entries = []
    issues = []
    names = {}
    try:
        lobby = data / LOBBY_FILE
        _check_links(data, lobby)
        _base, names = _lobby_entries(_read_lobby(lobby) or BASE)
    except (OSError, InstallationError, UnicodeError) as error:
        issues.append(str(error))
    for engine in engines:
        directory = data / "engine" / engine / "AI" / "Skirmish"
        try:
            _check_links(data, directory)
            if not directory.is_dir():
                continue
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except (OSError, InstallationError) as error:
            issues.append(f"{engine}: {error}")
            continue
        for parent in children:
            target = parent / VERSION
            manifest = target / MANIFEST
            try:
                _check_links(data, target)
                if not manifest.exists() and not _link(manifest):
                    continue
                _check_links(data, manifest)
                validate_deploy_name(parent.name)
                if not _managed(target, parent.name):
                    continue
                entries.append({
                    "project": parent.name, "name": names.get(f"{parent.name} {VERSION}", parent.name),
                    "engine": engine, "path": str(target),
                })
            except (OSError, InstallationError, ProjectError) as error:
                issues.append(f"{engine}: {error}")
    return entries, issues


def install(project: Path, data: Path, engine: str, confirm: Callable[[str], bool]) -> InstallResult:
    short_name, name, default = _metadata(project)
    stock, target = _target(data, engine, short_name)
    lobby = data / LOBBY_FILE
    _check_links(data, lobby)
    old_lobby = _read_lobby(lobby)
    new_lobby = _lobby_content(old_lobby, f"{short_name} {VERSION}", name)
    if old_lobby is not None and BEGIN not in old_lobby and not confirm(
        f"Extend the existing shared lobby AI-name file without replacing its entries?\n\n{lobby}"
    ):
        raise InstallationError("Installation cancelled; no files were changed.")
    original = _snapshot(target)
    target_is_current = False
    if original is not None:
        managed = _managed(target, short_name)
        target_is_current = managed and _unchanged(target)
        if not target_is_current and not confirm(
            f"Replace the existing {'modified' if managed else 'unmanaged'} AI files?\n\n{target}\n\n"
            "The replaced files will not be restored on removal."
        ):
            raise InstallationError("Installation cancelled; no files were changed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".bar-ai-stage-", dir=target.parent))
    staged = temp / VERSION
    backup = temp / "previous"
    safe_to_clean = True
    try:
        try:
            _prepare(project, stock, staged, short_name, name, default)
        except ProjectError as error:
            raise InstallationError(str(error)) from error
        if _snapshot(target) != original:
            raise InstallationError("The installed AI changed during installation; retry.")
        if target_is_current and old_lobby == new_lobby and _snapshot(staged) == original:
            return InstallResult(target, False)
        had_target = original is not None
        if had_target:
            target.rename(backup)
        try:
            staged.rename(target)
            _write_lobby(lobby, old_lobby, new_lobby)
        except (OSError, InstallationError) as error:
            try:
                if target.exists():
                    target.rename(staged)
                if had_target:
                    backup.rename(target)
            except OSError as rollback_error:
                safe_to_clean = False
                raise InstallationError(
                    f"Installation failed ({error}) and rollback failed ({rollback_error}). "
                    f"Files were retained for recovery in {temp}."
                ) from rollback_error
            raise
    finally:
        if safe_to_clean:
            shutil.rmtree(temp)
    return InstallResult(target, True)


def remove(project: Path, data: Path, engine: str, confirm: Callable[[str], bool]) -> Path:
    try:
        short_name = project_metadata(project)["short_name"]
    except ProjectError as error:
        raise InstallationError(str(error)) from error
    if project.name != short_name:
        raise InstallationError("Project folder does not match its short name.")
    return remove_installed(short_name, data, engine, confirm)


def remove_installed(short_name: str, data: Path, engine: str, confirm: Callable[[str], bool]) -> Path:
    try:
        validate_deploy_name(short_name)
    except ProjectError as error:
        raise InstallationError(str(error)) from error
    _stock, target = _target(data, engine, short_name)
    if not target.is_dir() or not _managed(target, short_name):
        raise InstallationError(f"No editor-managed installation found at {target}.")
    original = _snapshot(target)
    if not _unchanged(target) and not confirm(
        f"Installed AI files have been modified. Remove them anyway?\n\n{target}"
    ):
        raise InstallationError("Removal cancelled; no files were changed.")
    lobby = data / LOBBY_FILE
    _check_links(data, lobby)
    old_lobby = _read_lobby(lobby)
    new_lobby = old_lobby if _other_installation(data, engine, short_name) else _lobby_content(
        old_lobby, f"{short_name} {VERSION}", None
    )
    temp = Path(tempfile.mkdtemp(prefix=".bar-ai-remove-", dir=target.parent))
    safe_to_clean = True
    try:
        backup = temp / VERSION
        if _snapshot(target) != original:
            raise InstallationError("The installed AI changed during removal; retry.")
        target.rename(backup)
        try:
            _write_lobby(lobby, old_lobby, new_lobby)
        except (OSError, InstallationError) as error:
            try:
                backup.rename(target)
            except OSError as rollback_error:
                safe_to_clean = False
                raise InstallationError(
                    f"Removal failed ({error}) and rollback failed ({rollback_error}). "
                    f"Files were retained for recovery in {temp}."
                ) from rollback_error
            raise
    finally:
        if safe_to_clean:
            shutil.rmtree(temp)
    return target
