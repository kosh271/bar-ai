"""Bounded ZIP exchange for self-contained Studio projects."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO

from . import project_history, projects

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def write(project: Path, output: BinaryIO) -> None:
    def walk_error(error: OSError) -> None:
        raise error

    if projects._is_link(project):
        raise projects.ProjectError("Project cannot be a link.")
    count = total = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, directories, filenames in os.walk(project, onerror=walk_error):
            parent = Path(root)
            directories[:] = sorted(name for name in directories if not projects.is_private_path_name(name)
                                    and not projects._is_link(parent / name))
            for name in sorted(filenames):
                path = parent / name
                if projects.is_private_path_name(name) or projects._is_link(path):
                    continue
                relative = path.relative_to(project).as_posix()
                projects.workspace_path_parts(relative)
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise projects.ProjectError(f"Project contains a non-regular file: {relative}")
                total += info.st_size
                count += 1
                projects.check_workspace_size(info.st_size, total, count)
                archive.write(path, relative)
    if output.tell() > MAX_ARCHIVE_BYTES:
        raise projects.ProjectError("Compressed project archive exceeds its size limit.")


def _extract(archive: zipfile.ZipFile, staged: Path) -> None:
    entries = archive.infolist()
    projects.check_workspace_size(0, 0, len(entries))
    paths: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    seen: set[tuple[str, ...]] = set()
    total = 0
    for entry in entries:
        parts = projects.workspace_path_parts(entry.orig_filename)
        if entry.orig_filename != entry.filename:
            raise projects.ProjectError("Invalid ZIP filename.")
        if parts in seen:
            raise projects.ProjectError(f"Duplicate ZIP path: {entry.filename!r}")
        seen.add(parts)
        directory = entry.is_dir()
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                or entry.external_attr & 0x400 or entry.flag_bits & 1
                or (directory and entry.file_size)):
            raise projects.ProjectError(f"Unsupported ZIP entry: {entry.filename!r}")
        total += entry.file_size
        projects.check_workspace_size(entry.file_size, total, len(entries))
        for length in range(1, len(parts) + 1):
            prefix = parts[:length]
            key = tuple(part.casefold() for part in prefix)
            value = (prefix, directory or length < len(parts))
            previous = paths.setdefault(key, value)
            if previous != value:
                raise projects.ProjectError(f"Conflicting ZIP path: {entry.filename!r}")
        target = staged.joinpath(*parts)
        if directory:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            output = target.open("xb")
        except FileExistsError as error:
            raise projects.ProjectError(f"Duplicate ZIP path: {entry.filename!r}") from error
        with output, archive.open(entry) as source:
            shutil.copyfileobj(source, output, length=64 * 1024)
            if output.tell() != entry.file_size:
                raise projects.ProjectError(f"Incomplete ZIP entry: {entry.filename!r}")


def load(root: Path, source: BinaryIO) -> Path:
    if source.seek(0, os.SEEK_END) > MAX_ARCHIVE_BYTES:
        raise projects.ProjectError("Compressed project archive exceeds its size limit.")
    if projects._is_link(root):
        raise projects.ProjectError("Projects directory cannot be a link.")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary:
        staged = Path(temporary)
        try:
            with zipfile.ZipFile(source) as archive:
                _extract(archive, staged)
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as error:
            raise projects.ProjectError(f"Invalid project ZIP: {error}") from error
        metadata = projects.project_metadata(staged)
        key = metadata["short_name"]
        if projects._key(key, project=True) != key:
            raise projects.ProjectError("Invalid AI identifier in project ZIP.")
        profiles = dict(projects.list_profiles(staged))
        if metadata["default_profile"] not in profiles:
            raise projects.ProjectError("The project ZIP is missing its default profile.")
        for profile_key in profiles:
            profile = projects._profile_path(staged, profile_key)
            files = {path.as_posix(): path for path in projects.profile_files(profile)}
            if not {"script/init.as", "script/main.as"}.issubset(files) or not any(
                path.startswith("config/") for path in files
            ):
                raise projects.ProjectError(f"Incomplete profile in project ZIP: {profile_key}")
            for relative in files.values():
                try:
                    (profile / relative).read_text(encoding="utf-8")
                except UnicodeError as error:
                    raise projects.ProjectError(f"Profile file must be UTF-8: {relative}") from error
        target = projects._unique_child(root, key)
        project_history.ensure(staged)
        staged.rename(target)
    return target
