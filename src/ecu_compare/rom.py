"""Load an ECU binary and decode tables using a RomRaider definition."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ecu_compare.defs import AxisDef, DefinitionDB, RomDef, RomId, TableDef
from ecu_compare.scaling import Scaling, format_scaled

STORAGE_WIDTH = {
    "uint8": 1,
    "int8": 1,
    "uint16": 2,
    "int16": 2,
    "uint32": 4,
    "int32": 4,
    "float": 4,
}

_STRUCT = {
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "float": "f",
}

INT_RANGES = {
    "uint8": (0, 255),
    "int8": (-128, 127),
    "uint16": (0, 65535),
    "int16": (-32768, 32767),
    "uint32": (0, 2**32 - 1),
    "int32": (-(2**31), 2**31 - 1),
}


def storage_width(storagetype: str | None) -> int:
    return STORAGE_WIDTH.get((storagetype or "uint8").lower(), 1)


def _endian_prefix(endian: str) -> str:
    return "<" if (endian or "big").lower().startswith("little") else ">"


def unpack_one(data: bytes, offset: int, storagetype: str, endian: str) -> float:
    kind = (storagetype or "uint8").lower()
    width = storage_width(kind)
    if offset < 0 or offset + width > len(data):
        raise ValueError(f"Address 0x{offset:X} is outside the file")
    fmt = _endian_prefix(endian) + _STRUCT.get(kind, "B")
    value = struct.unpack_from(fmt, data, offset)[0]
    return float(value)


def pack_one(value: float, storagetype: str, endian: str) -> bytes:
    kind = (storagetype or "uint8").lower()
    fmt = _endian_prefix(endian) + _STRUCT.get(kind, "B")
    if kind == "float":
        return struct.pack(fmt, float(value))
    lo, hi = INT_RANGES[kind]
    rounded = int(round(value))
    rounded = max(lo, min(hi, rounded))
    return struct.pack(fmt, rounded)


def read_series(data: bytes, address: int, count: int, storagetype: str, endian: str) -> tuple[list[float], bytes]:
    width = storage_width(storagetype)
    values: list[float] = []
    start = address
    end = address + width * count
    raw = data[start:end]
    for i in range(count):
        values.append(unpack_one(data, address + i * width, storagetype, endian))
    return values, raw


def table_scaling(table: TableDef) -> Scaling:
    return Scaling(
        expression=table.expression or "x",
        to_byte=table.to_byte or "x",
        format=table.format,
        units=table.units,
    )


def axis_scaling(axis: AxisDef) -> Scaling:
    return Scaling(
        expression=axis.expression or "x",
        to_byte=axis.to_byte or "x",
        format=axis.format,
        units=axis.units,
    )


def _stype(value: str | None, default: str) -> str:
    return (value or default).lower()


def _endian(value: str | None) -> str:
    return value or "big"


@dataclass
class OccupiedRange:
    start: int
    end: int  # exclusive
    table_name: str
    kind: str  # data / x-axis / y-axis / switch
    cell_index: int | None = None
    x: int | None = None
    y: int | None = None


@dataclass
class DecodedAxis:
    name: str
    units: str
    raw: list[float]
    scaled: list[float | None]
    errors: list[str | None]
    address: int | None
    storagetype: str
    format: str = ""

    def label(self, index: int) -> str:
        if index < 0 or index >= len(self.scaled):
            return str(index)
        value = self.scaled[index]
        err = self.errors[index] if index < len(self.errors) else None
        text = format_scaled(value, self.format, err)
        if self.units and not err:
            return f"{text} {self.units}"
        return text


@dataclass
class DecodedTable:
    definition: TableDef
    data_raw: list[list[float]]
    data_scaled: list[list[float | None]]
    data_errors: list[list[str | None]]
    data_bytes: bytes
    x_axis: DecodedAxis | None = None
    y_axis: DecodedAxis | None = None
    switch_state: str | None = None
    decode_error: str | None = None
    occupied: list[OccupiedRange] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def category(self) -> str:
        return self.definition.category or "Uncategorized"

    @property
    def units(self) -> str:
        return self.definition.units

    def rows(self) -> int:
        return len(self.data_raw)

    def cols(self) -> int:
        return len(self.data_raw[0]) if self.data_raw else 0

    def cell_address(self, y: int, x: int) -> int | None:
        addr = self.definition.storageaddress
        if addr is None:
            return None
        width = storage_width(self.definition.storagetype)
        cols = max(self.cols(), 1)
        return addr + (y * cols + x) * width

    def cell_raw_bytes(self, data: bytes, y: int, x: int) -> bytes:
        addr = self.cell_address(y, x)
        if addr is None:
            return b""
        width = storage_width(self.definition.storagetype)
        return data[addr : addr + width]


def _decode_axis(data: bytes, axis: AxisDef | None, count: int) -> DecodedAxis | None:
    if axis is None or count <= 0:
        return None
    scaling = axis_scaling(axis)
    raw: list[float] = []
    scaled: list[float | None] = []
    errors: list[str | None] = []
    if axis.storageaddress is None:
        return DecodedAxis(
            name=axis.name or axis.axis_type,
            units=axis.units,
            raw=[float(i) for i in range(count)],
            scaled=[float(i) for i in range(count)],
            errors=[None] * count,
            address=None,
            storagetype=axis.storagetype or "float",
            format=axis.format,
        )
    try:
        raw, _blob = read_series(
            data, axis.storageaddress, count, axis.storagetype or "float", axis.endian or "big"
        )
    except (ValueError, struct.error) as exc:
        return DecodedAxis(
            name=axis.name or axis.axis_type,
            units=axis.units,
            raw=[],
            scaled=[],
            errors=[str(exc)],
            address=axis.storageaddress,
            storagetype=axis.storagetype or "float",
            format=axis.format,
        )
    for value in raw:
        s, err = scaling.to_display(value)
        scaled.append(s)
        errors.append(err)
    return DecodedAxis(
        name=axis.name or axis.axis_type,
        units=axis.units,
        raw=raw,
        scaled=scaled,
        errors=errors,
        address=axis.storageaddress,
        storagetype=axis.storagetype or "float",
        format=axis.format,
    )


def _match_switch(blob: bytes, table: TableDef) -> str | None:
    for state in table.states:
        if state.data and blob[: len(state.data)] == state.data:
            return state.name
    return None


def decode_table(data: bytes, table: TableDef) -> DecodedTable:
    occupied: list[OccupiedRange] = []
    kind = (table.table_type or "").lower()
    scaling = table_scaling(table)

    if kind == "switch":
        addr = table.storageaddress
        length = 0
        if table.states:
            length = max(len(s.data) for s in table.states)
        if table.sizey:
            length = max(length, table.sizey)
        if addr is None or length <= 0:
            return DecodedTable(
                definition=table,
                data_raw=[],
                data_scaled=[],
                data_errors=[],
                data_bytes=b"",
                decode_error="Switch has no address or data",
            )
        blob = data[addr : addr + length]
        occupied.append(OccupiedRange(addr, addr + length, table.name, "switch"))
        state = _match_switch(blob, table)
        return DecodedTable(
            definition=table,
            data_raw=[[float(b) for b in blob]],
            data_scaled=[[float(b) for b in blob]],
            data_errors=[[None for _ in blob]],
            data_bytes=blob,
            switch_state=state or "unknown",
            occupied=occupied,
        )

    if table.storageaddress is None:
        return DecodedTable(
            definition=table,
            data_raw=[],
            data_scaled=[],
            data_errors=[],
            data_bytes=b"",
            decode_error="No storage address",
        )

    try:
        if kind == "3d":
            nx = table.sizex or 0
            ny = table.sizey or 0
            if nx <= 0 or ny <= 0:
                raise ValueError("3D table missing sizex/sizey")
            values, blob = read_series(
                data, table.storageaddress, nx * ny, table.storagetype or "uint8", table.endian or "big"
            )
            grid_raw: list[list[float]] = []
            grid_scaled: list[list[float | None]] = []
            grid_err: list[list[str | None]] = []
            for y in range(ny):
                row_raw: list[float] = []
                row_s: list[float | None] = []
                row_e: list[str | None] = []
                for x in range(nx):
                    raw = values[y * nx + x]
                    s, err = scaling.to_display(raw)
                    row_raw.append(raw)
                    row_s.append(s)
                    row_e.append(err)
                    width = storage_width(table.storagetype)
                    start = table.storageaddress + (y * nx + x) * width
                    occupied.append(
                        OccupiedRange(start, start + width, table.name, "data", y * nx + x, x, y)
                    )
                grid_raw.append(row_raw)
                grid_scaled.append(row_s)
                grid_err.append(row_e)
            x_axis = _decode_axis(data, table.x_axis, nx)
            y_axis = _decode_axis(data, table.y_axis, ny)
            if x_axis and table.x_axis and table.x_axis.storageaddress is not None:
                width = storage_width(table.x_axis.storagetype or "float")
                occupied.append(
                    OccupiedRange(
                        table.x_axis.storageaddress,
                        table.x_axis.storageaddress + width * nx,
                        table.name,
                        "x-axis",
                    )
                )
            if y_axis and table.y_axis and table.y_axis.storageaddress is not None:
                width = storage_width(table.y_axis.storagetype or "float")
                occupied.append(
                    OccupiedRange(
                        table.y_axis.storageaddress,
                        table.y_axis.storageaddress + width * ny,
                        table.name,
                        "y-axis",
                    )
                )
            return DecodedTable(
                definition=table,
                data_raw=grid_raw,
                data_scaled=grid_scaled,
                data_errors=grid_err,
                data_bytes=blob,
                x_axis=x_axis,
                y_axis=y_axis,
                occupied=occupied,
            )

        if kind == "2d":
            n = table.length_2d()
            if n <= 0:
                raise ValueError("2D table missing size")
            values, blob = read_series(
                data, table.storageaddress, n, table.storagetype or "uint8", table.endian or "big"
            )
            row_s: list[float | None] = []
            row_e: list[str | None] = []
            for i, raw in enumerate(values):
                s, err = scaling.to_display(raw)
                row_s.append(s)
                row_e.append(err)
                width = storage_width(table.storagetype)
                start = table.storageaddress + i * width
                occupied.append(OccupiedRange(start, start + width, table.name, "data", i, 0, i))
            y_axis = _decode_axis(data, table.y_axis or table.x_axis, n)
            axis_src = table.y_axis or table.x_axis
            if y_axis and axis_src and axis_src.storageaddress is not None:
                width = storage_width(axis_src.storagetype or "float")
                occupied.append(
                    OccupiedRange(
                        axis_src.storageaddress,
                        axis_src.storageaddress + width * n,
                        table.name,
                        "y-axis",
                    )
                )
            return DecodedTable(
                definition=table,
                data_raw=[values],
                data_scaled=[row_s],
                data_errors=[row_e],
                data_bytes=blob,
                y_axis=y_axis,
                occupied=occupied,
            )

        # 1D and anything else treated as a short vector
        n = table.element_count_1d()
        values, blob = read_series(
            data, table.storageaddress, n, table.storagetype or "uint8", table.endian or "big"
        )
        row_s = []
        row_e = []
        for i, raw in enumerate(values):
            s, err = scaling.to_display(raw)
            row_s.append(s)
            row_e.append(err)
            width = storage_width(table.storagetype)
            start = table.storageaddress + i * width
            occupied.append(OccupiedRange(start, start + width, table.name, "data", i, i, 0))
        return DecodedTable(
            definition=table,
            data_raw=[values],
            data_scaled=[row_s],
            data_errors=[row_e],
            data_bytes=blob,
            occupied=occupied,
        )
    except (ValueError, struct.error) as exc:
        return DecodedTable(
            definition=table,
            data_raw=[],
            data_scaled=[],
            data_errors=[],
            data_bytes=b"",
            decode_error=str(exc),
            occupied=occupied,
        )


@dataclass
class LoadedRom:
    path: Path
    source_hint: str
    data: bytes
    rom_def: RomDef | None
    rom_id: RomId | None
    matching_ids: list[str]
    tables: dict[str, DecodedTable]
    occupancy: list[OccupiedRange]

    @property
    def size(self) -> int:
        return len(self.data)

    def cal_id(self) -> str:
        if len(self.data) >= 0x2008:
            try:
                return self.data[0x2000:0x2008].decode("ascii").strip("\x00 ")
            except UnicodeDecodeError:
                return ""
        return ""

    def identity_line(self) -> str:
        cal = self.cal_id()
        if self.rom_id:
            return f"{self.rom_id.label()}  CAL {cal}"
        if cal:
            return f"unidentified  CAL {cal}"
        return "unidentified"


def occupancy_index(ranges: list[OccupiedRange]) -> list[OccupiedRange | None]:
    """Sparse-friendly: we just keep the list; lookup is linear-range scan."""
    return ranges


def lookup_occupancy(ranges: list[OccupiedRange], addr: int) -> OccupiedRange | None:
    for item in ranges:
        if item.start <= addr < item.end:
            return item
    return None


def load_rom(path: Path, db: DefinitionDB, source_hint: str = "") -> LoadedRom:
    data = Path(path).read_bytes()
    rom_def, rom_id, matching = db.identify(data)
    tables: dict[str, DecodedTable] = {}
    occupied: list[OccupiedRange] = []
    if rom_def:
        for name, table in rom_def.tables.items():
            decoded = decode_table(data, table)
            tables[name] = decoded
            occupied.extend(decoded.occupied)
    return LoadedRom(
        path=Path(path),
        source_hint=source_hint,
        data=data,
        rom_def=rom_def,
        rom_id=rom_id,
        matching_ids=matching,
        tables=tables,
        occupancy=occupied,
    )
