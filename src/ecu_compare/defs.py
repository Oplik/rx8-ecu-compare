"""Parse RomRaider RX-8 ECU definition XML and match a binary to a ROM ID."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


def parse_addr(value: str | None) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 16)


@dataclass
class RomId:
    xmlid: str = ""
    internalidaddress: int | None = None
    internalidstring: str = ""
    make: str = ""
    model: str = ""
    submodel: str = ""
    market: str = ""
    year: str = ""
    transmission: str = ""
    memmodel: str = ""
    filesize: str = ""
    ecuid: str = ""

    def label(self) -> str:
        bits = [self.xmlid]
        extra = " ".join(p for p in (self.market, self.year, self.submodel, self.transmission) if p)
        if extra:
            bits.append(f"({extra})")
        return " ".join(bits)


@dataclass
class AxisDef:
    name: str = ""
    axis_type: str = ""  # X Axis / Y Axis
    storagetype: str | None = None
    endian: str | None = None
    storageaddress: int | None = None
    expression: str | None = None
    to_byte: str | None = None
    format: str = ""
    units: str = ""


@dataclass
class SwitchState:
    name: str = ""
    data: bytes = b""


@dataclass
class TableDef:
    name: str = ""
    category: str = ""
    table_type: str = ""  # 1D / 2D / 3D / Switch
    storagetype: str | None = None
    endian: str | None = None
    storageaddress: int | None = None
    sizex: int | None = None
    sizey: int | None = None
    size: int | None = None
    expression: str | None = None
    to_byte: str | None = None
    format: str = ""
    units: str = ""
    description: str = ""
    beforeram: bool = False
    x_axis: AxisDef | None = None
    y_axis: AxisDef | None = None
    states: list[SwitchState] = field(default_factory=list)

    def element_count_1d(self) -> int:
        if self.sizey and self.sizey > 1:
            return self.sizey
        if self.sizex and self.sizex > 1:
            return self.sizex
        if self.size and self.size > 4:
            return self.size
        return 1

    def length_2d(self) -> int:
        return self.sizey or self.sizex or self.size or 0


@dataclass
class RomDef:
    xmlids: list[str]
    romids: list[RomId]
    tables: dict[str, TableDef]
    base_name: str | None = None

    def best_romid(self) -> RomId:
        if not self.romids:
            return RomId()
        scored = sorted(
            self.romids,
            key=lambda r: (
                0 if (r.internalidaddress or 0) == 0x2000 else 1,
                len(r.internalidstring or ""),
                1 if r.submodel or r.market else 0,
            ),
            reverse=True,
        )
        return scored[0]


def _attr(elem: ET.Element, *names: str, default: str = "") -> str:
    for name in names:
        if name in elem.attrib and elem.attrib[name] not in (None, ""):
            return str(elem.attrib[name])
        lower = {k.lower(): k for k in elem.attrib}
        if name.lower() in lower:
            return str(elem.attrib[lower[name.lower()]])
    return default


def _child_text(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_hex_bytes(text: str) -> bytes:
    parts = text.replace("\n", " ").replace("\t", " ").split()
    out = bytearray()
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if token.lower().startswith("0x"):
            token = token[2:]
        out.append(int(token, 16))
    return bytes(out)


def _parse_scaling(elem: ET.Element) -> tuple[str | None, str | None, str, str]:
    expression = _attr(elem, "expression") or None
    to_byte = _attr(elem, "to_byte") or None
    fmt = _attr(elem, "format", default="")
    units = _attr(elem, "units", default="")
    scaling = elem.find("scaling")
    if scaling is not None:
        expression = _attr(scaling, "expression") or expression
        to_byte = _attr(scaling, "to_byte") or to_byte
        fmt = _attr(scaling, "format", default=fmt)
        units = _attr(scaling, "units", default=units)
    return expression, to_byte, fmt, units


def _parse_axis(elem: ET.Element) -> AxisDef:
    expression, to_byte, fmt, units = _parse_scaling(elem)
    return AxisDef(
        name=_attr(elem, "name"),
        axis_type=_attr(elem, "type"),
        storagetype=_attr(elem, "storagetype") or None,
        endian=_attr(elem, "endian") or None,
        storageaddress=parse_addr(_attr(elem, "storageaddress") or None),
        expression=expression,
        to_byte=to_byte,
        format=fmt,
        units=units,
    )


def _parse_table(elem: ET.Element) -> TableDef:
    expression, to_byte, fmt, units = _parse_scaling(elem)
    units = _attr(elem, "units", default=units)
    table_type = _attr(elem, "type")
    desc_el = elem.find("description")
    description = (desc_el.text or "").strip() if desc_el is not None else ""
    table = TableDef(
        name=_attr(elem, "name"),
        category=_attr(elem, "category"),
        table_type=table_type,
        storagetype=_attr(elem, "storagetype") or None,
        endian=_attr(elem, "endian") or None,
        storageaddress=parse_addr(_attr(elem, "storageaddress") or None),
        sizex=int(_attr(elem, "sizex"), 10) if _attr(elem, "sizex") else None,
        sizey=int(_attr(elem, "sizey"), 10) if _attr(elem, "sizey") else None,
        size=int(_attr(elem, "size"), 10) if _attr(elem, "size") else None,
        expression=expression,
        to_byte=to_byte,
        format=fmt,
        units=units,
        description=description,
        beforeram=_attr(elem, "beforeram").lower() == "true",
    )
    for child in list(elem):
        if child.tag.lower() != "table":
            continue
        axis = _parse_axis(child)
        kind = (axis.axis_type or "").lower()
        if "x axis" in kind:
            table.x_axis = axis
        elif "y axis" in kind:
            table.y_axis = axis
    for state_el in elem.findall("state"):
        table.states.append(
            SwitchState(
                name=_attr(state_el, "name"),
                data=_parse_hex_bytes(_attr(state_el, "data")),
            )
        )
    return table


def _parse_romid(elem: ET.Element) -> RomId:
    return RomId(
        xmlid=_child_text(elem, "xmlid"),
        internalidaddress=parse_addr(_child_text(elem, "internalidaddress") or None),
        internalidstring=_child_text(elem, "internalidstring"),
        make=_child_text(elem, "make"),
        model=_child_text(elem, "model"),
        submodel=_child_text(elem, "submodel"),
        market=_child_text(elem, "market"),
        year=_child_text(elem, "year"),
        transmission=_child_text(elem, "transmission"),
        memmodel=_child_text(elem, "memmodel"),
        filesize=_child_text(elem, "filesize"),
        ecuid=_child_text(elem, "ecuid"),
    )


def _merge_axis(base: AxisDef | None, override: AxisDef | None) -> AxisDef | None:
    if override is None:
        return copy.deepcopy(base) if base else None
    if base is None:
        return copy.deepcopy(override)
    merged = copy.deepcopy(base)
    if override.name:
        merged.name = override.name
    if override.axis_type:
        merged.axis_type = override.axis_type
    if override.storagetype:
        merged.storagetype = override.storagetype
    if override.endian:
        merged.endian = override.endian
    if override.storageaddress is not None:
        merged.storageaddress = override.storageaddress
    if override.expression:
        merged.expression = override.expression
    if override.to_byte:
        merged.to_byte = override.to_byte
    if override.format:
        merged.format = override.format
    if override.units:
        merged.units = override.units
    return merged


def merge_table(base: TableDef, override: TableDef) -> TableDef:
    merged = copy.deepcopy(base)
    if override.category:
        merged.category = override.category
    if override.table_type:
        merged.table_type = override.table_type
    if override.storagetype:
        merged.storagetype = override.storagetype
    if override.endian:
        merged.endian = override.endian
    if override.storageaddress is not None:
        merged.storageaddress = override.storageaddress
    if override.sizex is not None:
        merged.sizex = override.sizex
    if override.sizey is not None:
        merged.sizey = override.sizey
    if override.size is not None:
        merged.size = override.size
    if override.expression:
        merged.expression = override.expression
    if override.to_byte:
        merged.to_byte = override.to_byte
    if override.format:
        merged.format = override.format
    if override.units:
        merged.units = override.units
    if override.description:
        merged.description = override.description
    if override.beforeram:
        merged.beforeram = True
    if override.states:
        merged.states = copy.deepcopy(override.states)
    merged.x_axis = _merge_axis(merged.x_axis, override.x_axis)
    merged.y_axis = _merge_axis(merged.y_axis, override.y_axis)
    return merged


def _parse_rom_element(elem: ET.Element) -> tuple[str | None, list[RomId], dict[str, TableDef]]:
    base = _attr(elem, "base") or None
    romids: list[RomId] = []
    tables: dict[str, TableDef] = {}
    for child in list(elem):
        tag = child.tag.lower()
        if tag == "romid":
            romids.append(_parse_romid(child))
        elif tag == "table":
            table = _parse_table(child)
            if table.name:
                tables[table.name] = table
    return base, romids, tables


class DefinitionDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.roms: list[RomDef] = []
        self._by_xmlid: dict[str, RomDef] = {}
        self._load()

    def _load(self) -> None:
        data = self.path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        root = ET.fromstring(data)
        raw: list[tuple[str | None, list[RomId], dict[str, TableDef]]] = []
        for rom_el in root.findall("rom"):
            raw.append(_parse_rom_element(rom_el))

        resolved: list[RomDef] = []
        by_id: dict[str, RomDef] = {}

        def resolve(index: int, stack: set[int]) -> RomDef:
            base_name, romids, tables = raw[index]
            xmlids = [r.xmlid for r in romids if r.xmlid]
            key = xmlids[0].lower() if xmlids else f"anon-{index}"
            if key in by_id:
                return by_id[key]
            if index in stack:
                raise ValueError(f"Circular ROM base include involving {key}")
            merged_tables: dict[str, TableDef] = {}
            if base_name:
                parent = None
                wanted = base_name.lower()
                for j, (pbase, pids, ptables) in enumerate(raw):
                    pxmlids = [r.xmlid.lower() for r in pids if r.xmlid]
                    if wanted in pxmlids:
                        parent = resolve(j, stack | {index})
                        break
                if parent is None:
                    # Still usable: child tables only
                    merged_tables = {}
                else:
                    merged_tables = {n: copy.deepcopy(t) for n, t in parent.tables.items()}
            for name, table in tables.items():
                if name in merged_tables:
                    merged_tables[name] = merge_table(merged_tables[name], table)
                else:
                    merged_tables[name] = table
            rom = RomDef(xmlids=xmlids, romids=romids, tables=merged_tables, base_name=base_name)
            resolved.append(rom)
            for xmlid in xmlids:
                by_id[xmlid.lower()] = rom
            return rom

        for i in range(len(raw)):
            resolve(i, set())
        self.roms = resolved
        self._by_xmlid = by_id

    def get(self, xmlid: str) -> RomDef | None:
        return self._by_xmlid.get(xmlid.lower())

    def identify(self, data: bytes) -> tuple[RomDef | None, RomId | None, list[str]]:
        """Return (rom, best matching romid, all matching xmlids)."""
        hits: list[tuple[tuple[int, float], RomDef, RomId]] = []
        for rom in self.roms:
            for romid in rom.romids:
                expected = romid.internalidstring
                addr = romid.internalidaddress
                if not expected or addr is None:
                    continue
                nbytes = len(expected)
                if addr < 0 or addr + nbytes > len(data):
                    continue
                got = data[addr : addr + nbytes]
                try:
                    text = got.decode("ascii")
                except UnicodeDecodeError:
                    continue
                exact = text == expected
                if not exact and not text.startswith(expected):
                    continue
                # An exact id outranks a shorter id that is only a prefix of these bytes.
                rank = (
                    1 if exact else 0,
                    (0 if addr == 0x2000 else 2) + min(len(expected), 16) / 16.0,
                )
                hits.append((rank, rom, romid))
        if not hits:
            return None, None, []
        hits.sort(key=lambda h: h[0], reverse=True)
        best_score, best_rom, best_id = hits[0]
        # Prefer the highest-scoring hit; gather xmlids from the same rom
        matching_ids = [h[2].xmlid for h in hits if h[1] is best_rom]
        return best_rom, best_id, matching_ids
