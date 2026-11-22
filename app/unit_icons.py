"""Read unit icons from the selected local BAR installation."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import stat
import zlib
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import TypedDict

from . import game_sources
from .settings import Settings

try:
    from PIL import Image
except ImportError:
    Image = None

MAX_ICON_BYTES = 16 * 1024 * 1024
MAX_ICON_PIXELS = 4096 * 4096
UNIT_ID = re.compile(r"[A-Za-z0-9_]{1,128}")
PACKAGE_ICON = re.compile(r"unitpics/([A-Za-z0-9_]{1,128})\.dds", re.IGNORECASE | re.ASCII)
_cache_lock = RLock()


class IconStatus(TypedDict):
    available: bool
    message: str


def _check_links(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            info = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Unit icon path contains a link: {current}")


def _icon_directory(root: Path) -> Path | None:
    directory = root / "data" / "games" / "BAR.sdd" / "unitpics"
    _check_links(root, directory)
    return directory if directory.is_dir() else None


def icon_directory(settings: Settings | None) -> Path | None:
    return _icon_directory(Path(settings.bar_root).expanduser().resolve()) if settings is not None else None


@lru_cache(maxsize=8)
def _select_package(
    data: Path, catalog_game: str, indices: tuple[tuple[Path, int], ...], packages_modified: int,
) -> str | None:
    # The index and directory timestamps refresh selection after a launcher update.
    sources = game_sources.list_package_sources(data, tuple(path for path, _ in indices))
    if not sources:
        return None
    matching = [source for source in sources if source.game_name.casefold() == catalog_game.casefold()]
    return max(
        matching or sources,
        key=lambda source: ((data / "packages" / f"{source.digest}.sdp").lstat().st_mtime_ns, source.digest),
    ).digest


@lru_cache(maxsize=8)
def _manifest_icons(root: Path, digest: str) -> dict[str, tuple[str, int]]:
    package = root / "data" / "packages" / f"{digest}.sdp"
    icons = {}
    for name, checksum, size in game_sources._package_entries(package):
        match = PACKAGE_ICON.fullmatch(name)
        if match is None:
            continue
        unit_id = match[1].lower()
        if unit_id in icons:
            raise ValueError(f"Duplicate unit icon in game package: {name}")
        icons[unit_id] = (checksum, size)
    return icons


def clear_icon_cache() -> None:
    with _cache_lock:
        _select_package.cache_clear()
        _manifest_icons.cache_clear()


def _packaged_icons(root: Path, catalog_game: str) -> dict[str, tuple[str, int]]:
    data = root / "data"
    rapid, packages = data / "rapid", data / "packages"
    _check_links(root, rapid)
    _check_links(root, packages)
    try:
        modified = packages.stat().st_mtime_ns
    except (FileNotFoundError, NotADirectoryError):
        return {}
    indices = []
    for path in sorted(rapid.glob("*/byar/versions.gz")):
        _check_links(root, path)
        indices.append((path, path.stat().st_mtime_ns))
    try:
        with _cache_lock:
            digest = _select_package(data, catalog_game, tuple(indices), modified)
            if digest is None:
                return {}
            _check_links(root, packages / f"{digest}.sdp")
            return _manifest_icons(root, digest)
    except (game_sources.GameSourceError, zlib.error) as error:
        raise ValueError(f"Cannot read packaged unit icons: {error}") from error


def icon_status(settings: Settings | None, *, catalog_game: str = "") -> IconStatus:
    if settings is None:
        return {"available": False, "message": "Select a BAR installation to show unit icons."}
    if Image is None:
        return {"available": False, "message": "Install Pillow to show unit icons: python -m pip install -r requirements.txt"}
    root = Path(settings.bar_root).expanduser().resolve()
    if _icon_directory(root) is None and not _packaged_icons(root, catalog_game):
        return {"available": False, "message": "Unit icons were not found in the selected installation's unpacked BAR.sdd or installed Rapid game package."}
    return {"available": True, "message": ""}


def _read_pooled_icon(root: Path, catalog_game: str, unit_id: str) -> bytes | None:
    entry = _packaged_icons(root, catalog_game).get(unit_id)
    if entry is None:
        return None
    digest, size = entry
    if size > MAX_ICON_BYTES:
        raise ValueError(f"Unit icon is too large: {unit_id}")
    pool = root / "data" / "pool" / digest[:2] / f"{digest[2:]}.gz"
    _check_links(root, pool)
    try:
        with gzip.open(pool, "rb") as source:
            body = source.read(size + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError(f"Cannot read packaged unit icon {unit_id}: {error}") from error
    if len(body) != size or hashlib.md5(body, usedforsecurity=False).hexdigest() != digest:
        raise ValueError(f"Packaged unit icon failed integrity check: {unit_id}")
    return body


def _read_unpacked_icon(root: Path, unit_id: str) -> bytes | None:
    directory = _icon_directory(root)
    if directory is None:
        return None
    path = directory / f"{unit_id}.dds"
    _check_links(root, path)
    try:
        with path.open("rb") as source:
            return source.read(MAX_ICON_BYTES + 1)
    except FileNotFoundError:
        return None


def read_icon(settings: Settings | None, unit_id: str, *, catalog_game: str = "") -> bytes | None:
    if not UNIT_ID.fullmatch(unit_id):
        raise ValueError("Invalid unit ID.")
    if Image is None:
        raise ValueError("Pillow is required to read unit icons.")
    if settings is None:
        return None
    root = Path(settings.bar_root).expanduser().resolve()
    key = unit_id.lower()
    data = _read_unpacked_icon(root, key)
    if data is None:
        data = _read_pooled_icon(root, catalog_game, key)
    if data is None:
        return None
    if len(data) > MAX_ICON_BYTES:
        raise ValueError(f"Unit icon is too large: {unit_id}")
    try:
        with Image.open(io.BytesIO(data), formats=["DDS"]) as image:
            if image.width * image.height > MAX_ICON_PIXELS:
                raise ValueError(f"Unit icon dimensions are too large: {unit_id}")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"Cannot decode unit icon {unit_id}: {error}") from error
