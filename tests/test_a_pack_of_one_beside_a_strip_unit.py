"""A purchase line pulled from the server with tablets_per_stripe 1 beside a unit naming a strip.

Older clients sent 1 beside units like "20'" / "15'S" / "10TAB"; the line then read as one tablet a
strip (stores 131 / 139, Sep-Oct 2026: 3 strips of 15 stocked as 3). The unit is right there; a
loose line has unit "1" too and stays loose. Same rule as server syncService.purchaseItemPack.
"""
import unittest

from core.purchase_service import pulled_line_stock_units as units


class APackOfOneBesideAStripUnit(unittest.TestCase):
    def test_the_unit_wins(self):
        self.assertEqual(units({"type": "Tablet", "qty": 5, "free_qty": 0, "tablets_per_stripe": 1, "unit": "20'"}), 100)
        self.assertEqual(units({"type": "Capsule", "qty": 3, "free_qty": 0, "tablets_per_stripe": 1, "unit": "15'S"}), 45)
        self.assertEqual(units({"type": "Tablet", "qty": 1, "free_qty": 0, "tablets_per_stripe": 1, "unit": "10TAB"}), 10)

    def test_loose_own_pack_and_volume_stay(self):
        self.assertEqual(units({"type": "Tablet", "qty": 5, "free_qty": 0, "tablets_per_stripe": 1, "unit": "1"}), 5)
        self.assertEqual(units({"type": "Tablet", "qty": 2, "free_qty": 1, "tablets_per_stripe": 10, "unit": "15"}), 30)
        self.assertEqual(units({"type": "Syrup", "qty": 4, "free_qty": 0, "tablets_per_stripe": 1, "unit": "100ML"}), 4)


if __name__ == "__main__":
    unittest.main()
