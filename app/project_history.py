"""Local, project-scoped Git checkpoints for the workshop."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from .projects import _is_link


class HistoryError(Exception):
    """Project history is unavailable or an operation was refused."""


_MARKER = "bar-ai-studio-history"
_ATTRIBUTES = "* -text -filter -ident -working-tree-encoding\n"
MAX_DIFF_BYTES = 256 * 1024


def _git(project: Path, *args: str, allowed: tuple[int, ...] = (0,),
         limit: int | None = None) -> subprocess.CompletedProcess[bytes]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0")
    command = [
        "git", "--no-pager", "-C", str(project),
        "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
        "-c", "diff.autoRefreshIndex=false",
        "-c", f"core.hooksPath={project / '.git' / 'bar-ai-no-hooks'}",
        "-c", "user.name=BAR AI Studio", "-c", "user.email=bar-ai-studio@localhost",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    try:
        if limit is None:
            result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        else:
            result = _read_bounded(command, env, limit)
    except (OSError, subprocess.SubprocessError) as error:
        raise HistoryError(f"Cannot run Git for {project.name}: {error}") from error
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise HistoryError(f"Git {' '.join(args[:2])} failed: {detail or result.returncode}")
    return result


def _read_bounded(command: list[str], env: dict[str, str], limit: int) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=errors,
    ) as process:
        assert process.stdout is not None
        chunks: list[bytes] = []

        def read() -> None:
            chunks.append(process.stdout.read(limit + 1))
            if len(chunks[0]) > limit and process.poll() is None:
                process.kill()

        reader = threading.Thread(target=read)
        reader.start()
        try:
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
            reader.join()
        if chunks and len(chunks[0]) > limit:
            raise HistoryError("The checkpoint diff exceeds its output size limit.")
        errors.seek(0)
        return subprocess.CompletedProcess(command, process.returncode, b"".join(chunks), errors.read(4096))


def _check_tree(project: Path) -> None:
    def walk_error(error: OSError) -> None:
        raise HistoryError(f"Cannot inspect project files: {error}") from error

    for root, dirs, files in os.walk(project, followlinks=False, onerror=walk_error):
        parent = Path(root)
        if parent == project and ".git" in dirs:
            dirs.remove(".git")
        for name in dirs + files:
            path = parent / name
            if _is_link(path):
                raise HistoryError(f"Project history does not support links: {path}")
            if name.casefold() == ".git":
                raise HistoryError(f"Nested Git metadata is not supported: {path}")


def _check_managed(project: Path) -> None:
    metadata = project / ".git"
    entries = (_MARKER, "HEAD", "config", "index", "refs", "objects", "info", "info/attributes")
    if _is_link(project) or _is_link(metadata):
        raise HistoryError("Project history cannot contain linked metadata.")
    if not metadata.is_dir():
        raise HistoryError("Project history is not initialized or not managed by BAR AI Studio.")
    if any(_is_link(metadata / name) for name in entries):
        raise HistoryError("Project history cannot contain linked metadata.")
    if not (metadata / _MARKER).is_file() or not (metadata / "info" / "attributes").is_file():
        raise HistoryError("Project history is not initialized or not managed by BAR AI Studio.")
    if (metadata / "info" / "attributes").read_bytes() != _ATTRIBUTES.encode():
        raise HistoryError("Project history's Git attributes were changed.")


def ensure(project: Path) -> None:
    """Initialize a baseline without adopting an existing unmanaged repository."""
    metadata = project / ".git"
    if metadata.exists() or metadata.is_symlink():
        _check_managed(project)
        if not _head(project):
            checkpoint(project, "Initial project baseline")
        return
    if _is_link(project):
        raise HistoryError("Project history cannot contain linked metadata.")
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


def status_summary(project: Path) -> dict[str, object]:
    """Return read-only Git checkpoint HEAD and dirty-tree status for a Studio project."""
    try:
        _check_managed(project)
        _check_tree(project)
        head = _head(project)
        if not head:
            return {
                "checkpoint": None,
                "checkpoint_description": None,
                "dirty": None,
                "error": "Project history has no baseline checkpoint.",
            }
        _check_index(project)
        subject = _git(project, "log", "-1", "--format=%s", "HEAD").stdout.decode("utf-8", "replace").strip()
        dirty = bool(_git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "-z").stdout)
        return {
            "checkpoint": head,
            "checkpoint_description": subject,
            "dirty": dirty,
            "error": None,
        }
    except (HistoryError, OSError) as error:
        return {
            "checkpoint": None,
            "checkpoint_description": None,
            "dirty": None,
            "error": str(error),
        }


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


def _validate_commit(project: Path, commit: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise HistoryError("Invalid checkpoint ID.")
    result = _git(project, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", allowed=(0, 1))
    if result.stdout.strip() != commit.encode("ascii") or _git(
        project, "merge-base", "--is-ancestor", commit, "HEAD", allowed=(0, 1)
    ).returncode:
        raise HistoryError("The checkpoint is not in this project's current history.")


def diff(project: Path, *, base: str = "", commit: str = "", names_only: bool = False) -> dict:
    """Inspect a parent diff, a pair of checkpoints, or saved files against the baseline."""
    _check_managed(project)
    _check_tree(project)
    if base:
        _validate_commit(project, base)
    if commit:
        _validate_commit(project, commit)
    else:
        _check_index(project)
    if commit and not base:
        parents = _git(project, "rev-list", "--parents", "-n", "1", commit, limit=4096
                       ).stdout.decode("ascii").split()[1:]
        if parents:
            command, revisions = ("diff",), (parents[0], commit)
        else:
            command, revisions = ("diff-tree", "--root", "--no-commit-id", "-r"), (commit,)
    else:
        command = ("diff",)
        if not base:
            base = _git(project, "rev-list", "--first-parent", "--max-parents=0", "HEAD", limit=256
                        ).stdout.decode("ascii").strip()
        revisions = (base, commit) if commit else (base,)
    flags = ("--no-ext-diff", "--no-textconv", "--no-renames", "--no-color")
    # --numstat checks content even when a file's timestamp differs from the index.
    stats = _git(project, *command, *flags, "--numstat", "-z", *revisions, "--",
                 limit=MAX_DIFF_BYTES).stdout
    names = [entry.split(b"\t", 2)[-1] for entry in stats.split(b"\0") if entry]
    untracked = b""
    if not commit:
        untracked = _git(project, "ls-files", "--others", "-z", limit=MAX_DIFF_BYTES - len(stats)).stdout
        names.extend(path for path in untracked.split(b"\0") if path)
    paths = list(dict.fromkeys(path.decode("utf-8", "replace") for path in names))
    patch = b""
    if not names_only:
        patch = _git(project, *command, *flags, "--patch", *revisions, "--", limit=MAX_DIFF_BYTES).stdout
        for path in untracked.split(b"\0"):
            if path:
                patch += _git(project, "diff", *flags, "--no-index", "--", os.devnull,
                              "./" + os.fsdecode(path), allowed=(0, 1),
                              limit=MAX_DIFF_BYTES - len(patch)).stdout
    return {"paths": paths, "patch": patch.decode("utf-8", "replace")}


def restore(project: Path, commit: str) -> None:
    _validate_commit(project, commit)
    _check_tree(project)
    _check_index(project)
    if _git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "-z").stdout:
        raise HistoryError("The project has uncheckpointed or ignored files. Checkpoint or remove them before restoring.")
    entries = _git(project, "ls-tree", "-r", "-z", commit).stdout.split(b"\0")
    if any(entry and not entry.startswith((b"100644 blob ", b"100755 blob ")) for entry in entries):
        raise HistoryError("The checkpoint contains links or other unsupported Git entries.")
    try:
        _git(project, "restore", "--source", commit, "--worktree", "--", ".")
    except HistoryError as error:
        raise HistoryError(f"Restore may have partially changed project files; inspect them before continuing. {error}") from error
