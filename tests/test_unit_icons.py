import gzip
import hashlib
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

from app import game_sources
from app.settings import Settings
from app.unit_icons import clear_icon_cache, icon_status, read_icon


@unittest.skipIf(Image is None, "Install Pillow to test unit icon conversion.")
class UnitIconTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(str(self.root), "engine")
        self.directory = self.root / "data" / "games" / "BAR.sdd" / "unitpics"
        self.directory.mkdir(parents=True)

    def test_reads_dds_as_png_without_modifying_source(self):
        if Image is None:
            self.skipTest("Install Pillow to test unit icon conversion.")
        path = self.directory / "armck.dds"
        Image.new("RGBA", (3, 2), (255, 0, 50, 128)).save(path, format="DDS")
        original = path.read_bytes()
        result = read_icon(self.settings, "ARMCK")
        assert result is not None
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (3, 2))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 50, 128))
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(read_icon(self.settings, "missing"))

    def test_invalid_ids_and_corrupt_icons_are_reported(self):
        for unit_id in ("../armck", "armck.dds", "", "a" * 129):
            with self.subTest(unit_id=unit_id), self.assertRaisesRegex(ValueError, "Invalid unit ID"):
                read_icon(self.settings, unit_id)
        (self.directory / "armck.dds").write_bytes(b"not DDS")
        with self.assertRaisesRegex(ValueError, "Cannot decode unit icon"):
            read_icon(self.settings, "armck")

    def test_reads_dxt_compressed_icon(self):
        if Image is None:
            self.skipTest("Install Pillow to test unit icon conversion.")
        raw = io.BytesIO()
        Image.new("RGBA", (4, 4)).save(raw, format="DDS")
        header = bytearray(raw.getvalue()[:128])
        struct.pack_into("<I", header, 80, 4)
        header[84:88] = b"DXT1"
        struct.pack_into("<IIIII", header, 88, 0, 0, 0, 0, 0)
        (self.directory / "armck.dds").write_bytes(header + struct.pack("<HHI", 0xf800, 0, 0))
        result = read_icon(self.settings, "armck")
        assert result is not None
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_unavailable_directory_and_pillow_are_reported(self):
        self.assertTrue(icon_status(self.settings)["available"])
        self.assertFalse(icon_status(None)["available"])
        with patch("app.unit_icons.Image", None):
            self.assertIn("Pillow", icon_status(self.settings)["message"])
        self.directory.rmdir()
        self.assertIn("not found", icon_status(self.settings)["message"])

    def test_symlink_icon_is_rejected(self):
        target = self.root / "other.dds"
        target.write_bytes(b"data")
        try:
            (self.directory / "armck.dds").symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks is not available.")
        with self.assertRaisesRegex(ValueError, "link"):
            read_icon(self.settings, "armck")

    def make_package(self, digest, game, entries, *, tag="byar:git:fixture", mtime=None):
        data = self.root / "data"
        package = data / "packages" / f"{digest}.sdp"
        package.parent.mkdir(parents=True, exist_ok=True)
        index = data / "rapid" / "repo" / "byar" / "versions.gz"
        index.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(index, "at", encoding="utf-8") as source:
            source.write(f"{tag},{digest},,{game}\n")
        manifest = bytearray()
        pools = {}
        for name, body in entries.items():
            size = len(body) if isinstance(body, bytes) else body
            body = body if isinstance(body, bytes) else b"oversized"
            filename = name.encode()
            checksum = hashlib.md5(body, usedforsecurity=False).digest()
            manifest.extend(bytes([len(filename)]) + filename + checksum + bytes(4) + struct.pack(">I", size))
            pool = data / "pool" / checksum.hex()[:2] / f"{checksum.hex()[2:]}.gz"
            pool.parent.mkdir(parents=True, exist_ok=True)
            pool.write_bytes(gzip.compress(body))
            pools[name] = pool
        package.write_bytes(gzip.compress(manifest))
        if mtime is not None:
            os.utime(package, (mtime, mtime))
        return pools

    def dds(self, color=(255, 0, 50, 128)):
        output = io.BytesIO()
        Image.new("RGBA", (3, 2), color).save(output, format="DDS")
        return output.getvalue()

    def test_packaged_icons_are_available_and_unpacked_icons_take_precedence(self):
        expected = (255, 0, 50, 128)
        self.directory.rmdir()
        self.make_package("a" * 32, "Beyond All Reason test-1", {"unitpics/ARMCK.dds": self.dds(expected)})
        self.assertTrue(icon_status(self.settings)["available"])
        self.assertIsNone(read_icon(self.settings, "unknown"))
        result = read_icon(self.settings, "ARMCK")
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.getpixel((0, 0)), expected)
        self.directory.mkdir()
        self.assertEqual(read_icon(self.settings, "armck"), result)
        (self.directory / "armck.dds").write_bytes(self.dds((0, 255, 0, 255)))
        self.assertNotEqual(read_icon(self.settings, "armck"), result)

    def test_catalog_game_precedes_latest_installed_package(self):
        previous = "Beyond All Reason test-2"
        latest = "Beyond All Reason test-10"
        self.directory.rmdir()
        self.make_package("b" * 32, previous, {"unitpics/armck.dds": self.dds()}, tag="byar:test", mtime=1000)
        self.make_package("a" * 32, latest, {"unitpics/corck.dds": self.dds()}, mtime=2000)
        self.assertIsNone(read_icon(self.settings, "armck"))
        self.assertIsNotNone(read_icon(self.settings, "corck", catalog_game="Uninstalled BAR"))
        self.assertIsNotNone(read_icon(self.settings, "armck", catalog_game=previous))

    def test_package_manifest_is_reused_for_status_and_individual_icons(self):
        self.directory.rmdir()
        self.make_package("a" * 32, "Beyond All Reason test-1", {"unitpics/armck.dds": self.dds()})
        with patch("app.game_sources._package_entries", wraps=game_sources._package_entries) as read_manifest:
            self.assertTrue(icon_status(self.settings)["available"])
            self.assertIsNotNone(read_icon(self.settings, "armck"))
            self.assertIsNone(read_icon(self.settings, "corck"))
            self.assertEqual(read_manifest.call_count, 1)
            clear_icon_cache()
            self.assertIsNotNone(read_icon(self.settings, "armck"))
            self.assertEqual(read_manifest.call_count, 2)

    def test_corrupt_and_oversized_pooled_icons_are_rejected(self):
        self.directory.rmdir()
        body = self.dds()
        pools = self.make_package("a" * 32, "Beyond All Reason test-1", {
            "unitpics/armck.dds": body,
            "unitpics/oversized.dds": 16 * 1024 * 1024 + 1,
        })
        pool = pools["unitpics/armck.dds"]
        for packed, message in (
            (gzip.compress(bytes(len(body))), "integrity"),
            (gzip.compress(body + b"extra"), "integrity"),
            (b"invalid gzip", "Cannot read"),
        ):
            with self.subTest(message=message):
                pool.write_bytes(packed)
                with self.assertRaisesRegex(ValueError, message):
                    read_icon(self.settings, "armck")
        with self.assertRaisesRegex(ValueError, "too large"):
            read_icon(self.settings, "oversized")

    def test_pooled_icon_dimensions_are_limited(self):
        self.directory.rmdir()
        self.make_package("a" * 32, "Beyond All Reason test-1", {"unitpics/armck.dds": self.dds()})
        with patch("app.unit_icons.MAX_ICON_PIXELS", 5):
            with self.assertRaisesRegex(ValueError, "dimensions are too large"):
                read_icon(self.settings, "armck")

    def test_linked_package_and_pool_files_are_rejected(self):
        self.directory.rmdir()
        digest = "a" * 32
        pools = self.make_package(digest, "Beyond All Reason test-1", {"unitpics/armck.dds": self.dds()})
        for path in (self.root / "data" / "packages" / f"{digest}.sdp", pools["unitpics/armck.dds"]):
            with self.subTest(path=path):
                target = path.with_suffix(".original")
                path.rename(target)
                try:
                    try:
                        path.symlink_to(target)
                    except OSError:
                        self.skipTest("Creating symlinks is not available.")
                    with self.assertRaisesRegex(ValueError, "contains a link"):
                        read_icon(self.settings, "armck")
                finally:
                    path.unlink(missing_ok=True)
                    target.rename(path)


if __name__ == "__main__":
    unittest.main()
