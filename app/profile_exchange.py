"""Saved profile ZIP exchange and two-profile merge."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO

from . import project_archive, projects


class StalePreview(projects.ProjectError):
    """The saved inputs changed since the merge was previewed."""


def _path_entries(path: str):
    parts = projects.workspace_path_parts(path)
    for length in range(1, len(parts) + 1):
        prefix = parts[:length]
        yield tuple(part.casefold() for part in prefix), (prefix, length == len(parts))


def _check_files(files: dict[str, bytes]) -> None:
    if not {"script/init.as", "script/main.as"} <= files.keys() or not any(
        path.startswith("config/") for path in files
    ):
        raise projects.ProjectError("A profile needs JSON configuration, script/init.as, and script/main.as.")
    entries: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    total = 0
    for path, data in files.items():
        total += len(data)
        projects.check_workspace_size(len(data), total, len(files) + 1)
        for key, entry in _path_entries(path):
            if entries.setdefault(key, entry) != entry:
                raise projects.ProjectError(f"Profile contains conflicting file paths: {path}")
        try:
            data.decode("utf-8")
        except UnicodeError as error:
            raise projects.ProjectError(f"Profile file must be UTF-8: {path}") from error


def read(profile: Path) -> tuple[dict[str, str], dict[str, bytes]]:
    metadata_path = profile / "profile.json"
    if projects._is_link(metadata_path):
        raise projects.ProjectError("Profile metadata cannot be a link.")
    metadata = projects._read_metadata(metadata_path, ("name",))
    for directory in (profile / "config", profile / "script"):
        if projects._is_link(directory):
            raise projects.ProjectError("Profile directories cannot be links.")
    files = {}
    total = 0
    for relative in projects.profile_files(profile):
        path = profile / relative
        if any(projects._is_link(profile.joinpath(*relative.parts[:length]))
               for length in range(1, len(relative.parts) + 1)):
            raise projects.ProjectError(f"Profile paths cannot contain links: {relative}")
        size = path.stat().st_size
        total += size
        projects.check_workspace_size(size, total, len(files) + 2)
        with path.open("rb") as source:
            files[relative.as_posix()] = source.read(projects.MAX_WORKSPACE_FILE_BYTES + 1)
    _check_files(files)
    return metadata, files


def _metadata_bytes(metadata: dict[str, str], files: dict[str, bytes]) -> bytes:
    data = (json.dumps(metadata, indent=2) + "\n").encode("utf-8")
    projects.check_workspace_size(len(data), len(data) + sum(map(len, files.values())), len(files) + 1)
    return data


def write_archive(profile: Path, output: BinaryIO) -> None:
    metadata, files = read(profile)
    metadata_data = _metadata_bytes(metadata, files)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("profile.json", metadata_data)
        for path, data in files.items():
            archive.writestr(path, data)
    if output.tell() > project_archive.MAX_ARCHIVE_BYTES:
        raise projects.ProjectError("Compressed profile archive exceeds its size limit.")


def _destination(project: Path, name: str) -> Path:
    projects.list_profiles(project)
    return projects._unique_child(project / "profiles", projects._key(name, project=False))


def load_archive(project: Path, name: str, source: BinaryIO) -> Path:
    if source.seek(0, os.SEEK_END) > project_archive.MAX_ARCHIVE_BYTES:
        raise projects.ProjectError("Compressed profile archive exceeds its size limit.")
    target = _destination(project, name)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=target.parent) as temporary:
        staged = Path(temporary)
        try:
            with zipfile.ZipFile(source) as archive:
                project_archive._extract(archive, staged)
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as error:
            raise projects.ProjectError(f"Invalid profile ZIP: {error}") from error
        metadata, files = read(staged)
        expected = {"profile.json", *files}
        expected.update(parent.as_posix() for path in files for parent in Path(path).parents if parent != Path("."))
        if {path.relative_to(staged).as_posix() for path in staged.rglob("*")} != expected:
            raise projects.ProjectError("A profile ZIP may only contain profile metadata, JSON configuration, and AngelScript files.")
        metadata["name"] = name.strip()
        (staged / "profile.json").write_bytes(_metadata_bytes(metadata, files))
        staged.rename(target)
    return target


_QUOTED = r'"(?:[^"\\]|\\.)*"'
_COMMENTS = re.compile(rf'{_QUOTED}|//[^\r\n]*|/\*[\s\S]*?\*/')
_TRAILING = re.compile(rf'{_QUOTED}|,(?=[ \t\r\n]*[}}\]])')
_WHITESPACE = " \t\r\n"
_SPACE = re.compile(r"[ \t\r\n]*")


def _unique_object(pairs: list[tuple]) -> dict:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate configuration key.")
    return value


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


_DECODER = json.JSONDecoder(
    object_pairs_hook=_unique_object, parse_int=Decimal, parse_float=Decimal, parse_constant=_invalid_constant,
)


@dataclass
class _Node:
    start: int
    end: int
    value: object = None
    # Each member stores the start of its quoted key as well as the child span.
    members: dict[str, tuple[int, _Node]] | None = None


def _parse_config(text: str) -> _Node:
    clean = " " + text[1:] if text.startswith("\ufeff") else text
    clean = _COMMENTS.sub(
        lambda m: re.sub(r"[^\r\n]", " ", m[0]) if m[0].startswith("/") else m[0], clean,
    )
    def mask_trailing(match: re.Match) -> str:
        if match[0] != ",":
            return match[0]
        previous = match.start() - 1
        while previous >= 0 and clean[previous] in _WHITESPACE:
            previous -= 1
        return " " if previous >= 0 and clean[previous] not in "[{" else ","

    clean = _TRAILING.sub(mask_trailing, clean)

    def space(offset: int) -> int:
        return _SPACE.match(clean, offset).end()

    def parse(start: int) -> _Node:
        if clean[start:start + 1] != "{":
            value, end = _DECODER.raw_decode(clean, start)
            return _Node(start, end, value)
        members = {}
        pos = space(start + 1)
        while clean[pos:pos + 1] != "}":
            key, end = _DECODER.raw_decode(clean, pos)
            colon = space(end)
            if not isinstance(key, str) or key in members or clean[colon:colon + 1] != ":":
                raise ValueError("Invalid or duplicate configuration key.")
            child = parse(space(colon + 1))
            members[key] = (pos, child)
            pos = space(child.end)
            if clean[pos:pos + 1] == "}":
                break
            if clean[pos:pos + 1] != ",":
                raise ValueError("Missing configuration separator.")
            pos = space(pos + 1)
            if clean[pos:pos + 1] == "}":
                raise ValueError("Unexpected configuration separator.")
        return _Node(start, pos + 1, members=members)

    root = parse(space(0))
    if space(root.end) != len(clean):
        raise ValueError("Unexpected text after JSON value.")
    return root


def _equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _snippet(text: str) -> str:
    short = text if len(text) <= 160 else text[:157] + "…"
    return short.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _preview_value(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(map(_preview_value, value)) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_preview_value(key)}: {_preview_value(value[key])}" for key in sorted(value)
        ) + "}"
    return json.dumps(value, ensure_ascii=False)


def _conflict_values(left: str, right: str, a: _Node, b: _Node) -> dict[str, str]:
    preferred, other = left[a.start:a.end], right[b.start:b.end]
    first, second = _snippet(preferred), _snippet(other)
    if first != second:
        return {"preferred": first, "other": second}
    if isinstance(a.value, list) and isinstance(b.value, list):
        common = min(len(a.value), len(b.value))
        index = next((i for i in range(common) if not _equal(a.value[i], b.value[i])), common)

        def item(values: list) -> str:
            return _preview_value(values[index]) if index < len(values) else "(no item)"

        location = f"Array index {index}"
        preferred, other = item(a.value), item(b.value)
        first_item, second_item = _snippet(preferred), _snippet(other)
        if first_item != second_item:
            return {"preferred": first_item, "other": second_item, "location": location}
    else:
        location = ""
    offset = next((i for i, (x, y) in enumerate(zip(preferred, other)) if x != y),
                  min(len(preferred), len(other)))
    start = max(0, offset - 48)
    prefix = "…" if start else ""
    return {"preferred": _snippet(prefix + preferred[start:offset + 100]),
            "other": _snippet(prefix + other[start:offset + 100]),
            "location": location or f"Character {offset + 1}"}


def _merge_config(preferred: str, other: str) -> tuple[str, list[dict], int]:
    left, right = _parse_config(preferred), _parse_config(other)
    changes: list[tuple[int, str]] = []
    conflicts = []
    conflict_count = 0
    newline = "\r\n" if "\r\n" in preferred else "\n"

    def combine(a: _Node, b: _Node, path: tuple[str, ...]) -> None:
        nonlocal conflict_count
        if a.members is None or b.members is None:
            if a.members is not None or b.members is not None or not _equal(a.value, b.value):
                conflict_count += 1
                if len(conflicts) < 20:
                    setting = "".join("/" + key.replace("~", "~0").replace("/", "~1") for key in path)
                    conflicts.append({"setting": _snippet(setting), **_conflict_values(preferred, other, a, b)})
            return
        additions = []
        for key, (start, child) in b.members.items():
            if key in a.members:
                combine(a.members[key][1], child, (*path, key))
            else:
                additions.append(other[start:child.end])
        if not additions:
            return
        closing = a.end - 1
        newline_offset = preferred.rfind("\n", a.start, closing)
        line_start = newline_offset + 1
        indent = preferred[line_start:closing] if newline_offset != -1 else None
        if indent is not None and not indent.strip():
            insertion = indent + "  " + ("," + newline + indent + "  ").join(additions) + newline
            changes.append((line_start, insertion))
        else:
            changes.append((closing, ("" if closing == a.start + 1 else " ") + ", ".join(additions)))
        if a.members:
            last = next(reversed(a.members.values()))[1]
            # The original text can have a trailing comma masked by the parser.
            between = _COMMENTS.sub(lambda m: " " if m[0].startswith("/") else m[0],
                                    preferred[last.end:closing])
            if "," not in between:
                if last.end == closing:
                    offset, insertion = changes.pop()
                    changes.append((offset, "," + insertion))
                else:
                    changes.append((last.end, ","))

    combine(left, right, ())
    if not changes:
        return preferred, conflicts, conflict_count
    fragments = []
    offset = 0
    for position, insertion in sorted(changes, key=lambda change: change[0]):
        fragments.extend((preferred[offset:position], insertion))
        offset = position
    fragments.append(preferred[offset:])
    merged = "".join(fragments)
    _parse_config(merged)
    return merged, conflicts, conflict_count


_ORDERED_LIST = {"factory": "unit", "response": "vs"}


@dataclass
class _OrderedConfig:
    sections: dict[str, set[str]]
    lists: dict[tuple[str, str], dict[str, object]]


def _ordered_config(files: dict[str, bytes]) -> _OrderedConfig:
    config = _OrderedConfig({}, {})
    for path, data in files.items():
        if not path.lower().endswith(".json"):
            continue
        # Most files contain unrelated settings. Backslashes can introduce escaped keys.
        if not any(token in data for token in (b'"factory"', b'"response"', b"\\")):
            continue
        try:
            node = _parse_config(data.decode("utf-8"))
        except (ValueError, RecursionError, InvalidOperation):
            continue
        for kind, list_key in _ORDERED_LIST.items():
            entries = (node.members or {}).get(kind)
            if not entries:
                continue
            config.sections.setdefault(path, set()).add(kind)
            for key, (_, settings) in (entries[1].members or {}).items():
                layers = config.lists.setdefault((kind, key), {})
                indexed_list = (settings.members or {}).get(list_key)
                if indexed_list:
                    layers[path.casefold()] = indexed_list[1].value
    return config


def _ordered_config_conflicts(preferred: _OrderedConfig, other: _OrderedConfig, scripts_differ: bool) -> set[str]:
    conflicts = set()
    for key in preferred.lists.keys() & other.lists.keys():
        left, right = preferred.lists[key], other.lists[key]
        if not _equal(left, right):
            conflicts.add(key[0])
        elif scripts_differ and left:
            first = next(iter(left.values()))
            if any(not _equal(first, values) for values in left.values()):
                conflicts.add(key[0])
    return conflicts


def source_revision(preferred: dict[str, bytes], other: dict[str, bytes]) -> str:
    digests = [
        (path, *(hashlib.sha256(files[path]).hexdigest() if path in files else None for files in (preferred, other)))
        for path in sorted(preferred.keys() | other.keys())
    ]
    return hashlib.sha256(json.dumps(digests).encode("ascii")).hexdigest()


def merge(preferred: dict[str, bytes], other: dict[str, bytes]) -> tuple[dict[str, bytes], list[dict]]:
    files = {}
    report = []
    preferred_entries = {key: entry for path in preferred for key, entry in _path_entries(path)}
    preferred_config, other_config = _ordered_config(preferred), _ordered_config(other)
    paths = preferred.keys() | other.keys()
    scripts_differ = any(preferred.get(path) != other.get(path) for path in paths if path.startswith("script/"))
    # Layered files can inherit a positional list from another file loaded by the scripts.
    ordered_conflicts = _ordered_config_conflicts(preferred_config, other_config, scripts_differ)
    for path in sorted(paths):
        a, b = preferred.get(path), other.get(path)
        conflicts = []
        conflict_count = 0
        sections = ordered_conflicts.intersection(
            set(preferred_config.sections.get(path, ())).union(other_config.sections.get(path, ()))
        )
        if a != b and sections:
            if a is not None:
                files[path] = a
            kinds = " and ".join(sorted(sections))
            report.append({"path": path, "action": "layer" if a is not None else "omitted",
                           "conflict_count": 1, "conflicts": [{"reason":
                               f"The {kinds} layers depend on ordered lists. The preferred profile supplies these layers together."
                           }]})
            continue
        if a is None:
            for key, entry in _path_entries(path):
                existing = preferred_entries.get(key)
                if existing is not None and existing != entry:
                    conflicting_path = "/".join(existing[0])
                    report.append({"path": path, "action": "omitted", "conflict_count": 1,
                                   "conflicts": [{"reason": f"This path conflicts with {conflicting_path}."}]})
                    break
            else:
                files[path] = b
                report.append({"path": path, "action": "other", "conflict_count": 0, "conflicts": []})
            continue
        if b is None:
            data, action = a, "preferred"
        elif a == b:
            files[path] = a
            continue
        else:
            data, action = a, "preferred"
            if path.lower().endswith(".json"):
                try:
                    original = a.decode("utf-8")
                    merged, conflicts, conflict_count = _merge_config(original, b.decode("utf-8"))
                    data = merged.encode("utf-8")
                    if merged != original:
                        action = "settings"
                    elif not conflict_count:
                        action = "text"
                except (ValueError, RecursionError, InvalidOperation, UnicodeError):
                    conflicts = [{"reason": "Configuration could not be merged as JSON."}]
                    conflict_count = 1
            else:
                conflicts = [{"reason": "Scripts differ."}]
                conflict_count = 1
        files[path] = data
        report.append({"path": path, "action": action, "conflicts": conflicts, "conflict_count": conflict_count})
    _check_files(files)
    return files, report


def save_merge(project: Path, name: str, files: dict[str, bytes]) -> Path:
    target = _destination(project, name)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=target.parent) as temporary:
        staged = Path(temporary)
        for path, data in files.items():
            destination = staged / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (staged / "profile.json").write_bytes(_metadata_bytes({"name": name.strip()}, files))
        staged.rename(target)
    return target
