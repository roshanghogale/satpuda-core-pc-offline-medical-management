"""A supplier's due equals the sum of its open purchases (live check of 22 Oct 2026).

Stores 127, 132, 139 and 148: a purchase paid past its own total at entry (the purchase
screen asks for bill + previous due) lowered the supplier's due, but the older purchases
stayed open because purchases did not let an entry overpayment pay other bills (sales did).
Store 4: a 441.01 purchase paid 441 left the supplier owing 0.01 with no open purchase.
Same rule as server-live partyDueCascade.js and the phone's PartyBills.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core.customer_service import recalculate_customer_due  # noqa: E402
from core.purchase_service import recalculate_supplier_due  # noqa: E402


class SupplierDueMatchesOpenPurchases(unittest.TestCase):
    def setUp(self):
        p = mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False)
        p.start()
        self.addCleanup(p.stop)
        self.conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.sid = self.conn.execute("INSERT INTO suppliers (name) VALUES ('TEST SUPPLIER')").lastrowid

    def purchase(self, no, d, total, paid):
        return self.conn.execute(
            "INSERT INTO purchases (purchase_no, purchase_date, supplier_id, total_amount, final_amount, "
            "amount_paid, amount_paid_at_entry, cash_paid_at_entry, due_amount, due, bill_cleared) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (no, d, self.sid, total, total, paid, paid, paid, max(0, total - paid), max(0, total - paid),
             1 if paid >= total else 0)).lastrowid

    def check(self):
        recalculate_supplier_due(self.conn, self.sid)
        due, credit = self.conn.execute(
            "SELECT total_due, total_credit FROM suppliers WHERE id=?", (self.sid,)).fetchone()
        open_sum = self.conn.execute(
            "SELECT COALESCE(SUM(due_amount),0) FROM purchases WHERE supplier_id=? AND bill_cleared=0",
            (self.sid,)).fetchone()[0]
        self.assertAlmostEqual(due, open_sum, places=2)
        return due, credit

    def state(self, pid):
        return self.conn.execute(
            "SELECT due_amount, due, total_due, bill_cleared, account_cleared FROM purchases WHERE id=?",
            (pid,)).fetchone()

    def test_previous_due_paid_on_a_new_purchase_clears_the_older_one(self):
        old = self.purchase("1/FY2026-27", "2026-01-12", 3000, 0)
        new = self.purchase("2/FY2026-27", "2026-06-20", 285, 2185)  # 285 + 1900 of the previous due
        self.conn.commit()
        self.assertEqual(self.check(), (1100.0, 0.0))
        self.assertEqual(self.state(old), (1100.0, 1100.0, 1100.0, 0, 0))
        self.assertEqual(self.state(new)[3], 1)

    def test_more_than_everything_owed_is_a_credit_and_nothing_open(self):
        old = self.purchase("1/FY2026-27", "2026-06-01", 126, 0)
        self.purchase("2/FY2026-27", "2026-06-17", 269, 867)
        self.conn.commit()
        self.assertEqual(self.check(), (0.0, 472.0))
        self.assertEqual(self.state(old), (0.0, 0.0, 0.0, 1, 1))

    def test_a_paisa_is_no_due(self):
        self.purchase("1/FY2026-27", "2026-10-12", 441.01, 441)
        self.conn.commit()
        self.assertEqual(self.check(), (0.0, 0.0))

    def test_a_customer_paisa_is_no_due(self):
        cid = self.conn.execute("INSERT INTO customers (name) VALUES ('TEST CUSTOMER')").lastrowid
        self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid, due_amount, total_due) "
            "VALUES ('SCB1/FY2026-27', '2026-10-01', ?, 100.01, 100, 0, 0)", (cid,))
        self.conn.commit()
        recalculate_customer_due(self.conn, cid, sync=False)
        self.assertEqual(self.conn.execute(
            "SELECT total_due, total_credit FROM customers WHERE id=?", (cid,)).fetchone(), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
