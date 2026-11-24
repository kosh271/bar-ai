"""Revision-checked byte writes for profile editors."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .projects import MAX_WORKSPACE_FILE_BYTES, ProjectError, _is_link


_NEWLINE = re.compile(r"\r\n|\r|\n")


def normalized_newlines(text: str) -> str:
    return _NEWLINE.sub("\n", text)


def restore_newlines(original: str, edited: str) -> str:
    """Keep the endings of existing lines when an editor displays LF only."""
    edited = normalized_newlines(edited)
    endings = _NEWLINE.findall(original)
    if not endings:
        return edited
    default = max(dict.fromkeys(endings), key=endings.count)
    old_lines = _NEWLINE.split(original)
    new_lines = edited.split("\n")
    if old_lines == new_lines:
        return original
    if len(set(endings)) == 1:
        return edited.replace("\n", default)

    new_endings = [default] * (len(new_lines) - 1)
    # Preserve repeated lines in ordinary files without asking SequenceMatcher
    # to compare every pair of repeated lines in a large file.
    matcher = SequenceMatcher(None, old_lines, new_lines,
                              autojunk=len(old_lines) * len(new_lines) > 4_000_000)
    for tag, old_start, old_stop, new_start, new_stop in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            count = min(old_stop - old_start, new_stop - new_start,
                        len(endings) - old_start, len(new_endings) - new_start)
            if count > 0:
                new_endings[new_start:new_start + count] = endings[old_start:old_start + count]
    return "".join(line + ending for line, ending in zip(new_lines, [*new_endings, ""]))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path) -> bytes:
    if path.stat().st_size <= MAX_WORKSPACE_FILE_BYTES:
        with path.open("rb") as source:
            contents = source.read(MAX_WORKSPACE_FILE_BYTES + 1)
        if len(contents) <= MAX_WORKSPACE_FILE_BYTES:
            return contents
    raise ProjectError(f"Profile file exceeds its size limit: {path.name}")


def _check_target(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    if ".." in relative.parts or _is_link(root) or not path.is_file() or any(
        _is_link(root.joinpath(*relative.parts[:count])) for count in range(1, len(relative.parts) + 1)
    ):
        raise ProjectError("Profile files cannot be links or missing.")


def _stage(path: Path, data: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.editor-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(mode))
    except OSError:
        _discard_stage(temporary)
        raise
    return temporary


def _discard_stage(path: Path) -> None:
    # On Windows the source mode may make a staged file read-only.
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        if os.name != "nt":
            raise
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        path.unlink()


@dataclass(frozen=True)
class _Edit:
    path: Path
    staged: Path
    original: bytes
    before: str
    after: str
    mode: int


def replace(root: Path, entries: list[tuple[Path, str, bytes]]) -> None:
    """Check and stage every edit before replacing any of the input files."""
    staged: list[_Edit] = []
    replaced: list[_Edit] = []
    try:
        for path, expected, content in entries:
            _check_target(root, path)
            if len(content) > MAX_WORKSPACE_FILE_BYTES:
                raise ProjectError(f"Profile file exceeds its size limit: {path.name}")
            original = read(path)
            if digest(original) != expected:
                raise ProjectError(f"File changed outside the editor: {path.relative_to(root).as_posix()}")
            if original == content:
                continue
            mode = path.stat().st_mode
            staged.append(_Edit(path, _stage(path, content, mode), original, expected, digest(content), mode))
        for edit in staged:
            _check_target(root, edit.path)
            if digest(read(edit.path)) != edit.before:
                raise ProjectError(f"File changed outside the editor: {edit.path.relative_to(root).as_posix()}")
            os.replace(edit.staged, edit.path)
            replaced.append(edit)
    except (OSError, ProjectError) as error:
        failures = []
        for edit in reversed(replaced):
            try:
                _check_target(root, edit.path)
                if digest(read(edit.path)) != edit.after:
                    raise ProjectError(f"File changed during rollback: {edit.path.relative_to(root).as_posix()}")
                backup = _stage(edit.path, edit.original, edit.mode)
                try:
                    os.replace(backup, edit.path)
                finally:
                    _discard_stage(backup)
            except (OSError, ProjectError) as failure:
                failures.append(str(failure))
        if failures:
            raise OSError(f"Save failed ({error}). Rollback failed: {'; '.join(failures)}") from error
        raise
    finally:
        for edit in staged:
            _discard_stage(edit.staged)
