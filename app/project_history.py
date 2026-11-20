"""Local, project-scoped Git checkpoints for the workshop."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class HistoryError(Exception):
    """Project history is unavailable or an operation was refused."""


_MARKER = "bar-ai-studio-history"
_ATTRIBUTES = "* -text -filter -ident -working-tree-encoding\n"


def _git(project: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    command = [
        "git", "--no-pager", "-C", str(project),
        "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
        "-c", f"core.hooksPath={project / '.git' / 'bar-ai-no-hooks'}",
        "-c", "user.name=BAR AI Studio", "-c", "user.email=bar-ai-studio@localhost",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    try:
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise HistoryError(f"Cannot run Git for {project.name}: {error}") from error
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise HistoryError(f"Git {' '.join(args[:2])} failed: {detail or result.returncode}")
    return result


def _check_tree(project: Path) -> None:
    def walk_error(error: OSError) -> None:
        raise HistoryError(f"Cannot inspect project files: {error}") from error

    for root, dirs, files in os.walk(project, followlinks=False, onerror=walk_error):
        parent = Path(root)
        if parent == project and ".git" in dirs:
            dirs.remove(".git")
        for name in dirs + files:
            path = parent / name
            junction = getattr(path, "is_junction", None)
            if path.is_symlink() or (junction is not None and junction()):
                raise HistoryError(f"Project history does not support links: {path}")
            if name.casefold() == ".git":
                raise HistoryError(f"Nested Git metadata is not supported: {path}")


def ensure(project: Path) -> None:
    """Initialize a baseline without adopting an existing unmanaged repository."""
    metadata = project / ".git"
    if metadata.exists() or metadata.is_symlink():
        junction = getattr(metadata, "is_junction", None)
        if (metadata.is_symlink() or (junction is not None and junction()) or
                not metadata.is_dir() or not (metadata / _MARKER).is_file()):
            raise HistoryError("This project already has a Git repository not managed by BAR AI Studio; it was not changed.")
        if (metadata / "info" / "attributes").read_text(encoding="utf-8") != _ATTRIBUTES:
            raise HistoryError("Project history's Git attributes were changed; inspect the repository before continuing.")
        if not _head(project):
            checkpoint(project, "Initial project baseline")
        return
    _check_tree(project)
    _git(project, "init", "--quiet")
    with (metadata / "info" / "attributes").open("x", encoding="utf-8", newline="\n") as attributes:
        attributes.write(_ATTRIBUTES)
    (metadata / _MARKER).write_text("BAR AI Studio project history\n", encoding="utf-8")
    checkpoint(project, "Initial project baseline")


def _head(project: Path) -> str:
    result = _git(project, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1))
    return result.stdout.decode("ascii").strip() if result.returncode == 0 else ""


def _check_index(project: Path) -> None:
    entries = _git(project, "ls-files", "-v", "-z").stdout.split(b"\0")
    if any(entry and (entry[:1].islower() or entry.startswith(b"S ")) for entry in entries):
        raise HistoryError("Git has assume-unchanged or skip-worktree files; clear those flags outside Studio first.")


def checkpoint(project: Path, description: str) -> str:
    if not description.strip() or "\n" in description or "\r" in description or "\x00" in description:
        raise HistoryError("Enter a checkpoint description on one line.")
    _check_tree(project)
    head = _head(project)
    _check_index(project)
    staged = _git(project, "diff", "--cached", "--quiet", allowed=(0, 1))
    if staged.returncode:
        raise HistoryError("Git already has staged changes. Commit or unstage them outside Studio first.")
    try:
        _git(project, "add", "--all", "--force", "--", ".")
        changed = _git(project, "diff", "--cached", "--quiet", allowed=(0, 1))
        if not changed.returncode:
            raise HistoryError("There are no saved project changes to checkpoint.")
        _git(project, "commit", "--quiet", "-m", description.strip())
    except HistoryError as error:
        raise HistoryError(f"{error} If Git staged files before the failure, review them outside Studio.") from error
    result = _head(project)
    if not result or result == head:
        raise HistoryError("Git did not create a new checkpoint; inspect the project repository.")
    return result


def list_checkpoints(project: Path) -> list[dict[str, str]]:
    head = _head(project)
    if not head:
        raise HistoryError("Project history has no baseline. Inspect its Git repository.")
    output = _git(project, "log", "-z", "--format=%H%x00%cI%x00%s", "HEAD").stdout
    parts = output.rstrip(b"\0").split(b"\0")
    if len(parts) % 3:
        raise HistoryError("Git returned an invalid checkpoint list.")
    return [
        {"id": parts[i].decode("ascii"), "date": parts[i + 1].decode("utf-8", "replace"),
         "description": parts[i + 2].decode("utf-8", "replace")}
        for i in range(0, len(parts), 3)
    ]


def restore(project: Path, commit: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise HistoryError("Invalid checkpoint ID.")
    _check_tree(project)
    _check_index(project)
    if _git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "-z").stdout:
        raise HistoryError("The project has uncheckpointed or ignored files. Checkpoint or remove them before restoring.")
    if _git(project, "merge-base", "--is-ancestor", commit, "HEAD", allowed=(0, 1)).returncode:
        raise HistoryError("The checkpoint is not in this project's current history.")
    entries = _git(project, "ls-tree", "-r", "-z", commit).stdout.split(b"\0")
    if any(entry and not entry.startswith((b"100644 blob ", b"100755 blob ")) for entry in entries):
        raise HistoryError("The checkpoint contains links or other unsupported Git entries.")
    try:
        _git(project, "restore", "--source", commit, "--worktree", "--", ".")
    except HistoryError as error:
        raise HistoryError(f"Restore may have partially changed project files; inspect them before continuing. {error}") from error
