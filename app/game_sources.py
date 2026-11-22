"""Discover installed game packages and extract BARbarIAn files read-only."""

from __future__ import annotations

import gzip
import hashlib
import re
import struct
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


class GameSourceError(Exception):
    """An installed game source cannot be inspected or copied."""


@dataclass(frozen=True)
class GameSource:
    label: str
    game_name: str
    data: Path
    digest: str = ""
    unpacked: Path | None = None
    tag: str = ""


def _last_game(data: Path) -> str | None:
    log = data / "infolog.txt"
    if not log.is_file():
        return None
    match_game = re.compile(r'\[PreGame::AddModArchivesToVFS\].*using game "([^"]+)"')
    latest = None
    try:
        with log.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                match = match_game.search(line)
                if match:
                    latest = match.group(1)
    except OSError as error:
        raise GameSourceError(f"Cannot read game log {log}: {error}") from error
    return latest


def list_game_sources(data: Path, unpacked: Path | None) -> tuple[list[GameSource], GameSource | None]:
    sources: list[GameSource] = []
    seen: set[tuple[str, str]] = set()
    indices = sorted((data / "rapid").glob("*/byar/versions.gz"))
    try:
        for index in indices:
            with gzip.open(index, "rt", encoding="utf-8") as file:
                for line in file:
                    if not line.startswith("byar:"):
                        continue
                    parts = line.rstrip("\r\n").split(",", 3)
                    if len(parts) != 4 or not re.fullmatch(r"[0-9a-fA-F]{32}", parts[1]):
                        raise GameSourceError(f"Invalid BAR package record in {index}")
                    tag, digest, _, game_name = parts
                    digest = digest.lower()
                    if not (data / "packages" / f"{digest}.sdp").is_file() or (tag, digest) in seen:
                        continue
                    seen.add((tag, digest))
                    sources.append(GameSource(
                        f"{game_name} ({tag}, {digest[:8]})", game_name, data, digest, tag=tag
                    ))
    except (OSError, EOFError, UnicodeError) as error:
        raise GameSourceError(f"Cannot read installed game package index: {error}") from error

    sources.sort(key=lambda source: (
        0 if source.tag == "byar:test" else
        1 if source.tag == "byar:stable" else 2,
        source.label.casefold(),
    ))
    if unpacked is not None:
        sources.append(GameSource("Unpacked BAR.sdd (version may differ)", "", data, unpacked=unpacked))

    last_game = _last_game(data)
    matches = [source for source in sources if source.digest and source.game_name == last_game]
    suggested = matches[0] if matches and len({source.digest for source in matches}) == 1 else None
    return sources, suggested


def _package_entries(path: Path) -> Iterator[tuple[str, str, int]]:
    try:
        with gzip.open(path, "rb") as source:
            remainder = b""
            while chunk := source.read(64 * 1024):
                block = remainder + chunk
                position = 0
                while position < len(block):
                    length = block[position]
                    if not length:
                        raise GameSourceError(f"Invalid game package manifest: {path}")
                    name_start = position + 1
                    metadata = name_start + length
                    end = metadata + 24
                    if end > len(block):
                        break
                    try:
                        name = block[name_start:metadata].decode("utf-8")
                    except UnicodeError as error:
                        raise GameSourceError(f"Invalid filename in game package {path}: {error}") from error
                    digest = block[metadata:metadata + 16].hex()
                    size = struct.unpack_from(">I", block, metadata + 20)[0]
                    yield name, digest, size
                    position = end
                remainder = block[position:]
            if remainder:
                raise GameSourceError(f"Invalid game package manifest: {path}")
    except (OSError, EOFError) as error:
        raise GameSourceError(f"Cannot read game package {path}: {error}") from error


@contextmanager
def stock_directory(source: GameSource, temporary_parent: Path) -> Iterator[Path]:
    if source.unpacked is not None:
        yield source.unpacked / "luarules" / "configs" / "BARb" / "stable"
        return
    if not re.fullmatch(r"[0-9a-f]{32}", source.digest):
        raise GameSourceError("Invalid game package identifier.")
    temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stock-", dir=temporary_parent) as temporary:
        destination = Path(temporary)
        seen: set[str] = set()
        package = source.data / "packages" / f"{source.digest}.sdp"
        for name, digest, size in _package_entries(package):
            parts = PurePosixPath(name).parts
            if tuple(part.casefold() for part in parts[:4]) != ("luarules", "configs", "barb", "stable"):
                continue
            relative = parts[4:]
            if len(relative) < 2 or relative[0].casefold() not in ("config", "script"):
                continue
            if not name.lower().endswith((".json", ".as")):
                continue
            if any(part in ("", ".", "..") or "\\" in part or ":" in part for part in relative):
                raise GameSourceError(f"Unsafe filename in game package: {name}")
            target = destination.joinpath(*relative)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise GameSourceError(f"Unsafe filename in game package: {name}")
            case_key = str(target.relative_to(destination)).casefold()
            if case_key in seen:
                raise GameSourceError(f"Duplicate filename in game package: {name}")
            seen.add(case_key)
            pool = source.data / "pool" / digest[:2] / f"{digest[2:]}.gz"
            try:
                with gzip.open(pool, "rb") as packed:
                    body = packed.read()
            except (OSError, EOFError) as error:
                raise GameSourceError(f"Cannot read game package file {name}: {error}") from error
            if len(body) != size or hashlib.md5(body, usedforsecurity=False).hexdigest() != digest:
                raise GameSourceError(f"Game package file failed integrity check: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        if not seen:
            raise GameSourceError(f"BARbarIAn files not found in game package {package}")
        yield destination
