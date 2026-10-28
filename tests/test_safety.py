import tempfile
import unittest
from pathlib import Path

from ecu_compare.safety import ROOT, SafetyError, assert_inside_root, import_rom, write_text


class SafetyTests(unittest.TestCase):
    def test_root_contains_the_program(self):
        self.assertTrue((ROOT / "src" / "ecu_compare" / "app.py").is_file())

    def test_inside_root_ok(self):
        path = assert_inside_root(ROOT / "src" / "ecu_compare" / "safety.py")
        self.assertTrue(path.is_file())

    def test_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SafetyError):
                assert_inside_root(Path(tmp) / "outside.bin")

    def test_write_outside_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SafetyError):
                write_text(Path(tmp) / "nope.txt", "x")

    def test_import_rom_stays_inside(self):
        roms = ROOT / "data" / "roms"
        before = set(roms.glob("sample*.bin"))
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.bin"
            src.write_bytes(b"\x11" * 64)
            dest = import_rom(src)
            try:
                assert_inside_root(dest)
                self.assertEqual(dest.read_bytes(), src.read_bytes())
            finally:
                if dest not in before and dest.is_file():
                    dest.unlink()


if __name__ == "__main__":
    unittest.main()
