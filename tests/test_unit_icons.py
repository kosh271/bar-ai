import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

from app.settings import Settings
from app.unit_icons import icon_status, read_icon


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


if __name__ == "__main__":
    unittest.main()
