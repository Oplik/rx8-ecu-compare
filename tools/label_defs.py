"""Build rx8_defs_labeled.xml from the stock RomRaider copy.

Reads and writes only inside this project. data/defs/rx8_defs.xml is left as-is.
"""

from __future__ import annotations

import copy
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "defs" / "rx8_defs.xml"
DST = ROOT / "data" / "defs" / "rx8_defs_labeled.xml"

# old exact name -> new metadata applied on the BASE table (and name change on every ROM)
LABELS: dict[str, dict] = {
    "Table 2D - 5": {
        "name": "Decel Fuel Cut",
        "category": "Fuel Cut",
        "units": "RPM",
        "description": (
            "DFCO enable RPM vs coolant temperature. When throttle is closed and RPM is "
            "above this value, fuel is cut. Identified on an N3ZGEC ROM at 0x06D1E0; "
            "axis 0x06D1A0 is -40..110 C as floats. Values match the Pop and Bang "
            "'Decel Fuel Cut' table."
        ),
        "scaling_units": "RPM",
        "scaling_format": "0",
        "y_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
    },
    "Table 3D - 0 ": {
        "name": "Decel Fuel Cut Restore - By Gear",
        "category": "Fuel Cut",
        "units": "RPM",
        "description": (
            "RPM where fuel is restored after deceleration fuel cut, vs coolant temp (X) "
            "and gear 1-6 (Y). Sits next to Decel Fuel Cut (same E650 group). Warm gear-1 "
            "stays ~1300 RPM to avoid stall; gears 4-6 drop to ~1050. Data 0x06D2AC, "
            "uint8 * 25 = RPM."
        ),
        "scaling_units": "RPM",
        "scaling_format": "0",
        "x_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
        "y_axis": {"name": "Gear", "units": "", "format": "0"},
    },
    "Table 3D - 1 ": {
        "name": "Decel Fuel Cut Restore Adder - By Gear",
        "category": "Fuel Cut",
        "units": "RPM",
        "description": (
            "Same coolant x gear axes as Decel Fuel Cut Restore. Stock is all zeros; "
            "likely an unused RPM adder. Data 0x06D364."
        ),
        "scaling_units": "RPM",
        "x_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
        "y_axis": {"name": "Gear", "format": "0"},
    },
    "Table 3D - 2 ": {
        "name": "Decel Fuel Cut Delay A - By Gear",
        "category": "Fuel Cut",
        "description": (
            "Same coolant x gear axes as the DFCO RPM maps (E910 group). Values 50-300; "
            "units not proven (possibly milliseconds of delay before cut). Gear 1 is larger "
            "when warm. Data 0x06D41C."
        ),
        "x_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
        "y_axis": {"name": "Gear", "format": "0"},
    },
    "Table 3D - 3 ": {
        "name": "Decel Fuel Cut Delay B - By Gear",
        "category": "Fuel Cut",
        "description": (
            "Companion to Decel Fuel Cut Delay A (identical stock values). "
            "Possibly on vs off delay or two operating modes. Data 0x06D4D4."
        ),
        "x_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
        "y_axis": {"name": "Gear", "format": "0"},
    },
    "Table 2D - 175 ": {
        "name": "Secondary Fuel Injector Cranking Pulse Width",
        "category": "Fuel Injection",
        "units": "ms",
        "description": (
            "Same scaling as Primary Fuel Injector Cranking Pulse Width (uint16 * 0.005 ms) "
            "and nearly the same ECT curve, with 12 breakpoints instead of 13. Labeled as "
            "secondary cranking PW. Data 0x078318."
        ),
        "scaling_units": "ms",
        "scaling_format": "0.00",
        "y_axis": {"name": "Coolant Temperature", "units": "degC", "format": "0"},
    },
    "Closed Loop Fuelling Threshold (Unknown A)": {
        "name": "Closed Loop Fuelling Threshold (Load vs RPM)",
        "category": "Fuel Target (Closed Loop)",
        "description": (
            "2D threshold: Y axis is engine speed 750-6000 RPM; cell values look like "
            "load (g/rev-ish, 0.70 down to 0). Used with the other closed-loop enable "
            "thresholds. Data 0x071ED0."
        ),
        "y_axis": {"name": "Engine Speed", "units": "RPM", "format": "0"},
    },
}

