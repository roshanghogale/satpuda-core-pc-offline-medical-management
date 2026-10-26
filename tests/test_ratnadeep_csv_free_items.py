"""Ratnadeep Pharma's H/T/F CSV (the "seema_legacy" layout) carries the free quantity in T[16],
right after the quantity. The importer read it as none, so every free item of these bills was
lost (Jagdamba, bills 4147 / 4282, 26 Oct 2026)."""
import os
import tempfile
import unittest

from core import purchase_importer as pi

ROWS = """H,1.1,4147,18102026,,,,1,,18102026,,,0,,0,1
T,,,763,763,BECLOMIN OINT,15GM,LEB,01612,01032028,40.38,40.38,53.00,,,5,1,,,,,201.90,2.50,5.05,5,10.10,2.50,5.05,,0,3004
T,,,1780,1780,HIFEN 100 TAB,10 TAB,LYK,HH2606290,01052028,49.05,49.05,61.80,,,4.5,0.5,,,,,220.73,2.50,5.52,5,11.04,2.50,5.52,,0,3004
T,,,1422,1422,ORS APPLE DRINK-WHO,200 ML,WHO,FKA610001,01022028,14.26,14.26,32.26,,,60,,,,,,855.60,2.50,21.39,5,42.78,2.50,21.39,,0,30049086
F,1340.00,,0,0,0,0,,,,,,,,,,,,,,,1340.00
"""


class RatnadeepFreeItems(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".CSV")
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(ROWS)

    def tearDown(self):
        os.remove(self.path)

    def test_free_quantity_is_read_and_not_charged(self):
        inv = pi.parse_purchase_excel(self.path)
        by = {i.name: i for i in inv.items}
        self.assertEqual((by["BECLOMIN OINT"].qty, by["BECLOMIN OINT"].free_qty), (5.0, 1.0))
        self.assertEqual((by["HIFEN 100 TAB"].qty, by["HIFEN 100 TAB"].free_qty), (4.5, 0.5))
        self.assertEqual(by["ORS APPLE DRINK-WHO"].free_qty, 0.0)
        # The amount is still qty x rate: the free goods are not billed.
        self.assertAlmostEqual(by["BECLOMIN OINT"].amount, 201.90, places=2)

    def test_the_cgst_column_is_not_taken_as_a_discount(self):
        # T[22] = 2.50 is CGST %; T[21] = rate x qty, so no line has a discount.
        inv = pi.parse_purchase_excel(self.path)
        self.assertEqual([i.discount_pct for i in inv.items], [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
