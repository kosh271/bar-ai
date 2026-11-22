"""Scheduling and standings for Studio tournaments and parameter sweeps."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterator

from .headless import Player


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    player: Player
    promotable: bool = False


def matrix(candidates: list[Candidate], maps: list[str], mode: str) -> Iterator[tuple[str, Candidate, Candidate]]:
    if mode == "sweep":
        baseline = candidates[-1]
        for map_name in maps:
            for candidate in candidates[:-1]:
                yield map_name, candidate, baseline
                yield map_name, baseline, candidate
        return
    pairs = list(combinations(candidates, 2))
    for reverse_pass in (False, True):
        for map_index in range(len(maps)):
            swap = reverse_pass != bool(map_index % 2)
            for index, (first, second) in enumerate(pairs):
                map_name = maps[(map_index + index) % len(maps)]
                yield map_name, second if swap else first, first if swap else second


def rankings(candidates: list[Candidate], games: list[dict]) -> list[dict]:
    rows = {
        candidate.id: {
            "id": candidate.id, "label": candidate.label, "promotable": False,
            "wins": 0, "losses": 0, "draws": 0, "games": 0, "win_rate": None,
            "average_duration_seconds": None, "script_errors": 0, "engine_errors": 0,
            "error_free_games": 0, "failures": 0, "timeouts": 0, "_durations": [], "_decided_sides": {},
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
                row["_decided_sides"].setdefault(game["map"], {})[side] = score
            if outcome == "failed":
                row["failures"] += 1
            if outcome == "timeout":
                row["timeouts"] += 1
            if game["simulated_seconds"] is not None:
                row["_durations"].append(game["simulated_seconds"])
            if ((outcome in ("side1", "side2", "draw") or game["simulated_limit"]) and not game["engine_error"]
                    and not script_errors and not game["unassigned_script_errors"]):
                row["error_free_games"] += 1
    for candidate in candidates:
        row = rows[candidate.id]
        decided = row["wins"] + row["losses"] + row["draws"]
        durations = row.pop("_durations")
        paired = [score for sides in row.pop("_decided_sides").values()
                  if len(sides) == 2 for score in sides.values()]
        if decided:
            row["win_rate"] = row["wins"] / decided
        if durations:
            row["average_duration_seconds"] = round(sum(durations) / len(durations), 2)
        paired_wins = paired.count("wins")
        row["paired_win_rate"] = paired_wins / len(paired) if paired else None
        row["promotable"] = candidate.promotable and paired_wins > 0
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
    frames = None if "smoke_passed" in metadata else telemetry.get("end_frame")
    simulated_seconds = frames / 30 if type(frames) is int and 0 <= frames < 2**31 else None
    script_errors = telemetry.get("ai_script_errors", 0)
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
        "script_errors_by_side": side_errors,
        "unassigned_script_errors": script_errors - sum(side_errors.values()),
        "engine_error": bool(metadata.get("engine_exit")),
    }
