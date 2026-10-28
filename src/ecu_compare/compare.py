"""Compare two loaded ROMs: named tables plus raw bytes."""

from __future__ import annotations

from dataclasses import dataclass, field

from ecu_compare.rom import DecodedAxis, LoadedRom, OccupiedRange, pack_one, storage_width
from ecu_compare.scaling import format_scaled


@dataclass
class CellChange:
    table_name: str
    category: str
    units: str
    address: int
    x: int
    y: int
    x_label: str
    y_label: str
    raw_a: float | None
    raw_b: float | None
    scaled_a: float | None
    scaled_b: float | None
    error_a: str | None
    error_b: str | None
    hex_a: str
    hex_b: str
    format: str = ""
    kind: str = "data"  # data / switch / axis

    def scaled_text(self, which: str) -> str:
        if which == "a":
            text = format_scaled(self.scaled_a, self.format, self.error_a)
        else:
            text = format_scaled(self.scaled_b, self.format, self.error_b)
        if self.units and "scaling error" not in text:
            return f"{text} {self.units}".strip()
        return text

    def delta_text(self) -> str:
        if self.scaled_a is None or self.scaled_b is None:
            return ""
        delta = self.scaled_b - self.scaled_a
        sign = "+" if delta > 0 else ""
        body = format_scaled(delta, self.format)
        if self.units:
            return f"{sign}{body} {self.units}".strip()
        return f"{sign}{body}"

    def location_text(self) -> str:
        bits = []
        if self.y_label:
            bits.append(self.y_label)
        if self.x_label:
            bits.append(self.x_label)
        return ", ".join(bits)


@dataclass
class TableDiff:
    name: str
    category: str
    table_type: str
    address: int | None
    units: str
    cells: list[CellChange]
    decode_error: str | None = None
    switch_a: str | None = None
    switch_b: str | None = None
    total_cells: int = 0

    @property
    def changed_count(self) -> int:
        return len(self.cells)


def _abs_delta(cell: CellChange) -> float:
    if cell.scaled_a is None or cell.scaled_b is None:
        return 0.0
    return abs(cell.scaled_b - cell.scaled_a)


def _cell_direction(cell: CellChange) -> int:
    if cell.scaled_a is None or cell.scaled_b is None:
        return 0
    if cell.scaled_b > cell.scaled_a:
        return 1
    if cell.scaled_b < cell.scaled_a:
        return -1
    return 0


def _headline_cell(diff: TableDiff) -> CellChange | None:
    cells = [cell for cell in diff.cells if cell.kind != "axis"] or list(diff.cells)
    if not cells:
        return None
    return max(cells, key=_abs_delta)


def change_direction(diff: TableDiff) -> int:
    """+1 when the headline cell rose, -1 when it fell, 0 otherwise."""
    if (diff.table_type or "").lower() == "switch":
        return 0
    cell = _headline_cell(diff)
    if cell is None:
        return 0
    return _cell_direction(cell)


def change_blurb(diff: TableDiff) -> str:
    """One short line for the map list: before → after, or the largest step."""
    if (diff.table_type or "").lower() == "switch":
        return f"{diff.switch_a or '?'} → {diff.switch_b or '?'}"
    top = _headline_cell(diff)
    if top is None:
        return ""
    arrow = "▲ " if _cell_direction(top) > 0 else "▼ " if _cell_direction(top) < 0 else ""
    data_cells = [cell for cell in diff.cells if cell.kind != "axis"] or list(diff.cells)
    if len(data_cells) == 1:
        return f"{arrow}{top.scaled_text('a')} → {top.scaled_text('b')}"
    delta = top.delta_text()
    count = diff.changed_count or len(data_cells)
    if delta:
        return f"{arrow}{delta} · {count} cells"
    return f"{count} cells"


@dataclass
class KeyChange:
    """One headline difference: a single-value map, or the largest step in a bigger map."""

    table_name: str
    category: str
    summary: str
    y: int
    x: int
    direction: int = 0  # +1 File B is higher, -1 File B is lower


