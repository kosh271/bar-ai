"""Snapshot and materialize numeric profile sweeps for isolated headless runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from . import headless, installation, projects

MAX_VARIANTS = 16
_SCRIPT_NUMBER = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?[fFuU]?"
_JSON_NUMBER = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
_COMMON_TOKENS = r'''(?P<ignored>[^\S\r\n]+|\r\n?|\n|//[^\r\n]*|/\*[\s\S]*?(?:\*/|\Z)|"""[\s\S]*?(?:"""|\Z)|"(?:\\[\s\S]|[^"\\])*(?:"|\Z)|'(?:\\[\s\S]|[^'\\])*(?:'|\Z))'''
_JSON_TOKENS = re.compile(_COMMON_TOKENS + rf"|(?P<number>{_JSON_NUMBER})(?![\w.])|\w+|.", re.DOTALL)
_SCRIPT_TOKENS = re.compile(
    r"(?P<directive>(?:^|(?<=[\ufeff\r]))[ \t]*\#[^\r\n]*)|" + _COMMON_TOKENS + r"|0[xXbBoOdD][0-9A-Za-z_]+"
    + rf"|(?P<number>{_SCRIPT_NUMBER})(?![\w.])|[A-Za-z_][\w]*|\+\+|--|.", re.DOTALL | re.MULTILINE
)
_UNARY_AFTER = frozenset({
    "", "(", "{", "[", "=", ",", ":", ";", "?", "!", "~", "*", "/", "%", "+", "-", "|", "&", "^",
    "<", ">", "return", "case",
})


def _value(value: object) -> int | float:
    if type(value) not in (int, float) or abs(value) > 2**53 - 1 or not math.isfinite(value):
        raise headless.MatchError("Sweep values must be finite numbers in the supported numeric range.")
    return value


@dataclass(frozen=True)
class _Literal:
    text: str
    start: int
    end: int
    script: bool
    adjacent_operator: bool

    def write(self, value: int | float) -> bytes:
        value = _value(value)
        token = self.text[self.start:self.end]
        replacement = json.dumps(value, allow_nan=False)
        if self.script:
            suffix = token[-1] if token[-1] in "fFuU" else ""
            floating = suffix.lower() == "f" or any(char in token for char in ".eE")
            if floating:
                if not any(char in replacement for char in ".eE"):
                    replacement += ".0"
            else:
                if value != int(value) or (suffix.lower() == "u" and value < 0):
                    raise headless.MatchError("This script literal requires an integer of the same signedness.")
                replacement = str(int(value))
            replacement += suffix
            if self.adjacent_operator and replacement.startswith("-"):
                replacement = " " + replacement
        return (self.text[:self.start] + replacement + self.text[self.end:]).encode("utf-8")


def _literal(raw: bytes, suffix: str, line: int, column: int) -> _Literal:
    if type(line) is not int or type(column) is not int or line < 1 or column < 1:
        raise headless.MatchError("Sweep line and column must be positive integers.")
    if suffix not in (".json", ".as"):
        raise headless.MatchError("Select a profile JSONC or AngelScript file.")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise headless.MatchError("The sweep file must contain UTF-8 text.") from error
    line_start = int(text.startswith("\ufeff"))
    endings = re.finditer(r"\r\n?|\n", text)
    for _ in range(line - 1):
        ending = next(endings, None)
        if ending is None:
            raise headless.MatchError("Sweep line is outside the source file.")
        line_start = ending.end()
    ending = next(endings, None)
    offset = line_start + column - 1
    if offset >= (ending.start() if ending else len(text)):
        raise headless.MatchError("Sweep column is outside the source line.")

    script = suffix == ".as"
    token_pattern = _SCRIPT_TOKENS if script else _JSON_TOKENS
    previous = before_previous = ""
    previous_end = -1
    for token in token_pattern.finditer(text, int(text.startswith("\ufeff"))):
        start, end = token.span()
        if token.lastgroup == "number":
            unary_sign = script and previous in ("-", "+") and before_previous in _UNARY_AFTER
            if unary_sign and previous_end == start:
                start -= 1
            if start <= offset < end:
                if unary_sign and previous_end != token.start():
                    raise headless.MatchError("Select a script number with its unary sign attached.")
                return _Literal(text, start, end, script, start > 0 and text[start - 1] in "+-")
        if start > offset:
            break
        if token.lastgroup != "ignored":
            before_previous, previous = previous, token[0]
            previous_end = end
    raise headless.MatchError("Select a supported decimal numeric literal outside strings and comments.")


def replace_literal(raw: bytes, suffix: str, line: int, column: int, value: int | float) -> bytes:
    """Replace a decimal literal at a one-based line and Unicode column."""
    return _literal(raw, suffix, line, column).write(value)


@dataclass(frozen=True)
class SweepVariant:
    id: str
    label: str
    player: headless.Player


@dataclass(frozen=True)
class PreparedSweep:
    engine_dir: Path
    variants: tuple[SweepVariant, ...]
    promotion: dict


def _never_stop() -> bool:
    return False


def _check_stop(should_stop: Callable[[], bool]) -> None:
    if should_stop():
        raise headless.MatchError("Stopped while preparing sweep variants.")


def _path(name: str) -> tuple[str, ...]:
    if not isinstance(name, str):
        raise headless.MatchError("Select a file in the source profile.")
    try:
        parts = projects.workspace_path_parts(name)
    except projects.ProjectError as error:
        raise headless.MatchError("Select a regular file in the source profile.") from error
    if len(parts) < 2 or Path(parts[-1]).suffix.lower() != {"config": ".json", "script": ".as"}.get(parts[0]):
        raise headless.MatchError("Select a profile configuration or script file.")
    return parts


def _regular_files(root: Path, should_stop: Callable[[], bool]) -> Iterator[tuple[str, ...]]:
    directories = [()]
    while directories:
        parent = directories.pop()
        headless.artifact_stat(root, *parent, directory=True)
        for path in sorted(root.joinpath(*parent).iterdir()):
            _check_stop(should_stop)
            parts = (*parent, path.name)
            is_dir = path.is_dir()
            headless.artifact_stat(root, *parts, directory=is_dir)
            if is_dir:
                directories.append(parts)
            else:
                yield parts


def _read_profile(
    root: Path, should_stop: Callable[[], bool] = _never_stop,
) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    total = 0
    for folder, extension in (("config", ".json"), ("script", ".as")):
        headless.artifact_stat(root, folder, directory=True)
        for parts in _regular_files(root / folder, should_stop):
            if Path(parts[-1]).suffix.lower() != extension:
                continue
            name = "/".join((folder, *parts))
            _path(name)
            with headless.open_artifact(root, folder, *parts) as source:
                size = os.fstat(source.fileno()).st_size
                projects.check_workspace_size(size, total + size, len(contents) + 1)
                data = source.read(projects.MAX_WORKSPACE_FILE_BYTES + 1)
                if len(data) != size:
                    raise headless.MatchError("The source profile changed while it was being read.")
            contents[name] = data
            total += size
    if not {"script/init.as", "script/main.as"}.issubset(contents):
        raise headless.MatchError("The sweep source profile must contain init.as and main.as.")
    return contents


def _digest(contents: dict[str, bytes]) -> str:
    manifest = [(name, hashlib.sha256(raw).hexdigest()) for name, raw in sorted(contents.items())]
    return hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_profile(root: Path, contents: dict[str, bytes], should_stop: Callable[[], bool] = _never_stop) -> None:
    root.mkdir(parents=True)
    (root / "config").mkdir()
    (root / "script").mkdir()
    for name, raw in contents.items():
        _check_stop(should_stop)
        path = root.joinpath(*_path(name))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(raw)


def _copy_engine(source: Path, target: Path, should_stop: Callable[[], bool]) -> None:
    target.mkdir()
    for parts in _regular_files(source, should_stop):
        file = target.joinpath(*parts)
        file.parent.mkdir(parents=True, exist_ok=True)
        with headless.open_artifact(source, *parts) as original, file.open("xb") as output:
            while chunk := original.read(1024 * 1024):
                _check_stop(should_stop)
                output.write(chunk)


@contextmanager
def prepare(
    projects_root: Path, workspace: Path, sweep: dict, baseline: headless.Player, engine_dir: Path,
    *, should_stop: Callable[[], bool] = _never_stop,
) -> Iterator[PreparedSweep]:
    """Provide an isolated engine and candidate players, leaving the source snapshot for promotion."""
    if not isinstance(sweep, dict) or sweep.get("project") != baseline.short_name or sweep.get("profile") != baseline.profile:
        raise headless.MatchError("Select the saved project and profile of the swept player.")
    variants = sweep.get("values")
    if not isinstance(variants, list) or not 2 <= len(variants) <= MAX_VARIANTS:
        raise headless.MatchError(f"Enter between 2 and {MAX_VARIANTS} sweep values.")
    values = [_value(value) for value in variants]
    if len(set(values)) != len(values):
        raise headless.MatchError("Sweep values must be distinct.")
    path = "/".join(_path(sweep.get("path")))
    line, column = sweep.get("line"), sweep.get("column")
    _check_stop(should_stop)
    try:
        project = projects._existing_child(projects_root, baseline.short_name)
        profile = projects._profile_path(project, baseline.profile)
        headless.artifact_stat(projects_root, baseline.short_name, "profiles", baseline.profile, directory=True)
        files = _read_profile(profile, should_stop)
        if files != _read_profile(profile, should_stop):
            raise headless.MatchError("The source profile changed while it was being read. Retry the sweep.")
        if path not in files:
            raise headless.MatchError("The selected file is absent from the saved source profile.")
        literal = _literal(files[path], Path(path).suffix.lower(), line, column)
        patched_values = [literal.write(value) for value in values]
        headless.artifact_stat(workspace.parent, directory=True)
        create = workspace.resolve()
        for protected in (projects_root.resolve(), engine_dir.resolve()):
            if create.is_relative_to(protected) or protected.is_relative_to(create):
                raise headless.MatchError("The sweep workspace must be separate from the projects and BAR engine.")
        _check_stop(should_stop)
        workspace.mkdir(exist_ok=True)
        headless.artifact_stat(workspace, directory=True)
        saved_source = workspace / "source"
        if os.path.lexists(saved_source):
            raise headless.MatchError("The experiment already has a source snapshot.")
        with tempfile.TemporaryDirectory(prefix=".runtime-", dir=workspace) as temporary:
            transient = Path(temporary)
            private_engine = transient / "engine"
            _copy_engine(engine_dir, private_engine, should_stop)
            short_name = "StudioSweep" + uuid4().hex[:16]
            staged_project = transient / short_name
            records, candidates = {}, []
            for index, (value, patched) in enumerate(zip(values, patched_values), 1):
                candidate_id = f"v{index}"
                label = f"{path.rsplit('/', 1)[-1]} = {value}"
                config = {**files, path: patched}
                staged_profile = staged_project / "profiles" / candidate_id
                _write_profile(staged_profile, config, should_stop)
                projects._write_metadata(staged_profile / "profile.json", {"name": label})
                records[candidate_id] = {"value": value, "digest": _digest(config)}
                candidates.append(SweepVariant(candidate_id, label, replace(
                    baseline, short_name=short_name, version=installation.VERSION, profile=candidate_id, name=short_name
                )))
            skirmish = private_engine / "AI" / "Skirmish"
            stock = skirmish / "BARb" / "stable"
            destination = skirmish / short_name / installation.VERSION
            destination.parent.mkdir()
            installation._prepare(staged_project, stock, destination, short_name, short_name, "v1")
            _check_stop(should_stop)
            provenance = {
                "project": baseline.short_name, "profile": baseline.profile, "path": path,
                "line": line, "column": column, "source_digest": _digest(files), "variants": records,
                "ai_sha256": hashlib.sha256((destination / "SkirmishAI.dll").read_bytes()).hexdigest(),
            }
            staged_source = transient / "source"
            _write_profile(staged_source, files, should_stop)
            _check_stop(should_stop)
            staged_source.rename(saved_source)
            yield PreparedSweep(private_engine, tuple(candidates), provenance)
    except (OSError, UnicodeError, projects.ProjectError, installation.InstallationError) as error:
        raise headless.MatchError(f"Cannot prepare sweep variants: {error}") from error


def promote(projects_root: Path, workspace: Path, promotion: dict, variant_id: str, name: str) -> Path:
    """Publish a new profile from the exact source snapshot and recorded candidate value."""
    if not isinstance(name, str):
        raise headless.MatchError("Enter a name for the new profile.")
    try:
        project = projects._existing_child(projects_root, promotion["project"])
        profiles_root = project / "profiles"
        headless.artifact_stat(projects_root, project.name, "profiles", directory=True)
        headless.artifact_stat(workspace, "source", directory=True)
        snapshot = _read_profile(workspace / "source")
        if _digest(snapshot) != promotion["source_digest"]:
            raise headless.MatchError("The saved sweep snapshot no longer matches its recorded digest.")
        path = "/".join(_path(promotion["path"]))
        candidate = promotion["variants"][variant_id]
        raw = snapshot[path]
        snapshot[path] = replace_literal(
            raw, Path(path).suffix.lower(), promotion["line"], promotion["column"], candidate["value"]
        )
        if _digest(snapshot) != candidate["digest"]:
            raise headless.MatchError("The reconstructed candidate does not match the variant that was tested.")
        key = projects._key(name, project=False)
        target = projects._unique_child(profiles_root, key)
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=profiles_root) as temporary:
            staged = Path(temporary) / key
            _write_profile(staged, snapshot)
            projects._write_metadata(staged / "profile.json", {
                "name": name.strip(),
                "source": f"Sweep {workspace.name}/{variant_id} from {promotion['profile']} ({candidate['digest']})",
            })
            headless.artifact_stat(projects_root, project.name, "profiles", directory=True)
            projects._unique_child(profiles_root, key)
            staged.rename(target)
        return target
    except (OSError, KeyError, TypeError, ValueError, projects.ProjectError) as error:
        raise headless.MatchError(f"Cannot promote sweep variant: {error}") from error
