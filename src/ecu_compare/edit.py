"""Gated map editing. Saves only under data/exports. Never flashes. No checksums."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ecu_compare.rom import LoadedRom, decode_table, pack_one, table_scaling
from ecu_compare.safety import ROOT, assert_inside_root, safe_filename, write_bytes, write_text
from ecu_compare.scaling import ScalingError, format_scaled

IMMOBILIZER_PREFIX = "immobilizer"
CHECKSUM_WARNING = (
    "Checksums were NOT updated. This definition file does not describe RX-8 checksums. "
    "Do not flash this file until checksums are verified by another tool you trust."
)
DANGER_WARNING = (
    "Map editing can produce a file that will not run, or that can damage an engine. "
    "This program does not flash a car. Files outside this program's folder are never written."
)


class EditError(ValueError):
    pass


def is_immobilizer_table(name: str) -> bool:
    return "immobilizer" in (name or "").lower()


def replace_bytes(rom: LoadedRom, addr: int, payload: bytes) -> None:
    """Write bytes inside the file and decode again every table that covers them."""
    end = addr + len(payload)
    if addr < 0 or end > len(rom.data):
        raise EditError("That edit sits outside the file.")
    data = bytearray(rom.data)
    data[addr:end] = payload
    rom.data = bytes(data)
    touched = {
        item.table_name
        for item in rom.occupancy
        if item.start < end and item.end > addr and item.table_name in rom.tables
    }
    for name in touched:
        rom.tables[name] = decode_table(rom.data, rom.tables[name].definition)
    rom.occupancy = [span for decoded in rom.tables.values() for span in decoded.occupied]


def encode_scaled_cell(rom: LoadedRom, table_name: str, y: int, x: int, scaled: float) -> bytes:
    table = rom.tables.get(table_name)
    if table is None:
        raise EditError(f"Unknown table: {table_name}")
    if is_immobilizer_table(table_name):
        raise EditError("Immobilizer tables cannot be edited in this program.")
    if (table.definition.table_type or "").lower() == "switch":
        raise EditError("Switch tables cannot be edited in this program.")
    scaling = table_scaling(table.definition)
    try:
        raw = scaling.from_display(scaled)
    except (ScalingError, SyntaxError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise EditError(f"Could not convert value: {exc}") from exc
    return pack_one(raw, table.definition.storagetype, table.definition.endian)


def apply_cell(rom: LoadedRom, table_name: str, y: int, x: int, scaled: float) -> str | None:
    """Write one scaled cell. Returns a note when the stored number differs from what was typed."""
    table = rom.tables.get(table_name)
    if table is None:
        raise EditError(f"Unknown table: {table_name}")
    addr = table.cell_address(y, x)
    if addr is None:
        raise EditError("Table has no storage address")
    payload = encode_scaled_cell(rom, table_name, y, x, scaled)
    replace_bytes(rom, addr, payload)
    stored = rom.tables[table_name].data_scaled[y][x] if rom.tables[table_name].data_scaled else None
    if stored is None:
        return "The bytes were written, but the cell does not scale back to a number."
    fmt = table.definition.format
    entered = format_scaled(scaled, fmt)
    kept = format_scaled(stored, fmt)
    if entered != kept:
        return f"Entered {entered}. This cell stores {kept}."
    return None


def export_edited(rom: LoadedRom, notes: list[str], suggested_name: str | None = None) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(suggested_name or f"{rom.path.stem}_edited")
    bin_path = ROOT / "data" / "exports" / f"{stamp}_{base}.bin"
    note_path = ROOT / "data" / "exports" / f"{stamp}_{base}.notes.txt"
    assert_inside_root(bin_path)
    assert_inside_root(note_path)
    write_bytes(bin_path, rom.data)
    body = [
        "RX-8 ECU edited copy",
        f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Source working copy: {rom.path}",
        f"CAL ID: {rom.cal_id()}",
        "",
        DANGER_WARNING,
        CHECKSUM_WARNING,
        "",
        "Changes:",
    ]
    if notes:
        body.extend(f"  - {line}" for line in notes)
    else:
        body.append("  (none recorded)")
    body.append("")
    write_text(note_path, "\n".join(body) + "\n")
    return bin_path, note_path