def key_changes(result: CompareResult, limit: int = 8) -> list[KeyChange]:
    """Prefer single-value changes (rev limit, temps) over huge maps of tiny steps."""
    ranked: list[tuple[int, float, KeyChange]] = []
    for diff in result.table_diffs:
        cells = [cell for cell in diff.cells if cell.kind != "axis"] or list(diff.cells)
        if not cells:
            continue
        kind = (diff.table_type or "").lower()
        single = kind in {"1d", "switch"} or diff.total_cells <= 1
        if single:
            chosen = cells
            bucket = 0
        elif diff.changed_count <= 8:
            chosen = [max(cells, key=_abs_delta)]
            bucket = 1
        else:
            chosen = [max(cells, key=_abs_delta)]
            bucket = 2
        for cell in chosen:
            if kind == "switch":
                change = f"{diff.switch_a or '?'} → {diff.switch_b or '?'}"
            else:
                change = f"{cell.scaled_text('a')} → {cell.scaled_text('b')}"
                delta = cell.delta_text()
                if delta:
                    change = f"{change}   ({delta})"
            if not single and diff.changed_count > 1:
                change = f"{change}   · {diff.changed_count} cells"
            direction = 0
            if cell.scaled_a is not None and cell.scaled_b is not None:
                if cell.scaled_b > cell.scaled_a:
                    direction = 1
                elif cell.scaled_b < cell.scaled_a:
                    direction = -1
            ranked.append(
                (
                    bucket,
                    _abs_delta(cell),
                    KeyChange(diff.name, diff.category, change, cell.y, cell.x, direction),
                )
            )
    ranked.sort(key=lambda item: (item[0], -item[1], item[2].table_name.lower()))
    seen: set[str] = set()
    out: list[KeyChange] = []
    for _bucket, _magnitude, item in ranked:
        if item.table_name in seen:
            continue
        seen.add(item.table_name)
        out.append(item)
        if len(out) >= limit:
            break
    return out


@dataclass
class UnmappedByte:
    address: int
    hex_a: str
    hex_b: str
    uint8_a: int
    uint8_b: int
    uint16_a: int | None
    uint16_b: int | None


@dataclass
class CompareResult:
    rom_a: LoadedRom
    rom_b: LoadedRom
    differing_bytes: int
    table_diffs: list[TableDiff]
    mapped_changes: list[CellChange]
    unmapped: list[UnmappedByte]
    id_mismatch: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def tables_changed(self) -> int:
        return len(self.table_diffs)

    @property
    def unmapped_count(self) -> int:
        return len(self.unmapped)


def _hex_bytes(blob: bytes) -> str:
    return blob.hex(" ").upper() if blob else ""


def _axis_label(decoded, index: int, fallback: str) -> str:
    if decoded is None:
        return ""
    if index >= len(decoded.scaled):
        return f"{fallback}={index}"
    name = decoded.name or fallback
    if name.strip().lower() in {"value", "unknown"}:
        name = decoded.units or fallback
    pretty = decoded.label(index)
    if name and name.lower() not in pretty.lower():
        return f"{name}={pretty}"
    return pretty


