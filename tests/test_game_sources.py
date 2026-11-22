import gzip
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from app.game_sources import GameSourceError, _package_entries, list_game_sources, stock_directory
from app.projects import create_project

TEST_DIR = Path(__file__).resolve().parent


class GameSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.digest = "0123456789abcdef0123456789abcdef"
        self.entries = {
            "luarules/configs/barb/stable/config/base.json": b"// stock JSON\n",
            "luarules/configs/barb/stable/config/hard/base.json": b"// hard JSON\n",
            "luarules/configs/barb/stable/script/common.as": b"shared\n",
            "luarules/configs/barb/stable/script/hard/init.as": b'#include "../common.as"\n',
            "luarules/configs/barb/stable/script/hard/main.as": b"main\n",
        }

    def make_package(self):
        manifest = bytearray()
        for name, body in self.entries.items():
            filename = name.encode("utf-8")
            digest = hashlib.md5(body, usedforsecurity=False).digest()
            manifest.extend(bytes([len(filename)]) + filename + digest + b"\x00" * 4 + struct.pack(">I", len(body)))
            hexdigest = digest.hex()
            pool = self.data / "pool" / hexdigest[:2] / (hexdigest[2:] + ".gz")
            pool.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(pool, "wb") as file:
                file.write(body)
        package = self.data / "packages" / f"{self.digest}.sdp"
        package.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(package, "wb") as file:
            file.write(manifest)
        index = self.data / "rapid" / "repo" / "byar" / "versions.gz"
        index.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(index, "wt", encoding="utf-8") as file:
            file.write(f"byar:git:abc,{self.digest},,Beyond All Reason test-42\n")
            file.write(f"byar:test,{self.digest},,Beyond All Reason test-42\n")
        (self.data / "infolog.txt").write_text(
            '[PreGame::AddModArchivesToVFS] using game "Beyond All Reason test-42"\n',
            encoding="utf-8",
        )

    def test_suggests_last_used_package_and_copies_hard(self):
        self.make_package()
        sources, suggested = list_game_sources(self.data, None)
        self.assertEqual(len(sources), 2)
        self.assertIsNotNone(suggested)
        assert suggested is not None
        self.assertEqual(suggested.tag, "byar:test")
        projects = self.root / "projects"
        with stock_directory(suggested, projects) as stock:
            project = create_project(projects, "My AI", stock, source_label=suggested.label)
        profile = project / "profiles" / "hard"
        self.assertEqual((profile / "config" / "base.json").read_text(), "// hard JSON\n")
        self.assertEqual((profile / "script" / "init.as").read_text(), '#include "__shared__/common.as"\n')
        self.assertFalse(any(path.name.startswith(".stock-") for path in projects.iterdir()))

    def test_does_not_suggest_when_last_game_is_unknown(self):
        self.make_package()
        (self.data / "infolog.txt").write_text("no game identity\n", encoding="utf-8")
        sources, suggested = list_game_sources(self.data, None)
        self.assertEqual(len(sources), 2)
        self.assertIsNone(suggested)

    def test_does_not_suggest_when_game_name_matches_multiple_packages(self):
        self.make_package()
        other = "abcdef0123456789abcdef0123456789"
        (self.data / "packages" / f"{other}.sdp").write_bytes(
            (self.data / "packages" / f"{self.digest}.sdp").read_bytes()
        )
        index = self.data / "rapid" / "repo" / "byar" / "versions.gz"
        with gzip.open(index, "at", encoding="utf-8") as file:
            file.write(f"byar:stable,{other},,Beyond All Reason test-42\n")
        _, suggested = list_game_sources(self.data, None)
        self.assertIsNone(suggested)

    def test_bad_pool_content_is_rejected(self):
        self.make_package()
        _, suggested = list_game_sources(self.data, None)
        assert suggested is not None
        body = self.entries["luarules/configs/barb/stable/config/base.json"]
        digest = hashlib.md5(body, usedforsecurity=False).hexdigest()
        pool = self.data / "pool" / digest[:2] / (digest[2:] + ".gz")
        with gzip.open(pool, "wb") as file:
            file.write(b"wrong")
        projects = self.root / "projects"
        with self.assertRaisesRegex(GameSourceError, "integrity check"):
            with stock_directory(suggested, projects):
                pass
        self.assertEqual(list(projects.iterdir()), [])

    def test_manifest_yields_valid_entry_before_reading_later_gzip_members(self):
        path = self.root / "game.sdp"
        digest = bytes.fromhex(self.digest)
        record = b"\x01a" + digest + bytes(4) + struct.pack(">I", 42)
        path.write_bytes(gzip.compress(record * 4096) + b"invalid gzip member")

        entries = _package_entries(path)
        self.assertEqual(next(entries), ("a", self.digest, 42))
        with self.assertRaisesRegex(GameSourceError, "Cannot read game package"):
            list(entries)

    def test_truncated_manifest_record_is_rejected(self):
        path = self.root / "game.sdp"
        path.write_bytes(gzip.compress(b"\x01a" + bytes(23)))
        with self.assertRaisesRegex(GameSourceError, "Invalid game package manifest"):
            list(_package_entries(path))

    def test_rejects_package_paths_outside_profile_tree(self):
        self.entries["luarules/configs/barb/stable/script/../escape.as"] = b"bad"
        self.make_package()
        _, suggested = list_game_sources(self.data, None)
        assert suggested is not None
        projects = self.root / "projects"
        with self.assertRaisesRegex(GameSourceError, "Unsafe filename"):
            with stock_directory(suggested, projects):
                pass
        self.assertEqual(list(projects.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
