"""Read a game's unit definitions in an isolated unitsync process."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path


_RANDOM_SCRIPT = b"""
-- Unitsync's LuaParser exposes a dummy math.random. Use a repeatable sample,
-- not the game's runtime RNG, while evaluating randomized definitions.
local randomState = 1
math.randomseed = function(seed)
    if type(seed) ~= 'number' or seed ~= seed or seed == math.huge or seed == -math.huge then
        error('math.randomseed requires a finite number')
    end
    randomState = math.floor(seed) % 2147483647
    if randomState == 0 then randomState = 1 end
end
math.random = function(...)
    local count = select('#', ...)
    if count > 2 then error('math.random expects at most two arguments') end
    local lower, upper
    if count == 1 then lower, upper = 1, select(1, ...)
    elseif count == 2 then lower, upper = ...
    end
    if count > 0 then
        if type(lower) ~= 'number' or type(upper) ~= 'number'
            or lower ~= math.floor(lower) or upper ~= math.floor(upper)
            or lower < -2147483648 or upper > 2147483647 or lower > upper then
            error('math.random requires a non-empty integer interval')
        end
    end
    randomState = (randomState * 16807) % 2147483647
    local sample = (randomState - 1) / 2147483646
    if count == 0 then return sample end
    return lower + math.floor(sample * (upper - lower + 1))
end
"""

_UNIT_SCRIPT = _RANDOM_SCRIPT + b"""
VFS.MOD='M'; VFS.GAME='M'; VFS.MAP='m'; VFS.BASE='b'; VFS.ZIP='Mmeb'; VFS.RAW='r'
Spring.GetModOptions=function()
    return {forceallunits=true, experimentallegionfaction=true,
            experimentalextraunits=true, scavunitsforplayers=true}
end
Spring.GetGaiaTeamID=function() return 0 end
Spring.GetTeamInfo=function() return nil,nil,nil,nil,nil,0 end
Spring.GetAllyTeamList=function() return {} end
Game={gameSpeed=30,textColorCodes={Color=string.char(255),
    ColorAndOutline=string.char(254),Reset=string.char(8)}}