def _compare_table(a: LoadedRom, b: LoadedRom, name: str) -> TableDiff | None:
    ta = a.tables.get(name)
    tb = b.tables.get(name)
    if ta is None or tb is None:
        return None
    table = ta.definition
    cells: list[CellChange] = []
    if ta.decode_error or tb.decode_error:
        if ta.data_bytes != tb.data_bytes:
            return TableDiff(
                name=name,
                category=ta.category,
                table_type=table.table_type,
                address=table.storageaddress,
                units=table.units,
                cells=[],
                decode_error=ta.decode_error or tb.decode_error,
            )
        return None

    if (table.table_type or "").lower() == "switch":
        if ta.data_bytes != tb.data_bytes or ta.switch_state != tb.switch_state:
            addr = table.storageaddress or 0
            cells.append(
                CellChange(
                    table_name=name,
                    category=ta.category,
                    units="",
                    address=addr,
                    x=0,
                    y=0,
                    x_label="",
                    y_label="",
                    raw_a=None,
                    raw_b=None,
                    scaled_a=None,
                    scaled_b=None,
                    error_a=None,
                    error_b=None,
                    hex_a=_hex_bytes(ta.data_bytes),
                    hex_b=_hex_bytes(tb.data_bytes),
                    kind="switch",
                )
            )
            return TableDiff(
                name=name,
                category=ta.category,
                table_type=table.table_type,
                address=table.storageaddress,
                units="",
                cells=cells,
                switch_a=ta.switch_state,
                switch_b=tb.switch_state,
                total_cells=1,
            )
        return None

    def _axis_changes(axis_a: DecodedAxis | None, axis_b: DecodedAxis | None, kind: str) -> None:
        if axis_a is None or axis_b is None:
            return
        if axis_a.address is None:
            return
        n = min(len(axis_a.raw), len(axis_b.raw))
        width = storage_width(axis_a.storagetype)
        axis_def = ta.definition.y_axis if kind == "Y" else ta.definition.x_axis
        endian = (axis_def.endian if axis_def and axis_def.endian else None) or ta.definition.endian or "big"
        two_d = (table.table_type or "").lower() == "2d"
        for i in range(n):
            if axis_a.raw[i] == axis_b.raw[i]:
                continue
            addr = axis_a.address + i * width
            hex_a = pack_one(axis_a.raw[i], axis_a.storagetype, endian).hex(" ").upper()
            hex_b = pack_one(axis_b.raw[i], axis_b.storagetype, endian).hex(" ").upper()
            # A 3D Y breakpoint belongs to that row. A 2D axis runs along the single row.
            if kind == "Y" and not two_d:
                y, x = i, 0
            else:
                y, x = 0, i
            cells.append(
                CellChange(
                    table_name=name,
                    category=ta.category,
                    units=axis_a.units,
                    address=addr,
                    x=x,
                    y=y,
                    x_label=f"{axis_a.name or kind}[{i}]",
                    y_label="axis",
                    raw_a=axis_a.raw[i],
                    raw_b=axis_b.raw[i],
                    scaled_a=axis_a.scaled[i] if i < len(axis_a.scaled) else None,
                    scaled_b=axis_b.scaled[i] if i < len(axis_b.scaled) else None,
                    error_a=axis_a.errors[i] if i < len(axis_a.errors) else None,
                    error_b=axis_b.errors[i] if i < len(axis_b.errors) else None,
                    hex_a=hex_a,
                    hex_b=hex_b,
                    format=axis_a.format,
                    kind="axis",
                )
            )

    _axis_changes(ta.x_axis, tb.x_axis, "X")
    _axis_changes(ta.y_axis, tb.y_axis, "Y")

    rows = min(ta.rows(), tb.rows())
    cols = min(ta.cols(), tb.cols())
    total = 0
    for y in range(rows):
        for x in range(cols):
            total += 1
            raw_a = ta.data_raw[y][x]
            raw_b = tb.data_raw[y][x]
            if raw_a == raw_b:
                continue
            addr = ta.cell_address(y, x) or 0
            hex_a = _hex_bytes(ta.cell_raw_bytes(a.data, y, x))
            hex_b = _hex_bytes(tb.cell_raw_bytes(b.data, y, x))
            x_label = _axis_label(ta.x_axis, x, "X") if ta.x_axis else ""
            y_label = _axis_label(ta.y_axis, y, "Y") if ta.y_axis else ""
            if (table.table_type or "").lower() == "2d":
                y_label = _axis_label(ta.y_axis, x if ta.cols() > 1 else y, "Y")
                x_label = ""
            cells.append(
                CellChange(
                    table_name=name,
                    category=ta.category,
                    units=table.units,
                    address=addr,
                    x=x,
                    y=y,
                    x_label=x_label,
                    y_label=y_label,
                    raw_a=raw_a,
                    raw_b=raw_b,
                    scaled_a=ta.data_scaled[y][x],
                    scaled_b=tb.data_scaled[y][x],
                    error_a=ta.data_errors[y][x],
                    error_b=tb.data_errors[y][x],
                    hex_a=hex_a,
                    hex_b=hex_b,
                    format=table.format,
                    kind="data",
                )
            )
    if not cells:
        return None
    return TableDiff(
        name=name,
        category=ta.category,
        table_type=table.table_type,
        address=table.storageaddress,
        units=table.units,
        cells=cells,
        total_cells=total,
    )


