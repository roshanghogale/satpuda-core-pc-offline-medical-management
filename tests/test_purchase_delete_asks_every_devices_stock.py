"""Deleting a purchase takes its stock back; it is refused when that would take stock below zero.

Offline-first judged that on this PC's copy alone. A sale made a moment ago on the phone or the
other PC is not in the copy until the next pull: the check passed, the delete's stock movement
went up, and the shop's real stock went negative on every device. The server's figure (plus
this PC's own unsent movements) is asked first when the sync is working; with no internet the
local check stands as before.

And in every mode: two lines of the same batch were each checked against the whole stock.
No server is contacted here.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_purchase_service
from core.offline_first import worker

from tests.test_offline_first_pc_local import Base as OfBase


def _purchase(conn, lines, *, med_stock):
    """Supplier 1, medicine 20 holding med_stock, purchase 500 with the given stock_units lines."""
    conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
    conn.execute(
        "INSERT INTO medicines (id, name, type, unit, stock_qty, batch_no) "
        "VALUES (20, 'DOLO 650', 'Syrup', '1', ?, 'B1')", (med_stock,))
    conn.execute("INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, bill_number) "
                 "VALUES (500, '9', 1, '2026-10-20', 'INV-9')")
    for units in lines:
        conn.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, free_qty, type, stock_units) "
                     "VALUES (500, 20, ?, 0, 'Syrup', ?)", (units, units))
    conn.commit()


def _offline_patches(stack: ExitStack) -> None:
    for p in (
        mock.patch("core.sync_prefs.is_online_mode", return_value=False),
        mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
        mock.patch("core.sync_coordinator.after_purchase_deleted", return_value=None),
        mock.patch("core.purchase_service.recalculate_supplier_due", return_value=None),
    ):
        stack.enter_context(p)


class TwoLinesOfOneBatch(unittest.TestCase):
    def test_are_checked_together(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        _purchase(conn, [10, 10], med_stock=15)
        with ExitStack() as stack:
            _offline_patches(stack)
            res = desktop_purchase_service.delete_saved_purchase(conn, {"purchase_id": 500})
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("code"), "insufficient_stock")
        self.assertEqual(conn.execute("SELECT stock_qty FROM medicines WHERE id=20").fetchone()[0], 15)


class OfflineFirstDelete(OfBase):
    def setUp(self):
        super().setUp()
        _purchase(self.conn, [10], med_stock=10)
        # Everything this PC did has reached the server already.
        self.conn.execute(
            "INSERT INTO of_events (seq, event_uuid, collection, op, payload, created_at, status) "
            "VALUES (1, 'pc-x', 'stock_operations', 'stock', '{}', 'now', 'sent')")
        self.conn.execute("UPDATE of_stock_journal SET event_seq=1")
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        _offline_patches(self.stack)
        self.stack.enter_context(mock.patch("core.offline_first.runtime.is_active", return_value=True))
        self.stack.enter_context(mock.patch.object(worker, "current", return_value=None))

    def delete(self, server):
        with mock.patch("core.offline_first.client.stock", side_effect=server) as asked:
            res = desktop_purchase_service.delete_saved_purchase(self.conn, {"purchase_id": 500})
        return res, asked

    def stock(self):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=20").fetchone()[0]

    def test_a_sale_on_another_device_not_pulled_yet_stops_the_delete(self):
        res, asked = self.delete(lambda ids, timeout: {"stock": [[20, 4, 0]]})
        self.assertFalse(res.get("ok"), res)
        self.assertEqual(res.get("code"), "insufficient_stock")
        self.assertIn("another device", res["error"])
        self.assertEqual(self.stock(), 10)
        self.assertEqual(asked.call_args.args[0], [20])
        self.assertLessEqual(asked.call_args.kwargs["timeout"], 5.0)

    def test_nothing_sold_elsewhere_the_delete_goes_ahead(self):
        res, _ = self.delete(lambda ids, timeout: {"stock": [[20, 10, 0]]})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stock(), 0)

    def test_this_pcs_own_unsent_movements_count(self):
        # 6 more came in here (not sent yet) and the phone sold 6 (on the server, not pulled):
        # server 4 + this PC's 6 waiting = 10 after the sync -- the 10 can be taken back.
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty + 6 WHERE id=20")
        self.conn.commit()
        res, _ = self.delete(lambda ids, timeout: {"stock": [[20, 4, 0]]})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stock(), 6)

    def test_no_internet_the_local_check_stands(self):
        def down(ids, timeout):
            raise OSError("no route to host")

        res, _ = self.delete(down)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stock(), 0)

    def test_a_sync_that_just_failed_is_not_waited_on(self):
        w = mock.Mock(last={"error": "offline"})
        with mock.patch.object(worker, "current", return_value=w):
            res, asked = self.delete(lambda ids, timeout: {"stock": [[20, 0, 0]]})
        self.assertTrue(res.get("ok"), res)
        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
