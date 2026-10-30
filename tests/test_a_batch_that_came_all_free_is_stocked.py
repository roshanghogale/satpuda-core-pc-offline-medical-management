"""A purchase line with Qty 0 and only Free (owner, 30 Oct 2026: "the free is a quantity too").

The Purchase screen refused such a line ("Quantity must be greater than zero"); it now asks only
for the quantity OR the free quantity. Behind it, the save already stocks qty + free: these
tests hold that for a free-only line, Online and Offline, and that it is taken back on delete.
Made-up medicine; the server client is patched, nothing reaches a server.
"""
from __future__ import annotations

import os
import re
import unittest

from core import desktop_purchase_service, purchase_service
from tests import test_a_loose_purchase_line_stocks_its_own_pack as loose


class OnlineAFreeOnlyLine(loose._Online):
    def test_two_free_strips_add_twenty_tablets(self):
        doc, med, moved = self.save([loose._screen_line(0, 10, free=2)])
        self.assertEqual(moved, 20)
        self.assertEqual(med["stock_qty"], 98.0)
        self.assertEqual((doc["items"][0]["qty"], doc["items"][0]["free_qty"]), (0, 2))


class OfflineAFreeOnlyLine(loose.OfflineALooseLine):
    def test_save_and_delete(self):
        line = dict(self.line(0, 10), free_qty=3)
        purchase_service.save_purchase(self.conn, 1, "2026-09-12", "INV-F", self.calc(), [line])
        self.assertEqual(self.med(), (31, "10"))
        row = self.conn.execute("SELECT qty, free_qty FROM purchase_items").fetchone()
        self.assertEqual(row, (0, 3))
        pid = self.conn.execute("SELECT id FROM purchases ORDER BY id DESC LIMIT 1").fetchone()[0]
        res = desktop_purchase_service.delete_saved_purchase(self.conn, {"purchase_id": pid})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.med()[0], 1)


class TheScreenAcceptsIt(unittest.TestCase):
    def test_the_add_check_counts_free(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "desktop", "src", "pages", "PurchasePage.tsx"), encoding="utf-8").read()
        self.assertNotIn("Quantity must be greater than zero.", src)
        self.assertTrue(re.search(r"if \(qty <= 0 && !\(Math\.max\(0, Number\(freeStrips\)", src))


if __name__ == "__main__":
    unittest.main()
