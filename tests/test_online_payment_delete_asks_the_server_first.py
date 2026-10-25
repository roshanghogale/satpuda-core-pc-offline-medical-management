"""Online mode: deleting a customer / supplier payment asks the server first (audit, 25 Oct 2026).

If the server refuses the delete, nothing else happens: the party's balance is not pushed, and
the shop sees the error -- the PC and the server never disagree about a payment that is still
there. Only after the server has deleted it is the party's balance written.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import desktop_settings_service as svc


class OnlineDelete(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.conn = sqlite3.connect(":memory:")
        patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc", side_effect=self._get_doc),
            mock.patch("core.server_crud.upsert_contact_online",
                       side_effect=lambda col, doc: self.calls.append(("push", col, doc["id"]))),
            mock.patch("core.online_catalog.find_customer_by_id",
                       return_value={"id": 7, "name": "RAM", "total_due": 0, "total_credit": 0}),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 9, "name": "AGENCY", "total_due": 0, "total_credit": 0}),
            mock.patch("core.online_catalog.invalidate"),
            mock.patch.object(svc, "get_payments", return_value={"ok": True, "history": []}),
            mock.patch.object(svc, "_notify_payments_changed"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _get_doc(self, col, lid):
        if col == "customer_payments":
            return {"id": lid, "customer_id": 7, "amount": 50}
        if col == "supplier_payments":
            return {"id": lid, "supplier_id": 9, "amount": 70}
        return {}

    def test_a_refused_delete_changes_nothing(self):
        def refuse(col, lid):
            self.calls.append(("delete", col, lid))
            raise RuntimeError("Delete customer_payments/11 failed")

        with mock.patch("core.server_crud.delete_entity", side_effect=refuse):
            with self.assertRaises(RuntimeError):
                svc.delete_payment(self.conn, {"kind": "customer", "id": 11})
            with self.assertRaises(RuntimeError):
                svc.delete_payment(self.conn, {"kind": "supplier", "id": 12})
        self.assertEqual(self.calls, [("delete", "customer_payments", 11), ("delete", "supplier_payments", 12)])

    def test_the_server_deletes_first_then_the_balance_follows(self):
        with mock.patch("core.server_crud.delete_entity",
                        side_effect=lambda col, lid: self.calls.append(("delete", col, lid)) or {}):
            svc.delete_payment(self.conn, {"kind": "customer", "id": 11})
            svc.delete_payment(self.conn, {"kind": "supplier", "id": 12})
        self.assertEqual(self.calls, [("delete", "customer_payments", 11), ("push", "customers", 7),
                                      ("delete", "supplier_payments", 12), ("push", "suppliers", 9)])


if __name__ == "__main__":
    unittest.main()