IDLE_AXIS = {
    "name": "Coolant Temperature",
    "units": "degC",
    "format": "0",
}
IDLE_TABLES = [
    "Idle Target A",
    "Idle Target B",
    "Idle Target C",
    "Idle Target D",
    "Idle Target E",
    "Idle Target F",
    "Idle Target G",
    "Idle Target H",
    "Idle Target I",
]


def _set_desc(table: ET.Element, text: str) -> None:
    el = table.find("description")
    if el is None:
        el = ET.Element("description")
        table.insert(0, el)
    el.text = text


def _axis_tables(table: ET.Element) -> list[ET.Element]:
    return [c for c in list(table) if c.tag == "table"]


def _apply_axis(axis_el: ET.Element, spec: dict) -> None:
    if spec.get("name"):
        axis_el.set("name", spec["name"])
    if spec.get("units"):
        axis_el.set("units", spec["units"])
    scaling = axis_el.find("scaling")
    if scaling is None and (spec.get("units") or spec.get("format")):
        scaling = ET.SubElement(axis_el, "scaling")
    if scaling is not None:
        if spec.get("units"):
            scaling.set("units", spec["units"])
        if spec.get("format"):
            scaling.set("format", spec["format"])


def _apply_label(table: ET.Element, spec: dict) -> None:
    table.set("name", spec["name"])
    if spec.get("category"):
        table.set("category", spec["category"])
    if spec.get("units"):
        table.set("units", spec["units"])
    if spec.get("description"):
        _set_desc(table, spec["description"])
    scaling = table.find("scaling")
    if scaling is not None:
        if spec.get("scaling_units"):
            scaling.set("units", spec["scaling_units"])
        if spec.get("scaling_format"):
            scaling.set("format", spec["scaling_format"])
    axes = _axis_tables(table)
    for axis_el in axes:
        kind = (axis_el.get("type") or "").lower()
        if "x axis" in kind and spec.get("x_axis"):
            _apply_axis(axis_el, spec["x_axis"])
        elif "y axis" in kind and spec.get("y_axis"):
            _apply_axis(axis_el, spec["y_axis"])


def build() -> Path:
    if not SRC.is_file():
        raise FileNotFoundError(SRC)
    shutil.copy2(SRC, DST)
    tree = ET.parse(DST)
    root = tree.getroot()
    renamed = 0
    idle_axes = 0
    for rom in root.findall("rom"):
        for table in list(rom):
            if table.tag != "table":
                continue
            old = table.get("name") or ""
            if old in LABELS:
                spec = LABELS[old]
                # Child ROMs often only have name + storageaddress; still rename the join key.
                table.set("name", spec["name"])
                if table.get("type") or table.get("category") or table.find("scaling") is not None:
                    _apply_label(table, spec)
                renamed += 1
            if old in IDLE_TABLES:
                if table.get("type"):
                    table.set("units", "RPM")
                    scaling = table.find("scaling")
                    if scaling is not None:
                        scaling.set("units", "RPM")
                        scaling.set("format", "0")
                    for axis_el in _axis_tables(table):
                        if "y axis" in (axis_el.get("type") or "").lower():
                            _apply_axis(axis_el, IDLE_AXIS)
                            idle_axes += 1
    comment = ET.Comment(
        " Labeled overlay of rx8_defs.xml. Original names/addresses preserved via tools/label_defs.py. "
        "Identified from a 60E0G500 / N3ZGEC ROM. Stock rx8_defs.xml is unchanged. "
    )
    root.insert(0, comment)
    tree.write(DST, encoding="utf-8", xml_declaration=True)
    print(f"Wrote {DST}")
    print(f"Renamed table occurrences: {renamed}")
    print(f"Idle target axes labeled: {idle_axes}")
    return DST


if __name__ == "__main__":
    build()
