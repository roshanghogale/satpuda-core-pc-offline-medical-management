"""A bill made Offline lists on top, after the store's bills came down from the server (3 Oct 2026).

The server's bills were written without their FY serial, so Sales History and Home sorted
them by row id (thousands) and the new bill by its serial (SCB 122): the new bill, numbered
right, sat at the bottom of the day.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import server_entity_sync as fb  # noqa: E402
from core.billing_service import fetch_last_sale_id, fetch_recent_sales  # noqa: E402
from core.fy_serial import backfill_missing_fy_serials  # noqa: E402


def _sale(no, serial=None):
    d = {"bill_no": f"SCB{no}/FY2026-27", "bill_date": "2026-10-03", "customer_id": 1,
         "customer_name": "TEST", "total_amount": 100, "items": []}
    if serial is not None:
        d["fy_serial"] = serial
        d["fy_start_year"] = 2026
    return d


class PulledBillsKeepTheirSerial(unittest.TestCase):
    def setUp(self):
        from core.db_setup import initialise

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.conn = sqlite3.connect(os.path.join(d, "veterinary.db"))
        self.addCleanup(self.conn.close)
        initialise(self.conn)
        self.conn.execute("INSERT OR IGNORE INTO customers (id, name) VALUES (1, 'TEST')")
        self.conn.commit()
        online = mock.patch("core.sync_prefs.is_online_mode", return_value=True)
        online.start()
        self.addCleanup(online.stop)

    def _new_local_bill(self, sale_id, serial):
        self.conn.execute(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, fy_start_year, fy_serial)"
            " VALUES (?, ?, 1, '2026-10-03', 50, 2026, ?)", (sale_id, f"SCB{serial}/FY2026-27", serial))
        self.conn.commit()

    def test_a_pulled_bill_gets_the_serial_of_its_number(self):
        fb.sync_down_doc(self.conn, "sales", "5000", _sale(120))
        fb.sync_down_doc(self.conn, "sales", "5001", _sale(121, serial=121))
        rows = dict(self.conn.execute("SELECT id, fy_serial FROM sales").fetchall())
        self.assertEqual({5000: 120, 5001: 121}, rows)

    def test_the_new_bill_is_first(self):
        fb.sync_down_doc(self.conn, "sales", "5000", _sale(120))
        fb.sync_down_doc(self.conn, "sales", "5001", _sale(121))
        self._new_local_bill(5002, 122)
        self.assertEqual(5002, fetch_last_sale_id(self.conn))
        self.assertEqual([5002, 5001, 5000], [r[0] for r in fetch_recent_sales(self.conn, 5)])
        order = [r[0] for r in self.conn.execute(
            "SELECT id FROM sales ORDER BY bill_date DESC, COALESCE(fy_serial, id) DESC, id DESC")]
        self.assertEqual([5002, 5001, 5000], order)       # Sales History's own ORDER BY

    def test_a_store_already_offline_is_repaired(self):
        self.conn.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount)"
                          " VALUES (5000, 'SCB120/FY2026-27', 1, '2026-10-03', 10)")
        self.conn.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount)"
                          " VALUES (5001, 'OLD-7', 1, '2026-10-03', 10)")
        self.conn.execute("INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date)"
                          " VALUES (900, '45/FY2026-27', 1, '2026-10-02')")
        self.conn.commit()
        self.assertEqual({"sales": 1, "purchases": 1}, backfill_missing_fy_serials(self.conn))
        self.assertEqual((2026, 120), self.conn.execute(
            "SELECT fy_start_year, fy_serial FROM sales WHERE id=5000").fetchone())
        self.assertIsNone(self.conn.execute("SELECT fy_serial FROM sales WHERE id=5001").fetchone()[0])
        self.assertEqual(45, self.conn.execute("SELECT fy_serial FROM purchases WHERE id=900").fetchone()[0])
        self.assertEqual({"sales": 0, "purchases": 0}, backfill_missing_fy_serials(self.conn))


if __name__ == "__main__":
    unittest.main()
