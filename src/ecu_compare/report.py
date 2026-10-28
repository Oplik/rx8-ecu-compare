"""Write a human-readable compare report (named tables + extended listing)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ecu_compare.compare import CompareResult, TableDiff
from ecu_compare.safety import ROOT, assert_inside_root, safe_filename, write_text


def _file_block(label: str, rom) -> list[str]:
    ident = rom.rom_id.label() if rom.rom_id else "unidentified"
    lines = [
        f"{label}: {rom.path.name}",
        f"  Path (working copy): {rom.path}",
    ]
    if rom.source_hint:
        lines.append(f"  Imported from: {rom.source_hint}  (original was copied, never written)")
    lines.extend(
        [
            f"  Size: {rom.size} bytes",
            f"  CAL ID @ 0x2000: {rom.cal_id() or 'n/a'}",
            f"  Definition: {ident}",
            f"  Matching IDs: {', '.join(rom.matching_ids) or 'none'}",
        ]
    )
    return lines


def _fmt_addr(addr: int) -> str:
    return f"{addr:08X}"


def _named_table_section(diff: TableDiff) -> list[str]:
    lines = [
        f"[{diff.category}] {diff.name}",
        f"  Address: 0x{_fmt_addr(diff.address or 0)}  Type: {diff.table_type or '?'}  Units: {diff.units or '-'}",
    ]
    if diff.decode_error:
        lines.append(f"  Decode error: {diff.decode_error}")
        return lines
    if diff.switch_a or diff.switch_b:
        cell = diff.cells[0] if diff.cells else None
        lines.append(f"  State A: {diff.switch_a or '?'}")
        lines.append(f"  State B: {diff.switch_b or '?'}")
        if cell:
            lines.append(f"  Hex A: {cell.hex_a}")
            lines.append(f"  Hex B: {cell.hex_b}")
        return lines
    lines.append(f"  Changed cells: {diff.changed_count} / {diff.total_cells or diff.changed_count}")
    for cell in diff.cells:
        loc = cell.location_text()
        loc_bit = f"  {loc}: " if loc else "  "
        delta = cell.delta_text()
        delta_bit = f"  (Δ {delta})" if delta else ""
        lines.append(
            f"{loc_bit}{cell.scaled_text('a')}  ->  {cell.scaled_text('b')}{delta_bit}"
        )
        lines.append(
            f"    Address 0x{_fmt_addr(cell.address)}  hex {cell.hex_a} -> {cell.hex_b}"
        )
    return lines


def _extended_mapped(result: CompareResult) -> list[str]:
    lines = [
        "Address     Hex A            Hex B            Converted A              Converted B              Table / cell",
        "-" * 130,
    ]
    # One row per changed named cell (full multi-byte cell, not per byte)
    rows = sorted(result.mapped_changes, key=lambda c: (c.address, c.table_name))
    if not rows:
        lines.append("(no named-table cell changes)")
        return lines
    switch_states = {t.name: (t.switch_a or "unknown", t.switch_b or "unknown") for t in result.table_diffs}
    for cell in rows:
        if cell.kind == "switch":
            conv_a, conv_b = switch_states.get(cell.table_name, ("unknown", "unknown"))
        else:
            conv_a = cell.scaled_text("a")
            conv_b = cell.scaled_text("b")
        loc = cell.location_text()
        where = f"[{cell.category}] {cell.table_name}"
        if loc:
            where = f"{where}  {loc}"
        lines.append(
            f"{_fmt_addr(cell.address):<11} {cell.hex_a:<16} {cell.hex_b:<16} {conv_a:<24} {conv_b:<24} {where}"
        )
    return lines


def _extended_unmapped(result: CompareResult) -> list[str]:
    lines = [
        "Address     Hex A  Hex B  uint8 A -> B           uint16 BE A -> B (even addresses only)",
        "-" * 100,
    ]
    if not result.unmapped:
        lines.append("(no unmapped differing bytes)")
        return lines
    for item in result.unmapped:
        u8 = f"{item.uint8_a} -> {item.uint8_b}"
        if item.uint16_a is None or item.uint16_b is None:
            u16 = "-"
        else:
            u16 = f"{item.uint16_a} -> {item.uint16_b}"
        lines.append(
            f"{_fmt_addr(item.address):<11} {item.hex_a:<6} {item.hex_b:<6} {u8:<22} {u16}"
        )
    return lines


def render_report(result: CompareResult) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    size = min(result.rom_a.size, result.rom_b.size) or 1
    pct = 100.0 * result.differing_bytes / size
    lines: list[str] = [
        "Mazda RX-8 ECU Compare Report",
        f"Generated: {now}",
        "",
        *_file_block("File A", result.rom_a),
        "",
        *_file_block("File B", result.rom_b),
        "",
        "Summary",
        f"  Differing bytes: {result.differing_bytes} / {size} ({pct:.4f}%)",
        f"  Named tables with changes: {result.tables_changed}",
        f"  Named cells with changes: {len(result.mapped_changes)}",
        f"  Unmapped differing bytes: {result.unmapped_count}",
    ]
    if result.warnings:
        lines.append("  Warnings:")
        for warning in result.warnings:
            lines.append(f"    - {warning}")
    lines.extend(
        [
            "",
            "=" * 72,
            "NAMED TABLE CHANGES",
            "=" * 72,
            "",
        ]
    )
    if not result.table_diffs:
        lines.append("(no named table differences)")
    else:
        for diff in result.table_diffs:
            lines.extend(_named_table_section(diff))
            lines.append("")
    lines.extend(
        [
            "=" * 72,
            "EXTENDED LISTING (address / hex / converted)",
            "=" * 72,
            "",
            "Named-table cells (full cell hex + scaled units). Multi-byte values are one row.",
            "",
            *_extended_mapped(result),
            "",
            "Unmapped bytes (not in definition — no table scaling).",
            "Converted here as uint8 and, on even addresses, uint16 big-endian.",
            "",
            *_extended_unmapped(result),
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def default_report_path(result: CompareResult) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    a = safe_filename(result.rom_a.path.stem)
    b = safe_filename(result.rom_b.path.stem)
    return ROOT / "data" / "reports" / f"{stamp}_{a}_vs_{b}.txt"


def write_report(result: CompareResult, dest: Path | None = None) -> Path:
    path = dest or default_report_path(result)
    assert_inside_root(path)
    return write_text(path, render_report(result))
