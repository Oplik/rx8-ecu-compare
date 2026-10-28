import unittest

from ecu_compare.scaling import ScalingError, eval_expr, format_scaled, normalize_expr


class ScalingTests(unittest.TestCase):
    def test_timing_expression(self):
        self.assertAlmostEqual(eval_expr("(x * 0.5) - 50", 120), 10.0)
        self.assertAlmostEqual(eval_expr("x*0.5-50", 120), 10.0)

    def test_to_byte(self):
        self.assertAlmostEqual(eval_expr("(x+50)/.5", 10), 120.0)

    def test_caret_power(self):
        self.assertEqual(normalize_expr("x^-1"), "x**-1")
        self.assertAlmostEqual(eval_expr("x^-1", 0.5), 2.0)

    def test_divide_by_zero(self):
        with self.assertRaises(ScalingError):
            eval_expr("(x - -0.01) / 0.0", 1)

    def test_rejects_calls(self):
        with self.assertRaises(ScalingError):
            eval_expr("__import__('os').system('echo hi')", 1)

    def test_format(self):
        self.assertEqual(format_scaled(10.0, "0.00"), "10.00")
        self.assertEqual(format_scaled(9000, "0"), "9000")


if __name__ == "__main__":
    unittest.main()
