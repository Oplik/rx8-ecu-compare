import unittest

from ecu_compare.compare import change_blurb, change_direction, compare_roms, key_changes
from ecu_compare.defs import DefinitionDB
from ecu_compare.report import render_report
from ecu_compare.rom import load_rom
from ecu_compare.safety import ROOT


_HAS_SAMPLES = (ROOT / "data" / "roms" / "ORIG.bin").is_file() and (
    ROOT / "data" / "roms" / "EditedMap.bin"
).is_file()


@unittest.skipUnless(_HAS_SAMPLES, "sample ROMs are not part of the repository")
class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs.xml")
        cls.orig = load_rom(ROOT / "data" / "roms" / "ORIG.bin", cls.db, source_hint="ORIG.bin")
        cls.edited = load_rom(ROOT / "data" / "roms" / "EditedMap.bin", cls.db, source_hint="EditedMap.bin")

    def test_identity(self):
        self.assertEqual(self.orig.cal_id(), "60E0G500")
        self.assertEqual(self.orig.rom_id.xmlid, "N3ZGEC")
        self.assertGreater(len(self.orig.tables), 50)

    def test_identical_files(self):
        result = compare_roms(self.orig, self.orig)
        self.assertEqual(result.differing_bytes, 0)
        self.assertEqual(result.tables_changed, 0)
        self.assertEqual(result.unmapped_count, 0)

    def test_orig_vs_edited(self):
        result = compare_roms(self.orig, self.edited)
        self.assertEqual(result.differing_bytes, 330)
        self.assertGreater(result.tables_changed, 0)
        names = {d.name for d in result.table_diffs}
        # EditedMap is a real tune; at least some named maps should show up.
        self.assertTrue(any("Ignition" in n or "Fuel" in n or "Limit" in n for n in names) or result.tables_changed >= 1)
        mapped_bytes = 0
        for cell in result.mapped_changes:
            mapped_bytes += max(len(cell.hex_a.replace(" ", "")) // 2, 1)
        self.assertGreater(mapped_bytes, 0)
        headlines = key_changes(result, limit=12)
        names = [item.table_name for item in headlines]
        self.assertEqual(names[0], "Cold Rev Limit")
        self.assertLess(names.index("Normal Rev Limit"), names.index("Oil Metering By Load"))
        rev = next(item for item in headlines if item.table_name == "Normal Rev Limit")
        self.assertIn("7500", rev.summary)
        self.assertIn("8000", rev.summary)
        self.assertEqual(rev.direction, 1)
        self.assertEqual(headlines[0].direction, -1)
        cold = next(item for item in result.table_diffs if item.name == "Cold Rev Limit")
        cold_line = change_blurb(cold)
        self.assertIn("5500", cold_line)
        self.assertIn("4000", cold_line)
        self.assertTrue(cold_line.startswith("▼"))
        self.assertEqual(change_direction(cold), -1)
        self.assertEqual(change_direction(next(item for item in result.table_diffs if item.name == "Normal Rev Limit")), 1)
        oil = next(item for item in result.table_diffs if item.name == "Oil Metering By Load")
        self.assertIn("cells", change_blurb(oil))
        report = render_report(result)
        self.assertIn("NAMED TABLE CHANGES", report)
        self.assertIn("EXTENDED LISTING", report)
        self.assertIn("Converted A", report)
        self.assertIn("ORIG.bin", report)
        self.assertIn("EditedMap.bin", report)

    def test_rev_limit_decodes(self):
        table = self.orig.tables["Normal Rev Limit"]
        self.assertIsNone(table.decode_error)
        self.assertEqual(table.rows(), 1)
        value = table.data_scaled[0][0]
        self.assertIsNotNone(value)
        self.assertGreater(value, 5000)
        self.assertLess(value, 12000)
        self.assertEqual(table.definition.storagetype, "float")

    def test_base_timing_shape(self):
        table = self.orig.tables["Ignition Leading - Base Timing"]
        self.assertIsNone(table.decode_error)
        self.assertEqual(table.rows(), 18)
        self.assertEqual(table.cols(), 20)
        sample = table.data_scaled[0][0]
        self.assertIsNotNone(sample)
        self.assertGreater(sample, -60)
        self.assertLess(sample, 60)


if __name__ == "__main__":
    unittest.main()
