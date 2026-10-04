"""A return of several medicines goes up to the server with every line (4 Oct 2026).

From the second line on, each line was read with the medicine lookup's column names and
went up as medicine 0, qty 0: Vaibhav's SR113 and SR114 (3 lines each) reached the server
with one line. The refund was right; which medicines came back was lost.
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import server_entity_sync as fb  # noqa: E402


class EveryLineOfAReturn(unittest.TestCase):
    def setUp(self):
        from core.db_setup import initialise

        self.conn = sqlite3.connect(":memory:")
        initialise(self.conn)
        c = self.conn
        c.execute("INSERT INTO customers (id, name) VALUES (1, 'TEST')")
        c.execute("INSERT INTO suppliers (id, name) VALUES (1, 'TEST PHARMA')")
        for mid, name in ((1, "DOLO 650"), (2, "CIPCAL"), (3, "ZINCOVIT")):
            c.execute("INSERT INTO medicines (id, name, batch_no) VALUES (?, ?, ?)", (mid, name, f"B{mid}"))
        c.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount)"
                  " VALUES (1, 'SCB1/FY2026-27', 1, '2026-10-03', 100)")
        c.execute("INSERT INTO sales_returns (id, return_no, sale_id, customer_id, return_date, refund_amount)"
                  " VALUES (1, 'SR1', 1, 1, '2026-10-03', 100.31)")
        c.execute("INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date) VALUES (1, 'P1', 1, '2026-10-01')")
        c.execute("INSERT INTO purchase_returns (id, return_no, purchase_id, supplier_id, return_date, refund_amount)"
                  " VALUES (1, 'PR1', 1, 1, '2026-10-03', 60)")
        for mid, qty, rate in ((1, 3, 7.425), (2, 3, 18.414), (3, 3, 7.594)):
            c.execute("INSERT INTO sales_return_items (return_id, medicine_id, qty, rate, amount) VALUES (1,?,?,?,?)",
                      (mid, qty, rate, round(qty * rate, 2)))
            c.execute("INSERT INTO purchase_return_items (return_id, medicine_id, qty, rate, amount) VALUES (1,?,?,?,?)",
                      (mid, qty, rate, round(qty * rate, 2)))
        c.commit()

    def _lines(self, payload):
        return [(i["medicine_id"], i["name"], i["batch"], i["qty"], i["amount"]) for i in payload["items"]]

    def test_a_sales_return_sends_all_three_medicines(self):
        p = fb.build_sales_return_payload(self.conn, 1)
        self.assertEqual([(1, "DOLO 650", "B1", 3, 22.27), (2, "CIPCAL", "B2", 3, 55.24),
                          (3, "ZINCOVIT", "B3", 3, 22.78)], self._lines(p))
        self.assertEqual(3, p["item_count"])

    def test_a_purchase_return_sends_all_three_medicines(self):
        p = fb.build_purchase_return_payload(self.conn, 1)
        self.assertEqual([1, 2, 3], [i["medicine_id"] for i in p["items"]])
        self.assertEqual(["DOLO 650", "CIPCAL", "ZINCOVIT"], [i["name"] for i in p["items"]])


if __name__ == "__main__":
    unittest.main()
