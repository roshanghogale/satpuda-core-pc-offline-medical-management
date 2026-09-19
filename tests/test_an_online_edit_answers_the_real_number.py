"""After an Online edit, the save answers with the bill's own number, not its internal id.

Online the engine runs on an empty ``:memory:`` SQLite. The edit branches of save_sale and
save_purchase_bill read the number back from that empty database, found nothing and answered
with the row id: the Sales page said "Bill 37635 saved." for SCB1378 and the Purchase page
"Purchase 3026 saved." for 111/FY2026-27. The number stored on the server was right; only the
answer was wrong. The answer now comes from the server's copy the edit works on.

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_purchase_service, desktop_sales_service, purchase_service


def _conn():
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    return conn


class ASaleEditOnline(unittest.TestCase):
    SALE = {"id": 37635, "local_id": 37635, "bill_no": "SCB1378/FY2026-27",
            "bill_date": "2026-09-14", "customer_id": 5, "customer_name": "RAM",
            "cash_paid": 5.0, "online_paid": 0.0, "version": 4,
            "items": [{"medicine_id": 17, "qty": 1, "rate": 5, "amount": 5}]}

    def save(self, sale_id, doc):
        seen: dict = {}

        def fake_update(conn, sid, medicines, **kw):
            seen["sale_id"] = sid
            seen.update(kw)

        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True),
                mock.patch("core.desktop_returns_service.edit_below_returned_error",
                           return_value=None),
                mock.patch("core.customer_service.get_or_create_customer", return_value=5),
                mock.patch("core.billing_service.update_existing_bill", side_effect=fake_update),
                mock.patch("core.server_crud.get_doc",
                           side_effect=lambda coll, lid, *a, **k: (
                               dict(doc) if coll == "sales" and doc and int(lid) == doc["id"]
                               else None)),
                mock.patch.object(desktop_sales_service, "_try_save_sale_pdf", return_value=None),
                mock.patch.object(desktop_sales_service, "_next_hint", return_value="SCB1379"),
                mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=""),
            ):
                stack.enter_context(patch)
            res = desktop_sales_service.save_sale(_conn(), {
                "items": [{"id": 17, "name": "PARA", "qty": 2, "rate": 3, "amount": 6}],
                "customer_name": "RAM", "payment_mode": "Cash", "cash_paid": 6,
                "editing_sale_id": sale_id, "edit_previous_due": 0, "bill_date": "2026-09-14",
            })
        return res, seen

    def test_the_answer_is_the_bill_number(self):
        res, seen = self.save(37635, self.SALE)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["bill_no"], "SCB1378")
        self.assertEqual(res["sale_id"], 37635)
        self.assertEqual(seen["sale_id"], 37635)

    def test_the_edit_itself_still_goes_to_the_same_bill(self):
        _res, seen = self.save(37635, self.SALE)
        self.assertEqual(seen.get("cash_paid"), 6.0)


class APurchaseEditOnline(unittest.TestCase):
    EXISTING = {"id": 3026, "local_id": 3026, "client_uuid": "p-3026",
                "purchase_no": "111/FY2026-27", "purchase_date": "2026-09-12", "version": 5,
                "supplier_id": 301, "fy_start_year": 2026, "fy_serial": 111,
                "items": [{"medicine_id": 17485, "name": "MOLICOLD TAB", "type": "Tablet",
                           "qty": 2, "free_qty": 1, "unit": "10", "tablets_per_stripe": 10,
                           "rate": 49.98, "batch_no": "TN118C"}]}

    def setUp(self):
        self.sent: list[dict] = []

        def get_doc(collection, local_id):
            if collection == "purchases":
                return dict(self.EXISTING) if int(local_id) == 3026 else None
            if collection == "medicines":
                return {"id": 17485, "name": "MOLICOLD TAB", "type": "Tablet", "unit": "10",
                        "batch_no": "TN118C", "stock_qty": 158, "version": 3}
            return {"id": int(local_id), "name": "STG SUP", "version": 1}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda mid: get_doc("medicines", mid)),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 301, "name": "STG SUP"}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog.patch_supplier_cache", return_value=None),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or int(doc.get("id") or 0)),
            mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                              return_value=None),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
            mock.patch.object(purchase_service, "get_or_create_supplier", return_value=301),
            mock.patch("core.desktop_returns_service.edit_below_returned_error",
                       return_value=None),
        ):
            stack.enter_context(patch)

    def test_the_answer_is_the_purchase_number(self):
        res = desktop_purchase_service.save_purchase_bill(_conn(), {
            "supplier_name": "STG SUP", "purchase_date": "2026-09-12",
            "editing_purchase_id": 3026, "edit_previous_due": 0, "edit_previous_credit": 0,
            "items": [{"medicine_id": 17485, "name": "MOLICOLD TAB", "type": "Tablet",
                       "batch": "TN118C", "expiry": "10/27", "qty": 3, "free_qty": 1,
                       "rate": 49.98, "mrp": 60, "gst_pct": 5, "unit": "10",
                       "tablets_per_stripe": 10, "quantity_value": "10"}],
        })
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["purchase_no"], "111")
        self.assertEqual(res["purchase_id"], 3026)
        [doc] = self.sent
        self.assertEqual(doc["purchase_no"], "111/FY2026-27")


if __name__ == "__main__":
    unittest.main()
