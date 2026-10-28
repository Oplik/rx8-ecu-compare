# RX-8 ECU Compare

Desktop program that compares two Mazda RX-8 ECU binaries with RomRaider table definitions. It shows named map differences with units, draws 2D and 3D charts, and writes a text report.

It does not flash an ECU. Checksums are not updated !!!

## Requirements

Python 3 with tkinter. The standard Windows installer includes it.

## Start

Place a RomRaider RX-8 definition at `data/defs/rx8_defs.xml`, or use File → Definition file after startup. The program prefers `data/defs/rx8_defs_10.2.2026.xml` when that file is present.

Double-click `run.bat`, or from this folder:

```
set PYTHONPATH=src
python -m ecu_compare
```

Load two `.bin` files. Each file you pick is copied into `data/roms`. The program writes only inside its own folder.

Optional sample names the toolbar buttons look for:

- `data/roms/ORIG.bin`
- `data/roms/EditedMap.bin`

Those binaries are not part of this repository.

## What you can do

- Browse maps by category. Search matches the name, category, hex address, and the change summary.
- Tables with no address on the loaded ROM are listed in a closed folder named "Not on this ROM".
- Compare runs when both files are loaded. Pink means File B is higher. Blue means File B is lower.
- 2D maps draw both curves. 3D maps draw the selected row.
- Export a text report to `data/reports`. The report lists named tables with units, then every changed cell with address, hex, and converted values. Bytes that match no table are listed as hex.
- Editing is off until Extra settings → Enable map editing. Edits apply to File B in memory. Ctrl+Z undoes the last edit. Save writes a new file under `data/exports`. Immobilizer and switch tables stay locked.

Shortcuts: Ctrl+O load A, Ctrl+F search, F5 compare, F3 next changed map, F6 next changed cell, Ctrl+S export the current table, Ctrl+Z undo an edit. Arrow keys move the selected cell. Right-click a cell to copy it.

## Definitions

`data/defs/` contains RomRaider RX-8 definition XML used for table addresses and scaling. This program did not author those definitions.

- `rx8_defs_10.2.2026.xml` is loaded when it is present.
- `rx8_defs.xml` is an earlier RomRaider file.
- `rx8_defs_labeled.xml` is that earlier file with a few fuel-cut names filled in. Rebuild it with `python tools/label_defs.py`.
- `LABELS.md` records those names.

File → Definition file switches among the XML files in `data/defs`.

## Report

1. Named table changes with units.
2. Every changed cell: address, hex, and converted values.
3. Unmapped bytes as hex, with uint8 and big-endian uint16 views.

## Tests

```
set PYTHONPATH=src
python -m unittest discover -s tests -q
```

Tests that need sample ROMs are skipped when `data/roms/ORIG.bin` and `data/roms/EditedMap.bin` are absent.
