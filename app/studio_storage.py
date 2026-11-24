"""Report and explicitly remove Studio's disposable per-run artifacts."""

from __future__ import annotations

import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from .headless import MatchError
from .installation import _check_links, _link


_PIN = ".studio-pinned"
_OPERATION_LOCK = ".studio-operation.lock"
_ARTIFACT_LOCK = ".studio-artifacts.lock"


def operation_lease(root: Path, storage: Path):
    """Exclude other Studio processes from launching work or cleaning runs."""
    return _file_lease(root, storage, _OPERATION_LOCK, "Another Studio process is using these match artifacts.")


def artifact_mutation_lease(root: Path, storage: Path):
    """Exclude pins and cleanup from changing the same run concurrently."""
    return _file_lease(root, storage, _ARTIFACT_LOCK, "Another Studio process is changing these match artifacts.")


@contextmanager
def _file_lease(root: Path, storage: Path, filename: str, busy: str):
    path = storage / filename
    _check_links(root, path)
    storage.mkdir(parents=True, exist_ok=True)
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as lock:
        _check_links(root, path)
        opened = os.fstat(lock.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not os.path.samestat(opened, path.lstat()):
            raise MatchError("The Studio operation lock changed or is linked.")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise MatchError(busy) from error
        yield


def check_page(offset: int, limit: int, *, maximum: int = 100) -> None:
    if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
        raise MatchError("Offset must be a nonnegative integer up to 2147483647.")
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise MatchError(f"Limit must be an integer between 1 and {maximum}.")


def run_directory(folder: Path, run_id: str) -> Path:
    run = folder / run_id
    _check_links(folder, run)
    if not run.is_dir() or run.resolve().parent != folder.resolve():
        raise MatchError(f"Run {run_id} is unavailable.")
    return run


def inspect_run(run: Path, *, active: bool = False) -> dict:
    size = 0
    has_replay = False
    unreadable = False

    def on_error(_error: OSError) -> None:
        nonlocal unreadable
        unreadable = True

    for parent, folders, files in os.walk(run, onerror=on_error):
        directory = Path(parent)
        for name in folders[:]:
            if _link(directory / name):
                folders.remove(name)
                unreadable = True
        for name in files:
            path = directory / name
            has_replay |= path.suffix.casefold() == ".sdfz"
            if _link(path):
                unreadable = True
                continue
            try:
                size += path.stat().st_size
            except OSError:
                unreadable = True
    pinned = (run / _PIN).exists() or _link(run / _PIN)
    return {"id": run.name, "bytes": size, "pinned": pinned, "has_replay": has_replay, "active": active,
            "protected": pinned or has_replay or active or unreadable, "unreadable": unreadable}


def set_pin(run: Path, pinned: bool) -> None:
    marker = run / _PIN
    _check_links(run, marker)
    if pinned:
        try:
            with marker.open("xb"):
                pass
        except FileExistsError:
            if not marker.is_file():
                raise MatchError("The run pin marker is unavailable.")
    else:
        marker.unlink(missing_ok=True)


def cleanup(runs: list[Path]) -> dict:
    for run in runs:
        if inspect_run(run)["protected"]:
            raise MatchError(f"Run {run.name} is protected from cleanup.")
    removed, failed = [], []
    for run in runs:
        try:
            shutil.rmtree(run)
        except OSError as error:
            failed.append({"id": run.name, "error": str(error)})
        else:
            removed.append(run.name)
    return {"removed": removed, "failed": failed}
