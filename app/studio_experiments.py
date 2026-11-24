"""Scheduling and standings for Studio tournaments and parameter sweeps."""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from typing import Iterator

from .headless import MatchError, Player, open_artifact, side_totals
from .installation import _link
from .settings import FACTIONS


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    player: Player
    promotable: bool = False
    faction_policy: str = ""

    @property
    def profile_label(self) -> str:
        return f"{self.player.name} · {self.player.profile or 'Default'}"


MIN_PROMOTION_PAIRS = 2


def run_provenance(engine: Path, maps: list[str], players: list[Player],
                   *, should_stop=lambda: False) -> dict:
    """Capture executable and selected AI bytes before starting a run."""
    def file_digest(path: Path) -> bytes:
        digest = hashlib.sha256()
        with open_artifact(engine, *path.relative_to(engine).parts) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if should_stop():
                    raise MatchError("Stopped while recording run provenance.")
                digest.update(chunk)
        return digest.digest()

    def ai_digest(short_name: str, version: str) -> str | None:
        if any(not key or key in {".", ".."} or any(char in key for char in "/\\:\x00")
               for key in (short_name, version)):
            return None
        root = engine / "AI" / "Skirmish" / short_name / version
        if any(_link(path) for path in (engine, root.parent.parent.parent, root.parent.parent, root.parent, root)):
            return None
        if not root.is_dir():
            return None
        digest = hashlib.sha256()

        def unreadable(error: OSError) -> None:
            raise error

        for directory, folders, files in os.walk(root, onerror=unreadable):
            if should_stop():
                raise MatchError("Stopped while recording run provenance.")
            folder = Path(directory)
            if any(_link(folder / name) for name in (*folders, *files)):
                return None
            folders.sort()
            for name in sorted(files):
                path = folder / name
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(b"\0" + file_digest(path))
        return digest.hexdigest()

    def available(capture):
        try:
            return capture()
        except OSError:
            return None
        except MatchError:
            if should_stop():
                raise
            return None

    keys = dict.fromkeys((player.short_name, player.version) for player in players)
    return {
        "engine_sha256": available(lambda: file_digest(engine / "spring-headless.exe").hex()),
        "unitsync_sha256": available(lambda: file_digest(engine / "unitsync.dll").hex()),
        "game_archive_checksum": None,
        "map_archive_checksums": dict.fromkeys(maps),
        "archive_checksum_source": None,
        "archive_conflicts": [],
        "ais": [{"short_name": name, "version": version, "sha256": available(lambda: ai_digest(name, version))}
                for name, version in keys],
    }


def record_archive_checksums(provenance: dict, map_name: str, checksums: object) -> None:
    if not isinstance(checksums, dict):
        return
    conflicts = provenance["archive_conflicts"]
    for values, key, value, label in (
        (provenance, "game_archive_checksum", checksums.get("game"), "Game archive"),
        (provenance["map_archive_checksums"], map_name, checksums.get("map"), map_name),
    ):
        if not isinstance(value, str) or label in conflicts:
            continue
        provenance["archive_checksum_source"] = "engine_startup_log"
        if values.get(key) in (None, value):
            values[key] = value
        else:
            values[key] = None
            conflicts.append(label)


