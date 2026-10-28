import unittest

from ecu_compare.app import diff_cell_text, heatmap_color, is_placeholder_name, signed_delta, text_on


class HeatmapTests(unittest.TestCase):
    def test_range(self):
        low = heatmap_color(0, 0, 100)
        high = heatmap_color(100, 0, 100)
        mid = heatmap_color(50, 0, 100)
        self.assertTrue(low.startswith("#") and len(low) == 7)
        self.assertNotEqual(low, high)
        self.assertNotEqual(low, mid)

    def test_none_and_flat(self):
        self.assertEqual(heatmap_color(None, 0, 1), "#F4F8FB")
        self.assertEqual(heatmap_color(3, 5, 5), "#F4F8FB")

    def test_signed_delta(self):
        self.assertEqual(signed_delta(7500, 8000, "0"), "+500")
        self.assertEqual(signed_delta(8000, 7500, "0"), "-500")
        self.assertEqual(signed_delta(None, 1, "0"), "—")

    def test_diff_cell_shows_new_value_and_change(self):
        self.assertEqual(diff_cell_text(13, 20, "0"), "20\n+7")
        self.assertEqual(diff_cell_text(8000, 7500, "0"), "7500\n-500")
        self.assertEqual(diff_cell_text(5, 5, "0"), "5")
        self.assertEqual(diff_cell_text(None, 1, "0"), "—")
        self.assertEqual(diff_cell_text(1.001, 1.002, "0.00"), "1.00\n+0.001")
        self.assertEqual(signed_delta(1.001, 1.002, "0.00"), "+0.001")

    def test_placeholder_names(self):
        self.assertTrue(is_placeholder_name("Table 2D - 5"))
        self.assertTrue(is_placeholder_name("Table 3D - UNKNOWN_68c4c"))
        self.assertFalse(is_placeholder_name("Decel Fuel Cut"))
        self.assertFalse(is_placeholder_name("Normal Rev Limit"))

    def test_text_contrast(self):
        self.assertEqual(text_on("#FFFFFF"), "#111111")
        self.assertEqual(text_on("#111111"), "#FFFFFF")
        self.assertEqual(text_on(heatmap_color(0, 0, 100)), "#111111")
