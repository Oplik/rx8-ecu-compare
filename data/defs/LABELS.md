# Labeled RX-8 definitions

- Definition loaded when present: `rx8_defs_10.2.2026.xml`
- Earlier RomRaider file, left unchanged: `rx8_defs.xml`
- Names added on that earlier file: `rx8_defs_labeled.xml`
- Rebuild the labeled file: `python tools/label_defs.py`

Names below were checked against a CAL `60E0G500` / ECU `N3ZGEC` binary.

## Renames

| Old RomRaider name | New name | Address | Why |
|---|---|---|---|
| Table 2D - 5 | **Decel Fuel Cut** | data `0x06D1E0`, ECT axis `0x06D1A0` | 16 coolant temps −40…110 °C, uint16 RPM 2500→1400. Matches the Pop and Bang “Decel Fuel Cut” screenshot exactly. |
| Table 3D - 0 | **Decel Fuel Cut Restore - By Gear** | `0x06D2AC` | Same ECT axis, Y = gears 1–6, uint8×25 = RPM ~350 below the cut table. Gear 1 stays higher when warm. |
| Table 3D - 1 | **Decel Fuel Cut Restore Adder - By Gear** | `0x06D364` | Same axes; stock is all zeros. |
| Table 3D - 2 | **Decel Fuel Cut Delay A - By Gear** | `0x06D41C` | Same axes; values 50–300. Units not proven (possibly ms). |
| Table 3D - 3 | **Decel Fuel Cut Delay B - By Gear** | `0x06D4D4` | Same stock values as Delay A. |
| Table 2D - 175 | **Secondary Fuel Injector Cranking Pulse Width** | `0x078318` | Same ms scaling as the named primary cranking PW, nearly the same ECT curve. |
| Closed Loop Fuelling Threshold (Unknown A) | **Closed Loop Fuelling Threshold (Load vs RPM)** | `0x071ED0` | Axis is 750–6000 RPM; cells look like load. |

Idle Target A–I: axis labeled **Coolant Temperature**, units **RPM**. Names unchanged.

Hundreds of `Table 2D/3D - N` entries are still unnamed. Only maps with a strong match on that ROM were renamed, so labels are not invented.
