import unittest

from ecu_compare.defs import DefinitionDB
from ecu_compare.safety import ROOT


class DefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs.xml")

    def test_base_exists(self):
        base = self.db.get("RX8Base")
        self.assertIsNotNone(base)
        self.assertGreater(len(base.tables), 50)
        self.assertIn("Ignition Leading - Base Timing", base.tables)
        self.assertIn("Normal Rev Limit", base.tables)

    def test_child_inherits_and_has_addresses(self):
        rom = self.db.get("N3ZGEC")
        self.assertIsNotNone(rom)
        table = rom.tables["Ignition Leading - Base Timing"]
        self.assertEqual(table.table_type.lower(), "3d")
        self.assertEqual(table.sizex, 20)
        self.assertEqual(table.sizey, 18)
        self.assertIsNotNone(table.storageaddress)
        self.assertIsNotNone(table.x_axis)
        self.assertIsNotNone(table.x_axis.storageaddress)

    def test_shared_romids(self):
        a = self.db.get("60E0G500")
        b = self.db.get("N3ZGEC")
        self.assertIs(a, b)

    def test_case_insensitive_base(self):
        rom = self.db.get("N3ZGEC")
        self.assertEqual((rom.base_name or "").lower(), "rx8base")

    @unittest.skipUnless((ROOT / "data" / "roms" / "ORIG.bin").is_file(), "sample ROM is not part of the repository")
    def test_identify_orig(self):
        data = (ROOT / "data" / "roms" / "ORIG.bin").read_bytes()
        rom, romid, ids = self.db.identify(data)
        self.assertIsNotNone(rom)
        self.assertIsNotNone(romid)
        self.assertTrue("N3ZGEC" in ids or romid.xmlid == "N3ZGEC")
        self.assertEqual(romid.xmlid, "N3ZGEC")


if __name__ == "__main__":
    unittest.main()