local errors = {}
local log = Spring.Log
Spring.Log = function(section, level, message)
    if level == LOG.ERROR then errors[#errors + 1] = tostring(section) .. ': ' .. tostring(message) end
    log(section, level, message)
end
VFS.Include('init.lua')
VFS_MODES=VFS.MAP .. VFS.MOD .. VFS.BASE
return {unitdefs=VFS.Include('gamedata/unitdefs.lua'), warnings=errors}
"""


def valid_catalog(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("game"), str) or not value["game"]:
        return False
    units, warnings = value.get("units"), value.get("warnings")
    if not isinstance(units, list) or not units or not isinstance(warnings, list):
        return False
    if not all(isinstance(warning, str) for warning in warnings):
        return False
    ids: set[str] = set()
    for unit in units:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or not unit["id"]:
            return False
        if not isinstance(unit.get("name"), str):
            return False
        builds = unit.get("builds")
        if builds is not None and (not isinstance(builds, list) or
                                   not all(isinstance(item, str) and item for item in builds)):
            return False
        key = unit["id"].casefold()
        if key in ids:
            return False
        ids.add(key)
    return True


def scan(engine: Path, game: str) -> dict:
    with os.add_dll_directory(str(engine)):
        return _scan(engine, game)


def _scan(engine: Path, game: str) -> dict:
    lib = ctypes.CDLL(str(engine / "unitsync.dll"))
    char, integer = ctypes.c_char_p, ctypes.c_int
    signatures = {
        "Init": ([ctypes.c_bool, integer], integer), "UnInit": ([], None), "GetNextError": ([], char),
        "GetPrimaryModCount": ([], integer), "GetPrimaryModInfoCount": ([integer], integer),
        "GetInfoKey": ([integer], char), "GetInfoType": ([integer], char), "GetInfoValueString": ([integer], char),
        "GetPrimaryModArchive": ([integer], char), "AddAllArchives": ([char], None),
        "lpOpenSource": ([char, char], integer), "lpExecute": ([], integer), "lpErrorLog": ([], char),
        "lpRootTable": ([], integer), "lpSubTableStr": ([char], integer), "lpPopTable": ([], None),
        "lpGetKeyExistsStr": ([char], integer), "lpGetStrKeyListCount": ([], integer),
        "lpGetStrKeyListEntry": ([integer], char), "lpGetIntKeyListCount": ([], integer),
        "lpGetIntKeyListEntry": ([integer], integer), "lpGetStrKeyStrVal": ([char, char], char),
        "lpGetIntKeyStrVal": ([integer, char], char), "lpClose": ([], None),
        "OpenFileVFS": ([char], integer), "FileSizeVFS": ([integer], integer),
        "ReadFileVFS": ([integer, ctypes.c_void_p, integer], integer), "CloseFileVFS": ([integer], None),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result

    def text(raw: bytes | None) -> str:
        return raw.decode("utf-8", "replace") if raw else ""

    def count(value: int, label: str) -> int:
        if value < 0:
            raise RuntimeError(f"Cannot read {label}: {text(lib.GetNextError())}")
        return value

    if not lib.Init(False, 0):
        raise RuntimeError(f"Cannot initialize unitsync: {text(lib.GetNextError())}")
    try:
        archives: set[bytes] = set()
        for index in range(count(lib.GetPrimaryModCount(), "games")):
            size = count(lib.GetPrimaryModInfoCount(index), "game information")
            info = {text(lib.GetInfoKey(i)): text(lib.GetInfoValueString(i)) for i in range(size)
                    if text(lib.GetInfoType(i)) == "string"}
            if info.get("name") == game:
                archive = lib.GetPrimaryModArchive(index)
                if not archive:
                    raise RuntimeError(f"Cannot read the archive for {game}: {text(lib.GetNextError())}")
                archives.add(archive)
        if len(archives) != 1:
            raise RuntimeError(f"Game {game!r} is missing or matches multiple archives; select a unique installed game.")
        lib.AddAllArchives(next(iter(archives)))
        error = text(lib.GetNextError())
        if error:
            raise RuntimeError(f"Cannot load the game archive: {error}")
        warnings: list[str] = []
        names: dict[str, str] = {}
        handle = lib.OpenFileVFS(b"language/en/units.json")
        if not handle:
            warnings.append("English unit names were not found in this game; showing unit IDs.")
        else:
            try:
                size = lib.FileSizeVFS(handle)
                if size < 0 or size > 20 * 1024 * 1024:
                    raise RuntimeError("The English unit-name file has an invalid size.")
                buffer = ctypes.create_string_buffer(size)
                if lib.ReadFileVFS(handle, buffer, size) != size:
                    raise RuntimeError("Could not read the complete English unit-name file.")
                try:
                    data = json.loads(buffer.raw.decode("utf-8"))
                    candidate = data["units"]["names"]
                    if not isinstance(candidate, dict) or not all(
                        isinstance(key, str) and isinstance(value, str) for key, value in candidate.items()
                    ):
                        raise ValueError("names must be a string-to-string object")
                    names = candidate
                except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                    warnings.append(f"Cannot read English unit names: {error}")
            finally:
                lib.CloseFileVFS(handle)
        if not lib.lpOpenSource(_UNIT_SCRIPT, b"Mmeb") or not lib.lpExecute():
            raise RuntimeError(f"Cannot evaluate unit definitions: {text(lib.lpErrorLog())}")
        try:
            if not lib.lpRootTable() or not lib.lpSubTableStr(b"warnings"):
                raise RuntimeError("The game definition parser returned no warning list.")
            warning_keys = [lib.lpGetIntKeyListEntry(i) for i in range(lib.lpGetIntKeyListCount())]
            warnings.extend(text(lib.lpGetIntKeyStrVal(key, b"")) for key in warning_keys)
            lib.lpPopTable()
            if not lib.lpSubTableStr(b"unitdefs"):
                raise RuntimeError("The game definition parser returned no unit definitions.")
            keys = [lib.lpGetStrKeyListEntry(i) for i in range(lib.lpGetStrKeyListCount())]
            units = []
            for key in keys:
                unit_id = text(key)
                if not key or not lib.lpSubTableStr(key):
                    raise RuntimeError(f"Invalid unit definition: {unit_id!r}")
                name = names.get(unit_id) or text(lib.lpGetStrKeyStrVal(b"name", b""))
                builds: list[str] | None = []
                if lib.lpGetKeyExistsStr(b"buildoptions"):
                    if not lib.lpSubTableStr(b"buildoptions"):
                        builds = None
                    else:
                        if lib.lpGetStrKeyListCount():
                            builds = None
                        else:
                            build_keys = [lib.lpGetIntKeyListEntry(i) for i in range(lib.lpGetIntKeyListCount())]
                            builds = [text(lib.lpGetIntKeyStrVal(i, b"")) for i in sorted(build_keys)]
                            if any(not entry for entry in builds):
                                builds = None
                        lib.lpPopTable()
                units.append({"id": unit_id, "name": name, "builds": builds})
                lib.lpPopTable()
        finally:
            lib.lpClose()
        result = {"game": game, "units": units, "warnings": warnings}
        if not valid_catalog(result):
            raise RuntimeError("The parsed unit catalog is invalid or empty.")
        return result
    finally:
        lib.UnInit()


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("Usage: unit_catalog ENGINE DATA WRITE OUTPUT GAME")
    engine, data, write, output = map(Path, sys.argv[1:5])
    os.environ["SPRING_WRITEDIR"] = str(write)
    os.environ["SPRING_DATADIR"] = str(data)
    try:
        result = scan(engine, sys.argv[5])
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except (OSError, RuntimeError, AttributeError) as error:
        print(f"Unit catalog failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
