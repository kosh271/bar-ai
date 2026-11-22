"""Query installed engine content in a separate process."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Callable


def scan(engine: Path, progress: Callable[[int, int], None]) -> dict:
    dll_dir = os.add_dll_directory(str(engine))
    lib = ctypes.CDLL(str(engine / "unitsync.dll"))
    char = ctypes.c_char_p
    integer = ctypes.c_int
    signatures = {
        "Init": ([ctypes.c_bool, integer], integer),
        "UnInit": ([], None),
        "GetNextError": ([], char),
        "GetMapCount": ([], integer),
        "GetMapName": ([integer], char),
        "GetMapInfoCount": ([integer], integer),
        "GetPrimaryModCount": ([], integer),
        "GetPrimaryModInfoCount": ([integer], integer),
        "GetSkirmishAICount": ([], integer),
        "GetSkirmishAIInfoCount": ([integer], integer),
        "GetInfoKey": ([integer], char),
        "GetInfoType": ([integer], char),
        "GetInfoValueString": ([integer], char),
        "GetInfoValueInteger": ([integer], integer),
        "GetSkirmishAIOptionCount": ([integer], integer),
        "GetOptionKey": ([integer], char),
        "GetOptionListCount": ([integer], integer),
        "GetOptionListDef": ([integer], char),
        "GetOptionListItemKey": ([integer, integer], char),
        "GetOptionListItemName": ([integer, integer], char),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result

    def text(value: bytes | None) -> str:
        return value.decode("utf-8", "replace") if value else ""

    def count(value: int, what: str) -> int:
        if value < 0:
            raise RuntimeError(f"Cannot read {what}: {text(lib.GetNextError())}")
        return value

    def info(size: int) -> dict[str, str]:
        return {
            text(lib.GetInfoKey(i)): text(lib.GetInfoValueString(i))
            for i in range(size) if text(lib.GetInfoType(i)) == "string"
        }

    if not lib.Init(False, 0):
        raise RuntimeError(f"Cannot initialize unitsync: {text(lib.GetNextError())}")
    try:
        maps: dict[str, dict] = {}
        warnings = []
        total = count(lib.GetMapCount(), "maps")
        for i in range(total):
            progress(i, total)
            name = text(lib.GetMapName(i))
            if not name or name in maps:
                continue
            width = height = 0
            size = lib.GetMapInfoCount(i)
            if size < 0:
                warnings.append(f"Cannot read map size for {name}: {text(lib.GetNextError())}")
            else:
                for j in range(size):
                    key = text(lib.GetInfoKey(j))
                    if key not in ("width", "height") or text(lib.GetInfoType(j)) != "integer":
                        continue
                    if key == "width":
                        width = max(0, lib.GetInfoValueInteger(j))
                    else:
                        height = max(0, lib.GetInfoValueInteger(j))
            maps[name] = {"name": name, "width": width, "height": height}
        progress(total, total)
        games = []
        for i in range(count(lib.GetPrimaryModCount(), "games")):
            data = info(count(lib.GetPrimaryModInfoCount(i), "game information"))
            if data.get("name", "").startswith("Beyond All Reason"):
                games.append(data["name"])
        ais = []
        for i in range(count(lib.GetSkirmishAICount(), "AIs")):
            data = info(count(lib.GetSkirmishAIInfoCount(i), "AI information"))
            if not data.get("shortName") or not data.get("version"):
                continue
            profiles = []
            options = count(lib.GetSkirmishAIOptionCount(i), f"options for {data['shortName']}")
            for option in range(options):
                if text(lib.GetOptionKey(option)).casefold() == "profile":
                    size = count(lib.GetOptionListCount(option), f"profiles for {data['shortName']}")
                    profiles = []
                    for j in range(size):
                        key = text(lib.GetOptionListItemKey(option, j))
                        profiles.append({"key": key, "name": text(lib.GetOptionListItemName(option, j)) or key})
                    default = text(lib.GetOptionListDef(option))
                    if default and not any(p["key"] == default for p in profiles):
                        profiles.insert(0, {"key": default, "name": f"{default} (default)"})
            ais.append({"short_name": data["shortName"], "version": data["version"],
                        "name": data.get("name") or data["shortName"], "profiles": profiles})
        return {"maps": [maps[name] for name in sorted(maps)], "games": sorted(set(games)),
                "ais": ais, "warnings": warnings}
    finally:
        lib.UnInit()
        dll_dir.close()


def main() -> None:
    engine, data, write, output = map(Path, sys.argv[1:])
    os.environ["SPRING_WRITEDIR"] = str(write)
    os.environ["SPRING_DATADIR"] = str(data)
    progress_path = write / "scan-progress.txt"

    def progress(checked: int, total: int) -> None:
        progress_path.write_text(f"{checked} {total}\n", encoding="ascii")

    try:
        result = scan(engine, progress)
        output.write_text(json.dumps(result), encoding="utf-8")
    except (OSError, RuntimeError, AttributeError) as error:
        print(f"Content scan failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
