import unittest
from pathlib import Path

from ecu_compare.defs import DefinitionDB, TableDef
from ecu_compare.edit import EditError, apply_cell, export_edited, is_immobilizer_table
from ecu_compare.rom import LoadedRom, decode_table, load_rom
from ecu_compare.safety import ROOT, assert_inside_root, sha256_file


class EditTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "data" / "roms" / "ORIG.bin").is_file(), "sample ROM is not part of the repository")
    def test_rev_limit_roundtrip_export(self):
        orig_path = ROOT / "data" / "roms" / "ORIG.bin"
        before = sha256_file(orig_path)
        db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs.xml")
        rom = load_rom(orig_path, db)
        apply_cell(rom, "Normal Rev Limit", 0, 0, 8200.0)
        value = rom.tables["Normal Rev Limit"].data_scaled[0][0]
        self.assertAlmostEqual(value, 8200.0, delta=0.5)
        bin_path, note_path = export_edited(rom, ["Normal Rev Limit -> 8200"], "test_rev")
        assert_inside_root(bin_path)
        assert_inside_root(note_path)
        self.assertTrue(bin_path.is_relative_to(ROOT / "data" / "exports"))
        notes = note_path.read_text(encoding="utf-8")
        self.assertIn("Checksums were NOT updated", notes)
        self.assertEqual(sha256_file(orig_path), before)

    @unittest.skipUnless((ROOT / "data" / "roms" / "ORIG.bin").is_file(), "sample ROM is not part of the repository")
    def test_immobilizer_locked(self):
        self.assertTrue(is_immobilizer_table("Immobilizer Disable - With RKE Installed"))
        db = DefinitionDB(ROOT / "data" / "defs" / "rx8_defs.xml")
        rom = load_rom(ROOT / "data" / "roms" / "ORIG.bin", db)
        with self.assertRaises(EditError):
            apply_cell(rom, "Immobilizer Disable - With RKE Installed", 0, 0, 1)

    def test_out_of_range_value_is_clamped_and_reported(self):
        table = TableDef(
            name="Byte",
            table_type="1D",
            storagetype="uint8",
            endian="big",
            storageaddress=0,
            expression="x",
            to_byte="x",
            format="0",
            size=1,
        )
        decoded = decode_table(bytes([10]), table)
        rom = LoadedRom(
            path=Path("byte.bin"),
            source_hint="",
            data=bytes([10]),
            rom_def=None,
            rom_id=None,
            matching_ids=[],
            tables={"Byte": decoded},
            occupancy=list(decoded.occupied),
        )
        warning = apply_cell(rom, "Byte", 0, 0, 300)
        self.assertIsNotNone(warning)
        self.assertIn("255", warning or "")
        self.assertEqual(rom.data[0], 255)
        self.assertEqual(rom.tables["Byte"].data_scaled[0][0], 255.0)

    def test_shared_bytes_refresh_the_other_table(self):
        left = TableDef(
            name="Left",
            table_type="1D",
            storagetype="uint8",
            endian="big",
            storageaddress=0,
            expression="x",
            to_byte="x",
            format="0",
            size=1,
        )
        right = TableDef(
            name="Right",
            table_type="1D",
            storagetype="uint8",
            endian="big",
            storageaddress=0,
            expression="x",
            to_byte="x",
            format="0",
            size=1,
        )
        raw = bytes([4])
        a = decode_table(raw, left)
        b = decode_table(raw, right)
        rom = LoadedRom(
            path=Path("shared.bin"),
            source_hint="",
            data=raw,
            rom_def=None,
            rom_id=None,
            matching_ids=[],
            tables={"Left": a, "Right": b},
            occupancy=list(a.occupied) + list(b.occupied),
        )
        warning = apply_cell(rom, "Left", 0, 0, 9)
        self.assertIsNone(warning)
        self.assertEqual(rom.tables["Right"].data_scaled[0][0], 9.0)