def matrix(candidates: list[Candidate], maps: list[str], mode: str, *, seed: int = 0
           ) -> Iterator[tuple[str, Candidate, Candidate]]:
    if mode == "sweep":
        baseline = candidates[-1]
        for map_name in maps:
            for candidate in candidates[:-1]:
                yield map_name, candidate, baseline
                yield map_name, baseline, candidate
        return
    rotation = list(candidates)
    if len(rotation) % 2:
        rotation.append(None)
    rounds = []
    for _ in range(len(rotation) - 1):
        rounds.append([(rotation[index], rotation[-(index + 1)]) for index in range(len(rotation) // 2)
                       if rotation[index] is not None and rotation[-(index + 1)] is not None])
        # Each player meets one opponent per round, with one bye for an odd roster.
        rotation.insert(1, rotation.pop())
    appearances: Counter[str] = Counter()
    random = Random(seed)

    def with_faction(candidate: Candidate) -> Candidate:
        if candidate.faction_policy == "Rotate":
            faction = FACTIONS[appearances[candidate.id] % len(FACTIONS)]
            appearances[candidate.id] += 1
        elif candidate.faction_policy == "Random":
            faction = random.choice(FACTIONS)
        else:
            return candidate
        return replace(candidate, player=replace(candidate.player, faction=faction),
                       label=f"{candidate.profile_label} · {faction}")

    pair_index = 0
    for map_index in range(len(maps)):
        for round_pairs in rounds:
            for first, second in round_pairs:
                map_name = maps[(map_index + pair_index) % len(maps)]
                first, second = with_faction(first), with_faction(second)
                yield map_name, first, second
                yield map_name, second, first
                pair_index += 1
        pair_index = 0


def paired_score(candidate_id: str, games: list[dict]) -> tuple[int, float | None]:
    """Score only clean, decided matches with the same opponent and swapped sides."""
    pairs: dict[tuple, dict] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        outcome = game.get("outcome")
        errors = game.get("script_errors_by_side")
        if (outcome not in ("side1", "side2", "draw") or game.get("telemetry_verified") is not True
                or game.get("engine_error") is not False
                or game.get("unassigned_script_errors") != 0 or not isinstance(errors, dict)
                or set(errors) != {"side1", "side2"} or any(errors.values())
                or not all(isinstance(game.get(key), str) for key in ("map", "side1", "side2"))):
            continue
        if candidate_id == game["side1"]:
            side, opponent = "side1", game["side2"]
            factions = tuple(game.get("factions") or ())
        elif candidate_id == game["side2"]:
            side, opponent = "side2", game["side1"]
            factions = tuple(reversed(game.get("factions") or ()))
        else:
            continue
        checksums = game.get("archive_checksums") or {}
        if not isinstance(checksums, dict) or not all(isinstance(value, str) for value in checksums.values()):
            continue
        key = (game["map"], opponent, factions, game.get("pair_index"), checksums.get("game"), checksums.get("map"))
        pairs.setdefault(key, {})[side] = 0.5 if outcome == "draw" else int(outcome == side)
    scores = [score for sides in pairs.values() if len(sides) == 2 for score in sides.values()]
    return len(scores) // 2, sum(scores) / len(scores) if scores else None


def rankings(candidates: list[Candidate], games: list[dict]) -> list[dict]:
    rows = {
        candidate.id: {
            "id": candidate.id, "label": candidate.label, "promotable": False,
            "wins": 0, "losses": 0, "draws": 0, "games": 0, "win_rate": None,
            "average_duration_seconds": None, "script_errors": 0, "engine_errors": 0,
            "error_free_games": 0, "failures": 0, "timeouts": 0, "_durations": [],
        }
        for candidate in candidates
    }
    for game in games:
        outcome = game["outcome"]
        if outcome == "cancelled":
            continue
        for side in ("side1", "side2"):
            row = rows[game[side]]
            row["games"] += 1
            script_errors = game["script_errors_by_side"][side]
            row["script_errors"] += script_errors
            row["engine_errors"] += int(game["engine_error"])
            if outcome in ("side1", "side2", "draw"):
                score = "draws" if outcome == "draw" else "wins" if outcome == side else "losses"
                row[score] += 1
            if outcome == "failed":
                row["failures"] += 1
            if outcome == "timeout":
                row["timeouts"] += 1
            if game["simulated_seconds"] is not None:
                row["_durations"].append(game["simulated_seconds"])
            if (game.get("telemetry_verified") is True and not game["engine_error"]
                    and (outcome in ("side1", "side2", "draw") or game["simulated_limit"])
                    and not script_errors and not game["unassigned_script_errors"]):
                row["error_free_games"] += 1
    for candidate in candidates:
        row = rows[candidate.id]
        decided = row["wins"] + row["losses"] + row["draws"]
        durations = row.pop("_durations")
        if decided:
            row["win_rate"] = row["wins"] / decided
        if durations:
            row["average_duration_seconds"] = round(sum(durations) / len(durations), 2)
        count, rate = paired_score(candidate.id, games)
        row["valid_pairs"] = count
        row["paired_win_rate"] = rate
        row["promotable"] = (candidate.promotable and count >= MIN_PROMOTION_PAIRS
                             and rate is not None and rate > 0.5)
    return sorted(rows.values(), key=lambda row: (
        row["win_rate"] is None, -(row["win_rate"] or 0.0),
        -(row["error_free_games"] / max(1, row["games"])),
        row["average_duration_seconds"] is None, row["average_duration_seconds"] or 0,
        row["label"].casefold(), row["id"],
    ))


def telemetry_metrics(metadata: dict) -> dict:
    telemetry = metadata.get("telemetry", {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    frames = (None if "smoke_passed" in metadata
              or telemetry.get("end_frame_source") in ("last_log_frame", "team_defeat")
              else telemetry.get("end_frame"))
    simulated_seconds = frames / 30 if type(frames) is int and 0 <= frames < 2**31 else None
    script_errors = telemetry.get("ai_script_errors")
    verified = ("smoke_passed" not in metadata and type(metadata.get("engine_exit")) is int
                and type(script_errors) is int and script_errors >= 0)
    if type(script_errors) is not int or script_errors < 0:
        script_errors = 0
    side_errors = {"side1": 0, "side2": 0}
    teams = telemetry.get("teams")
    for team in teams if isinstance(teams, list) else ():
        if not isinstance(team, dict):
            continue
        side = team.get("side")
        errors = team.get("ai_script_errors")
        if side in (1, 2) and type(errors) is int and errors >= 0:
            side_errors[f"side{side}"] += errors
    script_errors = max(script_errors, sum(side_errors.values()))
    return {
        "simulated_seconds": simulated_seconds, "script_errors": script_errors,
        "side_totals": side_totals(teams if isinstance(teams, list) and "smoke_passed" not in metadata else []),
        "script_errors_by_side": side_errors,
        "unassigned_script_errors": script_errors - sum(side_errors.values()),
        "engine_error": bool(metadata.get("engine_exit")),
        "telemetry_verified": verified,
    }
