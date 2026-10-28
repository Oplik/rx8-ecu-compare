import unittest

from ecu_compare.compare import compare_roms
from ecu_compare.defs import DefinitionDB
from ecu_compare.report import write_report
from ecu_compare.rom import load_rom
from ecu_compare.safety import ROOT, assert_inside_root


_HAS_SAMPLES = (ROOT / "data" / "roms" / "ORIG.bin").is_file() and (
    ROOT / "data" / "roms" / "EditedMap.bin"
).is_file()


@unittest.skipUnless(_HAS_SAMPLES, "sample ROMs are not part of the repository")
class ReportTests(unittest.TestCase):
    def test_write_stays_inside_project(self):
        db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs.xml")
        a = load_rom(ROOT / "data" / "roms" / "ORIG.bin", db)
        b = load_rom(ROOT / "data" / "roms" / "EditedMap.bin", db)
        result = compare_roms(a, b)
        path = write_report(result)
        assert_inside_root(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("7500 RPM", text)
        self.assertIn("8000 RPM", text)
        self.assertIn("hex 45 EA 60 00 -> 45 FA 00 00", text)
        self.assertIn("EXTENDED LISTING", text)
        self.assertIn("Converted A", text)
