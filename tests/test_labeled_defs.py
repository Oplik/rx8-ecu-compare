import unittest

from ecu_compare.defs import DefinitionDB
from ecu_compare.rom import load_rom
from ecu_compare.safety import ROOT


_HAS_ORIG = (ROOT / "data" / "roms" / "ORIG.bin").is_file()


@unittest.skipUnless(_HAS_ORIG, "sample ROM is not part of the repository")
class LabeledDefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs_labeled.xml")
        cls.rom = load_rom(ROOT / "data" / "roms" / "ORIG.bin", cls.db)

    def test_decel_fuel_cut(self):
        t = self.rom.tables["Decel Fuel Cut"]
        self.assertEqual(t.category, "Fuel Cut")
        self.assertEqual(t.definition.storageaddress, 0x06D1E0)
        self.assertEqual(t.cols(), 16)
        self.assertEqual(t.data_raw[0][0], 2500)
        self.assertEqual(t.data_raw[0][-1], 1400)
        self.assertEqual(t.y_axis.name, "Coolant Temperature")

    def test_restore_by_gear(self):
        t = self.rom.tables["Decel Fuel Cut Restore - By Gear"]
        self.assertEqual(t.rows(), 6)
        self.assertEqual(t.cols(), 16)
        self.assertEqual(t.x_axis.name, "Coolant Temperature")
        self.assertEqual(t.y_axis.name, "Gear")

    def test_stock_name_gone(self):
        self.assertNotIn("Table 2D - 5", self.rom.tables)

    def test_still_identifies_orig(self):
        self.assertEqual(self.rom.cal_id(), "60E0G500")
        self.assertEqual(self.rom.rom_id.xmlid, "N3ZGEC")
