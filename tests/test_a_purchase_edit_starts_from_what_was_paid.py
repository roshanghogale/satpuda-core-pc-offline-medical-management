"""A saved purchase opened for edit starts from what it was paid at entry (22 Oct 2026).

The Purchase screen's Cash / Online boxes were filled from cash_paid_at_entry /
online_paid_at_entry alone. A purchase stored with only its paid-at-entry figure (346 on the live
server: stores 126, 127, 132) opened with Cash 0, and saving the edit for any other reason turned
the paid bill into a full due on the supplier. The supplier's due is worked out from cash +
online, else amount_paid_at_entry, else amount_paid (server PURCHASE_ENTRY_PAID_SQL), and the
edit now starts from the same figure.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_purchase_service as dps


class EntryPaymentForEdit(unittest.TestCase):
    def test_the_cash_and_online_split_is_kept(self):
        self.assertEqual(dps.entry_payment_for_edit(30, 20, 50, 50), (30.0, 20.0))
        self.assertEqual(dps.entry_payment_for_edit(0, 75, 75, 0), (0.0, 75.0))

    def test_paid_at_entry_without_a_split_is_cash(self):
        self.assertEqual(dps.entry_payment_for_edit(0, 0, 3952, 0), (3952.0, 0.0))
        self.assertEqual(dps.entry_payment_for_edit(None, None, "120.5", None), (120.5, 0.0))

    def test_then_amount_paid(self):
        self.assertEqual(dps.entry_payment_for_edit(0, 0, 0, 40), (40.0, 0.0))

    def test_an_unpaid_purchase_stays_unpaid(self):
        self.assertEqual(dps.entry_payment_for_edit(0, 0, 0, 0), (0.0, 0.0))


class ThePurchaseScreenLoad(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        sid = conn.execute("INSERT INTO suppliers (name) VALUES ('ENTRY PAID SUPPLIER')").lastrowid
        mid = conn.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES ('ENTRY PAID TAB','Tablet',100,'10',20,10,'E1')"
        ).lastrowid
        pid = conn.execute(
            "INSERT INTO purchases (purchase_no, supplier_id, purchase_date, bill_number, subtotal, "
            "total_amount, final_amount, need_to_pay, amount_paid, amount_paid_at_entry, "
            "cash_paid_at_entry, online_paid_at_entry, due, due_amount, bill_cleared, account_cleared) "
            "VALUES ('7/FY2026-27', ?, '2026-10-07', 'EP1', 100, 100, 100, 100, 100, 100, 0, 0, 0, 0, 1, 1)",
            (sid,),
        ).lastrowid
        conn.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, mrp, gst_pct, item_amount) "
            "VALUES (?,?,10,10,20,0,100)", (pid, mid),
        )
        conn.commit()
        return conn, pid

    def test_offline_a_paid_purchase_without_a_split_opens_paid_in_cash(self):
        conn, pid = self._conn()
        form = dps.load_purchase(conn, pid)["form"]
        self.assertEqual((form["cash_paid"], form["online_paid"]), (100.0, 0.0))

    def test_online_the_store_copy_opens_paid_in_cash(self):
        from core import online_catalog, server_crud, sync_prefs

        doc = {
            "id": 81, "local_id": 81, "purchase_no": "67/FY2026-27", "supplier_id": 5,
            "supplier_name": "ENTRY PAID SUPPLIER", "purchase_date": "2026-05-28", "bill_number": "X1",
            "total_amount": 3952, "final_amount": 3952, "amount_paid": 0, "amount_paid_at_entry": 3952,
            "cash_paid_at_entry": 0, "online_paid_at_entry": 0, "items": [],
        }
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        with mock.patch.object(sync_prefs, "is_online_mode", lambda: True), \
                mock.patch.object(server_crud, "get_doc", lambda c, i: dict(doc) if c == "purchases" else {}), \
                mock.patch.object(online_catalog, "find_supplier_by_id", lambda i: {"id": 5, "name": "ENTRY PAID SUPPLIER"}), \
                mock.patch.object(online_catalog, "medicine_by_id", lambda i: None):
            out = dps.load_purchase(conn, 81)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual((out["form"]["cash_paid"], out["form"]["online_paid"]), (3952.0, 0.0))


if __name__ == "__main__":
    unittest.main()
