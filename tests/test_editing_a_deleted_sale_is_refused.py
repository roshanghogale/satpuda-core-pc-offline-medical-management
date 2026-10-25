"""Saving an edit of a sale bill that was deleted meanwhile is refused.

Offline-first: a bill deleted on another PC comes in as deleted=1 (its stock already given
back by that PC). An edit tab still open on this PC saved it as deleted=0 again and put the
old lines' stock back a second time. A bill deleted here in another tab (row gone) failed
half-way. Both now answer code "bill_deleted" and touch nothing.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_sales_service


def _shop(deleted: int | None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
    conn.execute(
        "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, batch_no, "
        "expiry_date) VALUES (1, 'PARA TAB', 'TAB', '1', 12, 100, 60, 50, 'B1', '2028-01-01')"
    )
    if deleted is not None:
        conn.execute(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, deleted) "
            "VALUES (7, 'SCB7', 1, '2026-10-20', 200, ?)",
            (deleted,),
        )
        conn.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate) VALUES (7, 1, 2, 100)")
    conn.commit()
    return conn


BODY = {"items": [{"id": 1, "name": "PARA TAB", "batch": "B1", "expiry": "2028-01-01", "qty": 1,
                   "rate": 100.0, "amount": 100.0}],
        "bill_date": "2026-10-20", "customer_name": "RAM", "payment_mode": "Cash",
        "cash_paid": 100.0, "editing_sale_id": 7}


class EditOfADeletedBill(unittest.TestCase):
    def _save(self, conn):
        with mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True), \
                mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.desktop_returns_service.edit_below_returned_error", return_value=None):
            return desktop_sales_service.save_sale(conn, dict(BODY))

    def test_deleted_on_another_pc_is_not_brought_back(self):
        conn = _shop(deleted=1)
        res = self._save(conn)
        self.assertFalse(res.get("ok"), res)
        self.assertEqual(res.get("code"), "bill_deleted")
        self.assertEqual(conn.execute("SELECT deleted FROM sales WHERE id=7").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT stock_qty FROM medicines WHERE id=1").fetchone()[0], 50)

    def test_deleted_here_is_refused(self):
        conn = _shop(deleted=None)
        res = self._save(conn)
        self.assertEqual(res.get("code"), "bill_deleted", res)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales_items").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT stock_qty FROM medicines WHERE id=1").fetchone()[0], 50)

    def test_a_live_bill_still_saves(self):
        conn = _shop(deleted=0)
        with mock.patch("core.billing_service.update_existing_bill") as update:
            res = self._save(conn)
        self.assertNotEqual(res.get("code"), "bill_deleted", res)
        update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
