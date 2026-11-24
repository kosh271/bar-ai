import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from collections import Counter
from itertools import islice

from app.headless import Player
from app.studio_experiments import Candidate, matrix, rankings


def candidate(key, *, eligible=False, policy=""):
    return Candidate(key, key, Player(key, "1", "hard", "Armada", 0), eligible, policy)


def result(map_name, first, second, winner, *, errors=0):
    return {"map": map_name, "side1": first, "side2": second, "outcome": winner,
            "simulated_seconds": 60, "script_errors_by_side": {"side1": errors, "side2": 0},
            "unassigned_script_errors": 0, "engine_error": False, "simulated_limit": False,
            "telemetry_verified": True}


class ExperimentDesignTests(unittest.TestCase):
    def test_tight_tournament_budgets_cover_every_candidate_on_both_sides(self):
        candidates = [candidate(str(index), policy="Random") for index in range(8)]
        games = list(islice(matrix(candidates, ["A", "B", "C"], "tournament"), 24))
        counts = Counter((entry.id, side) for _, first, second in games
                         for entry, side in ((first, 1), (second, 2)))
        self.assertEqual(len(counts), 16)
        self.assertEqual({name for name, _, _ in games}, {"A", "B", "C"})
        for i in range(0, len(games), 2):
            name, first, second = games[i]
            self.assertEqual(games[i + 1], (name, second, first))

    def test_odd_roster_visits_each_opponent_and_map_once_per_side(self):
        candidates = [candidate(str(index), policy="Random") for index in range(5)]
        games = list(matrix(candidates, ["A", "B"], "tournament", seed=10))
        self.assertEqual(len(games), 40)
        self.assertEqual(len({(name, first.id, second.id) for name, first, second in games}), 40)
        self.assertEqual(games, list(matrix(candidates, ["A", "B"], "tournament", seed=10)))
        self.assertNotEqual(games, list(matrix(candidates, ["A", "B"], "tournament", seed=20)))

    def test_promotion_needs_a_majority_of_at_least_two_clean_pairs(self):
        entries = [candidate("v", eligible=True), candidate("b")]
        losing = [result("A", "v", "b", "side1"), result("A", "b", "v", "side1")]
        losing += [result("B", "v", "b", "side2"), result("B", "b", "v", "side1")]
        variant = lambda games: next(row for row in rankings(entries, games) if row["id"] == "v")
        self.assertFalse(variant(losing)["promotable"])
        wins = [result(name, first, second, win) for name in ("A", "B")
                for first, second, win in (("v", "b", "side1"), ("b", "v", "side2"))]
        self.assertFalse(variant(wins[:2])["promotable"])
        self.assertTrue(variant(wins)["promotable"])
        wins[-1]["telemetry_verified"] = False
        self.assertFalse(variant(wins)["promotable"])
        wins[-1]["telemetry_verified"] = True
        wins[-1]["script_errors_by_side"]["side1"] = 1
        self.assertFalse(variant(wins)["promotable"])

    def test_games_against_different_opponents_cannot_complete_a_pair(self):
        entries = [candidate("v", eligible=True), candidate("b"), candidate("c")]
        games = [result(name, first, second, win) for name in ("A", "B")
                 for first, second, win in (("v", "b", "side1"), ("c", "v", "side2"))]
        variant = next(row for row in rankings(entries, games) if row["id"] == "v")
        self.assertFalse(variant["promotable"])
        self.assertEqual(variant["valid_pairs"], 0)

    def test_provenance_changes_with_installed_profile_bytes(self):
        from app.studio_experiments import record_archive_checksums, run_provenance

        with TemporaryDirectory() as temporary:
            engine = Path(temporary)
            (engine / "spring-headless.exe").write_bytes(b"a")
            profile = engine / "AI" / "Skirmish" / "v" / "1" / "config" / "hard"
            profile.mkdir(parents=True)
            config = profile / "config.json"
            config.write_bytes(b"b")
            args = (engine, ["A", "B"], [candidate("v").player])
            initial = run_provenance(*args)
            self.assertEqual(initial["map_archive_checksums"], {"A": None, "B": None})
            self.assertIsNotNone(initial["engine_sha256"])
            self.assertIsNotNone(initial["ais"][0]["sha256"])
            self.assertEqual(initial, run_provenance(*args))
            config.write_bytes(b"c")
            changed = run_provenance(*args)
            self.assertNotEqual(initial["ais"][0]["sha256"], changed["ais"][0]["sha256"])
            record_archive_checksums(changed, "A", {"game": "01020304", "map": "10203040"})
            self.assertEqual(changed["game_archive_checksum"], "01020304")
            self.assertEqual(changed["map_archive_checksums"], {"A": "10203040", "B": None})
            record_archive_checksums(changed, "A", {"game": "01020304", "map": "a0203040"})
            self.assertEqual(changed["map_archive_checksums"], {"A": None, "B": None})
            self.assertEqual(changed["archive_conflicts"], ["A"])

    def test_archive_checksums_only_read_the_native_startup_block(self):
        from app.headless import archive_checksums

        game, map_checksum = "ff" * 64, "ac" * 64
        output = f"[t=00:00:01.485457][f=-000001] [PreGame::StartServer]\n\tmod-checksum={game}\n\tmap-checksum={map_checksum}\n"
        self.assertEqual(archive_checksums(output), {"game": game, "map": map_checksum})
        self.assertEqual(archive_checksums(output.split("\n", 1)[1]), {})