def _covered_bytes(ranges: list[OccupiedRange]) -> set[int]:
    covered: set[int] = set()
    for item in ranges:
        if item.kind not in ("data", "switch", "x-axis", "y-axis"):
            continue
        covered.update(range(item.start, item.end))
    return covered


def _uint16_be(data: bytes, addr: int) -> int | None:
    if addr % 2 != 0 or addr + 2 > len(data):
        return None
    return (data[addr] << 8) | data[addr + 1]


def compare_roms(rom_a: LoadedRom, rom_b: LoadedRom) -> CompareResult:
    warnings: list[str] = []
    if len(rom_a.data) != len(rom_b.data):
        warnings.append(
            f"File sizes differ ({len(rom_a.data)} vs {len(rom_b.data)}). "
            "Byte compare uses the shorter length."
        )
    id_mismatch = False
    if rom_a.cal_id() and rom_b.cal_id() and rom_a.cal_id() != rom_b.cal_id():
        id_mismatch = True
        warnings.append(
            f"CAL IDs differ ({rom_a.cal_id()} vs {rom_b.cal_id()}). "
            "Table names still apply per file, but layouts may not match."
        )
    if rom_a.rom_def is None or rom_b.rom_def is None:
        warnings.append("One or both files did not match rx8_defs.xml; named tables may be missing.")

    n = min(len(rom_a.data), len(rom_b.data))
    differing = 0
    diff_addrs: list[int] = []
    for i in range(n):
        if rom_a.data[i] != rom_b.data[i]:
            differing += 1
            diff_addrs.append(i)

    names = []
    seen = set()
    for source in (rom_a.tables, rom_b.tables):
        for name in source:
            if name not in seen:
                seen.add(name)
                names.append(name)

    table_diffs: list[TableDiff] = []
    mapped_changes: list[CellChange] = []
    mapped_bytes: set[int] = set()
    for name in names:
        diff = _compare_table(rom_a, rom_b, name)
        if diff is None:
            continue
        table_diffs.append(diff)
        mapped_changes.extend(diff.cells)
        for cell in diff.cells:
            width = max(len(bytes.fromhex(cell.hex_a.replace(" ", "") or "00")), 1)
            if cell.kind == "switch":
                width = max(len(cell.hex_a.split()), 1)
            mapped_bytes.update(range(cell.address, cell.address + width))

    # Also treat occupancy of unchanged mapped regions so leftover diffs are truly unmapped
    covered = _covered_bytes(rom_a.occupancy) | _covered_bytes(rom_b.occupancy)

    unmapped: list[UnmappedByte] = []
    for addr in diff_addrs:
        if addr in covered:
            continue
        ba = rom_a.data[addr]
        bb = rom_b.data[addr]
        unmapped.append(
            UnmappedByte(
                address=addr,
                hex_a=f"{ba:02X}",
                hex_b=f"{bb:02X}",
                uint8_a=ba,
                uint8_b=bb,
                uint16_a=_uint16_be(rom_a.data, addr),
                uint16_b=_uint16_be(rom_b.data, addr),
            )
        )

    table_diffs.sort(key=lambda t: (t.category.lower(), t.name.lower()))
    return CompareResult(
        rom_a=rom_a,
        rom_b=rom_b,
        differing_bytes=differing,
        table_diffs=table_diffs,
        mapped_changes=mapped_changes,
        unmapped=unmapped,
        id_mismatch=id_mismatch,
        warnings=warnings,
    )
