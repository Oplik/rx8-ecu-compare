import unittest

from ecu_compare.app import (
    CurvePoint,
    best_changed_row,
    curve_points,
    fill_bands,
    is_curve_table,
    line_segments,
    nearest_index,
    plot_box,
    row_points,
    series_match,
    values_on,
    x_tick_indices,
)
from ecu_compare.defs import DefinitionDB, TableDef
from ecu_compare.rom import DecodedAxis, DecodedTable, load_rom
from ecu_compare.safety import ROOT


def _axis(values: list[float]) -> DecodedAxis:
    return DecodedAxis(
        name="Coolant Temperature",
        units="degC",
        raw=list(values),
        scaled=list(values),
        errors=[None] * len(values),
        address=0,
        storagetype="float",
        format="0",
    )


def _curve(values: list[float], axis: list[float] | None = None, kind: str = "2D") -> DecodedTable:
    breaks = axis if axis is not None else [float(i) for i in range(len(values))]
    return DecodedTable(
        definition=TableDef(name="Curve", table_type=kind, units="RPM", format="0"),
        data_raw=[[float(v) for v in values]],
        data_scaled=[[float(v) for v in values]],
        data_errors=[[None] * len(values)],
        data_bytes=b"",
        y_axis=_axis(breaks),
    )


class CurveMathTests(unittest.TestCase):
    def test_points_follow_the_coolant_axis(self):
        table = _curve([2500, 2400, 1400], [-40, 0, 110])
        points = curve_points(table)
        self.assertEqual([p.x for p in points], [-40, 0, 110])
        self.assertEqual([p.y for p in points], [2500, 2400, 1400])
        self.assertEqual(points[2].x_text, "110")
        self.assertEqual((points[1].y_index, points[1].x_index), (0, 1))

    def test_grid_and_scalar_are_not_curves(self):
        grid = _curve([1, 2, 3, 4])
        grid.data_raw = [[1, 2], [3, 4]]
        grid.data_scaled = [[1, 2], [3, 4]]
        grid.definition.table_type = "3D"
        self.assertFalse(is_curve_table(grid))
        self.assertEqual(curve_points(grid), [])
        scalar = _curve([7500])
        self.assertFalse(is_curve_table(scalar))

    def test_plot_puts_high_values_up_and_right(self):
        box = plot_box(500, 220, [-40, 110], [1400, 2500])
        self.assertLess(box.px(-40), box.px(110))
        self.assertLess(box.py(2500), box.py(1400))

    def test_click_picks_the_nearest_breakpoint(self):
        points = curve_points(_curve([2500, 1400], [-40, 110]))
        box = plot_box(500, 220, [-40, 110], [1400, 2500])
        self.assertEqual(nearest_index(points, box, box.px(-40)), 0)
        self.assertEqual(nearest_index(points, box, box.px(110)), 1)

    def test_missing_value_splits_the_line(self):
        points = curve_points(_curve([2500, 2400, 2200, 2000, 1800, 1400], [-40, -30, -20, -10, 0, 10]))
        gap = points[2]
        points = [
            points[0],
            points[1],
            CurvePoint(gap.index, gap.x, None, gap.y_index, gap.x_index, gap.x_text),
            points[3],
            points[4],
            points[5],
        ]
        box = plot_box(500, 220, [-40, 10], [1400, 2500])
        segments = line_segments(points, box)
        self.assertEqual(len(segments), 2)
        self.assertEqual(len(segments[0]), 2)
        self.assertEqual(len(segments[1]), 3)

    def test_fill_is_pink_when_file_b_is_higher(self):
        base = curve_points(_curve([2000, 2000], [0, 10]))
        higher = values_on(base, _curve([2200, 2100], [0, 10]))
        assert higher is not None
        box = plot_box(500, 220, [0, 10], [2000, 2200])
        bands = fill_bands(base, higher, box)
        self.assertEqual(len(bands), 1)
        self.assertEqual(bands[0][1], "#F8D0D0")
        self.assertTrue(series_match(base, base))
        self.assertFalse(series_match(base, higher))

    def test_tick_stride_keeps_the_ends(self):
        wide = x_tick_indices(16, 800)
        self.assertEqual(wide[0], 0)
        self.assertEqual(wide[-1], 15)
        self.assertIn(1, wide)
        narrow = x_tick_indices(16, 200)
        self.assertEqual(narrow[0], 0)
        self.assertEqual(narrow[-1], 15)
        self.assertGreater(narrow[1], 1)

    def test_row_graph_uses_the_x_axis_and_the_biggest_change(self):
        x_axis = _axis([10, 20, 30])
        x_axis.name = "Load"
        x_axis.units = "%"
        y_axis = _axis([1000, 2000])
        y_axis.name = "RPM"
        left = DecodedTable(
            definition=TableDef(name="Oil", table_type="3D", units="%", format="0"),
            data_raw=[[1, 2, 3], [4, 5, 9]],
            data_scaled=[[1, 2, 3], [4, 5, 9]],
            data_errors=[[None, None, None], [None, None, None]],
            data_bytes=b"",
            x_axis=x_axis,
            y_axis=y_axis,
        )
        right = DecodedTable(
            definition=left.definition,
            data_raw=[[1, 2, 3], [4, 8, 9]],
            data_scaled=[[1, 2, 3], [4, 8, 9]],
            data_errors=[[None, None, None], [None, None, None]],
            data_bytes=b"",
            x_axis=x_axis,
            y_axis=y_axis,
        )
        self.assertFalse(is_curve_table(left))
        points = row_points(left, 1)
        self.assertEqual([point.x for point in points], [10, 20, 30])
        self.assertEqual([point.y for point in points], [4, 5, 9])
        self.assertEqual(points[1].x_text, "20")
        self.assertEqual((points[1].y_index, points[1].x_index), (1, 1))
        self.assertEqual(best_changed_row(left, right, left.rows()), 1)
        overlaid = values_on(points, right)
        assert overlaid is not None
        self.assertEqual([point.y for point in overlaid], [4, 8, 9])
        self.assertEqual(best_changed_row(left, left, 2), 0)

    @unittest.skipUnless((ROOT / "data" / "roms" / "ORIG.bin").is_file(), "sample ROM is not part of the repository")
    def test_orig_decel_fuel_cut(self):
        defs = ROOT / "data" / "defs" / "rx8_defs_labeled.xml"
        rom = load_rom(ROOT / "data" / "roms" / "ORIG.bin", DefinitionDB(defs))
        table = rom.tables["Decel Fuel Cut"]
        points = curve_points(table)
        self.assertEqual(len(points), 16)
        self.assertEqual(points[0].x, -40)
        self.assertEqual(points[-1].x, 110)
        self.assertEqual(points[0].y, 2500)
        self.assertEqual(points[5].y, 2100)
        self.assertEqual(points[-1].y, 1400)
        oil = rom.tables["Oil Metering By Load"]
        oil_row = row_points(oil, 0)
        self.assertGreaterEqual(oil.rows(), 2)
        self.assertEqual(len(oil_row), oil.cols())
        self.assertGreaterEqual(len(oil_row), 2)


if __name__ == "__main__":
    unittest.main()
