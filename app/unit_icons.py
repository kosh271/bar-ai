"""Read unit icons from the selected local BAR installation."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TypedDict

from .settings import Settings

try:
    from PIL import Image
except ImportError:
    Image = None

MAX_ICON_BYTES = 16 * 1024 * 1024
MAX_ICON_PIXELS = 4096 * 4096


class IconStatus(TypedDict):
    available: bool
    message: str


def icon_directory(settings: Settings | None) -> Path | None:
    if settings is None:
        return None
    root = Path(settings.bar_root).expanduser().resolve()
    directory = root / "data" / "games" / "BAR.sdd" / "unitpics"
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        is_junction = getattr(current, "is_junction", None)
        if current.is_symlink() or (is_junction is not None and is_junction()):
            raise ValueError(f"Unit icon path contains a link: {current}")
    return directory if directory.is_dir() else None


def icon_status(settings: Settings | None) -> IconStatus:
    if settings is None:
        return {"available": False, "message": "Select a BAR installation to show unit icons."}
    if Image is None:
        return {"available": False, "message": "Install Pillow to show unit icons: python -m pip install -r requirements.txt"}
    if icon_directory(settings) is None:
        return {"available": False, "message": "Unit icons were not found in the selected installation's data/games/BAR.sdd/unitpics folder."}
    return {"available": True, "message": ""}


def read_icon(settings: Settings | None, unit_id: str) -> bytes | None:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", unit_id):
        raise ValueError("Invalid unit ID.")
    if Image is None:
        raise ValueError("Pillow is required to read unit icons.")
    directory = icon_directory(settings)
    if directory is None:
        return None
    path = directory / (unit_id.lower() + ".dds")
    is_junction = getattr(path, "is_junction", None)
    if path.is_symlink() or (is_junction is not None and is_junction()):
        raise ValueError("Unit icon is a link.")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_ICON_BYTES + 1)
    except FileNotFoundError:
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
