import unittest

from ecu_compare.app import DEFS_PATH
from ecu_compare.compare import compare_roms
from ecu_compare.defs import DefinitionDB
from ecu_compare.rom import load_rom
from ecu_compare.safety import ROOT


class NewDefsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "data" / "defs" / "rx8_defs_10.2.2026.xml"
        cls.db = DefinitionDB(cls.path)
        orig = ROOT / "data" / "roms" / "ORIG.bin"
        cls.rom = load_rom(orig, cls.db, source_hint="ORIG.bin") if orig.is_file() else None

    def test_app_loads_the_downloaded_defs(self):
        self.assertEqual(DEFS_PATH, self.path)
        self.assertTrue(self.path.is_file())

    def test_orig_still_matches(self):
        if self.rom is None:
            self.skipTest("sample ROM is not part of the repository")
        self.assertEqual(self.rom.cal_id(), "60E0G500")
        self.assertEqual(self.rom.rom_id.xmlid, "N3ZGEC")
        rev = self.rom.tables["Normal Rev Limit"]
        self.assertIsNone(rev.decode_error)
        self.assertEqual(rev.data_scaled[0][0], 7500)

    def test_off_throttle_fuel_cut_is_the_old_decel_map(self):
        if self.rom is None:
            self.skipTest("sample ROM is not part of the repository")
        table = self.rom.tables["Off Throttle Fuel Cut - Fuel Resume - In Neutral Threshold"]
        self.assertEqual(table.definition.storageaddress, 0x06D1E0)
        self.assertEqual(table.category, "Off Throttle Fuel Cut")
        self.assertEqual(table.data_scaled[0][0], 2500)
        self.assertEqual(table.data_scaled[0][-1], 1400)
        self.assertEqual(table.units, "RPM")

    def test_erased_launch_control_is_blank_and_not_a_change(self):
        if self.rom is None:
            self.skipTest("sample ROM is not part of the repository")
        table = self.rom.tables["Launch Control - Engine Speed Setpoint"]
        self.assertEqual(table.data_bytes, b"\xff\xff\xff\xff")
        self.assertEqual(table.data_errors[0][0], "blank")
        self.assertIsNone(table.data_scaled[0][0])
        result = compare_roms(self.rom, self.rom)
        self.assertEqual(result.tables_changed, 0)
        names = {item.name for item in result.table_diffs}
        self.assertNotIn("Launch Control - Engine Speed Setpoint", names)
        self.assertNotIn("Launch Control - Engine Speed Setpoint Hysteresis", names)

    def test_exact_ecu_id_beats_a_shorter_prefix(self):
        data = bytearray(0x70000)
        data[0x6C046 : 0x6C046 + 6] = b"N3J1EL"
        _rom, romid, _ids = self.db.identify(bytes(data))
        self.assertIsNotNone(romid)
        self.assertEqual(romid.xmlid, "N3J1EL")


if __name__ == "__main__":
    unittest.main()
