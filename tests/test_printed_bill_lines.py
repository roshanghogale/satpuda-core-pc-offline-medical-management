"""A printed line must add up: MRP x Qty has to reach the Amount charged.

medicines.mrp holds the price of a STRIP while a sale is counted in tablets, so
the bill printed "MRP 199.65 x Qty 10" against an amount of 199.65 -- out by the
strip size. The shop could not reconcile its own bill against the total.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.layout_config import is_strip_count_type, parse_tablets_per_stripe  # noqa: E402


def printed_mrp(med_type: str, unit: str, strip_mrp: float) -> float:
    """The MRP as bill_context now prints it: per the unit the qty counts in."""
    mrp = float(strip_mrp)
    if is_strip_count_type(str(med_type or ""), str(unit or "")):
        per_strip = parse_tablets_per_stripe(unit)
        if per_strip > 1:
            return round(mrp / per_strip, 4)
    return mrp


class TestPrintedLineReconciles(unittest.TestCase):
    def _check(self, med_type, unit, strip_mrp, qty, amount):
        shown = printed_mrp(med_type, unit, strip_mrp)
        self.assertAlmostEqual(round(shown * qty, 2), amount, places=2,
                               msg=f"{med_type} {unit}: {shown} x {qty}")

    def test_tablet_strip_of_ten(self):
        self._check("Tablet", "10", 199.65, 10, 199.65)

    def test_tablet_strip_of_fifteen_multiple_strips(self):
        self._check("Tablet", "15", 150.00, 30, 300.00)

    def test_loose_units_are_untouched(self):
        self._check("Injection", "1", 42.50, 3, 127.50)

    def test_measured_types_are_untouched(self):
        # A syrup is priced per bottle, not per millilitre, even though its
        # unit string carries a measure.
        self._check("Syrup", "100ML", 85.00, 2, 170.00)

    def test_strip_mrp_is_divided_not_multiplied(self):
        self.assertLess(printed_mrp("Tablet", "10", 199.65), 199.65)

    def test_unparseable_unit_leaves_the_mrp_alone(self):
        # Better to print the honest strip price than to divide by a guess.
        self.assertEqual(printed_mrp("Tablet", "", 50.0), 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
