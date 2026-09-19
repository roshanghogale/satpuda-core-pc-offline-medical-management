"""An edited supplier payment stores the balance as it really stood before and after it.

Editing a supplier payment Online worked out the balance before the payment as
``old_due + old_amount``. That ignores credit: store 4 SP14 was a Rs 100 payment on a supplier
who owed nothing (it made Rs 100 credit). Edited to Rs 40, the row stored due_before 100
although the supplier owed 0 before it. Settings -> Payments and the ledger show those
columns. The balance before the payment is today's net balance with this payment taken back
out: due - credit + old amount, and it only counts the old amount when the payment was this
supplier's.

Offline the edit read the supplier's due AFTER the old payment and subtracted the new amount
from it again, so the stored columns counted the old payment twice.

The totals themselves (supplier total_due / total_credit) were right and stay right.
The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_settings_service as svc  # noqa: E402


class _OnlineEdit(unittest.TestCase):
    def run_edit(self, *, due, credit, old_amount, amount, old_supplier=5):
        supplier = {"id": 5, "local_id": 5, "name": "JAIN PHARMA", "phone": "",
                    "total_due": due, "total_credit": credit}
        queued: list[dict] = []
        cached: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch("core.online_catalog.find_supplier_by_name", return_value=supplier),
                mock.patch("core.online_catalog.suppliers", return_value=[supplier]),
                mock.patch("core.online_catalog.patch_supplier_cache",
                           side_effect=lambda d: cached.append(dict(d))),
                mock.patch("core.server_crud.get_doc", return_value={
                    "id": 14, "amount": old_amount, "payment_no": "SP14",
                    "supplier_id": old_supplier}),
                mock.patch("core.online_mutation_queue.enqueue",
                           side_effect=lambda **kw: queued.append(kw["payload"]) or {}),
                mock.patch("core.online_mutation_queue.flush_now", return_value=True),
                mock.patch.object(svc, "repair_supplier_dues_online", return_value=0),
                mock.patch.object(svc, "_notify_payments_changed", return_value=None),
                mock.patch.object(svc, "get_payments", return_value={}),
            ):
                stack.enter_context(patch)
            out = svc.save_payment(sqlite3.connect(":memory:"), {
                "kind": "supplier", "party": "JAIN PHARMA", "amount": amount, "mode": "Cash",
                "date": "2026-09-14", "id": 14,
            })
        [payload] = queued
        return out, payload, cached[-1]


class AnOnlineEdit(_OnlineEdit):
    def test_an_overpayment_edited_down_stores_no_due_before_it(self):
        # SP14: Rs 100 on a supplier owing 0 -> credit 100. Edited to 40.
        out, payload, cache = self.run_edit(due=0, credit=100, old_amount=100, amount=40)
        self.assertEqual((payload["due_before"], payload["due_after"]), (0.0, 0.0))
        self.assertEqual((out["due_before"], out["due_after"]), (0.0, 0.0))
        self.assertEqual((cache["total_due"], cache["total_credit"]), (0.0, 40.0))

    def test_credit_left_after_the_old_payment_is_taken_off_the_due_before_it(self):
        # Owed 70, paid 100 -> credit 30. Edited to 60: owed 70 before it, 10 after.
        _out, payload, cache = self.run_edit(due=0, credit=30, old_amount=100, amount=60)
        self.assertEqual((payload["due_before"], payload["due_after"]), (70.0, 10.0))
        self.assertEqual((cache["total_due"], cache["total_credit"]), (10.0, 0.0))

    def test_a_due_left_after_the_old_payment(self):
        _out, payload, cache = self.run_edit(due=50, credit=0, old_amount=100, amount=120)
        self.assertEqual((payload["due_before"], payload["due_after"]), (150.0, 30.0))
        self.assertEqual((cache["total_due"], cache["total_credit"]), (30.0, 0.0))

    def test_a_payment_moved_from_another_supplier_is_not_added_back(self):
        _out, payload, cache = self.run_edit(due=50, credit=0, old_amount=100, amount=20,
                                             old_supplier=9)
        self.assertEqual((payload["due_before"], payload["due_after"]), (50.0, 30.0))
        self.assertEqual((cache["total_due"], cache["total_credit"]), (30.0, 0.0))

    def test_a_new_payment_is_unchanged(self):
        supplier = {"id": 5, "name": "JAIN PHARMA", "total_due": 0, "total_credit": 30}
        queued: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch("core.online_catalog.find_supplier_by_name", return_value=supplier),
                mock.patch("core.online_catalog.suppliers", return_value=[supplier]),
                mock.patch("core.online_catalog.patch_supplier_cache", return_value=None),
                mock.patch("core.server_crud.allocate_id", return_value=15),
                mock.patch("core.server_crud._now", return_value="2026-09-14T05:00:00.000Z"),
                mock.patch("core.online_mutation_queue.enqueue",
                           side_effect=lambda **kw: queued.append(kw["payload"]) or {}),
                mock.patch("core.online_mutation_queue.flush_now", return_value=True),
                mock.patch.object(svc, "repair_supplier_dues_online", return_value=0),
                mock.patch.object(svc, "_notify_payments_changed", return_value=None),
                mock.patch.object(svc, "get_payments", return_value={}),
            ):
                stack.enter_context(patch)
            svc.save_payment(sqlite3.connect(":memory:"), {
                "kind": "supplier", "party": "JAIN PHARMA", "amount": 20, "mode": "Cash",
                "date": "2026-09-14"})
        [payload] = queued
        self.assertEqual((payload["due_before"], payload["due_after"]), (0.0, 0.0))
        self.assertEqual(payload["_suppliers"][0]["total_credit"], 50.0)


class AnOfflineEdit(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        conn.execute(
            "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, total_amount, "
            "final_amount, amount_paid, amount_paid_at_entry, due, total_due, due_amount) "
            "VALUES (1, '1/FY2026-27', 1, '2026-09-01', 150, 150, 0, 0, 150, 150, 150)")
        conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(svc, "_notify_payments_changed", return_value=None))

    def stored(self, pid):
        return self.conn.execute(
            "SELECT amount, due_before, due_after FROM supplier_payments WHERE id=?", (pid,)
        ).fetchone()

    def test_a_new_payment_then_its_edit(self):
        out = svc.save_payment(self.conn, {"kind": "supplier", "party": "SHREE PHARMA",
                                           "amount": 100, "mode": "Cash", "date": "2026-09-14"})
        pid = int(out["payment_id"])
        self.assertEqual(self.stored(pid), (100.0, 150.0, 50.0))
        svc.save_payment(self.conn, {"kind": "supplier", "party": "SHREE PHARMA", "amount": 120,
                                     "mode": "Cash", "date": "2026-09-14", "id": pid})
        self.assertEqual(self.stored(pid), (120.0, 150.0, 30.0))
        due = self.conn.execute("SELECT total_due FROM suppliers WHERE id=1").fetchone()[0]
        self.assertAlmostEqual(float(due), 30.0, places=2)


if __name__ == "__main__":
    unittest.main()
