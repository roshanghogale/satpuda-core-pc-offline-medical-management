"""An Online customer receipt never writes a sale document.

After an Online receipt the PC worked the receipt down the customer's bills (FIFO) and pushed every
bill of the customer with its new due, each built from the sales list and bumped to version + 1
(desktop_settings_service._fifo_clear_customer_sales_online). That is the race the supplier side
lost (X1): another device's edit of one of those bills that lands at the same version with an
older stamp is overwritten by the stale copy, and an edit stamped before the push but arriving
after it is skipped.

The server does not need the PC's copy. upsertCustomerPayment runs
cascadeCustomerAfterLedgerChange in the same transaction as the receipt, and a receipt's delete
(softDeleteDoc) runs it too: every sale's total_due, due_amount, account_cleared and bill_cleared
are recomputed from the ledger (billed - paid at the counter - receipts - refunds, oldest bill
first) and only the bills whose figures changed get a new version. So the PC pushes no sale; it
refreshes the customer's own figure and pushes the customer row, as the supplier payment does.

Sales History and the customer ledger stay right after a part, full and over receipt, an edited
receipt and a deleted one. A fake store with the server's cascade stands in; nothing reaches a
server.
"""
from __future__ import annotations

import copy
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_pages_service as pages  # noqa: E402
from core import desktop_settings_service, online_mutation_queue  # noqa: E402

CID = 14
NAME = "A K PAWAR"


def r2(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def _sale(sid, no, day, total, paid, version=3):
    return {"id": sid, "local_id": sid, "bill_no": no, "bill_date": day, "customer_id": CID,
            "customer_name": NAME, "doctor_name": "", "total_amount": float(total),
            "amount_paid": float(paid), "cash_paid": float(paid), "online_paid": 0.0,
            "discount": 0.0, "credit_amount": 0.0, "previous_due": 0.0,
            "due_amount": float(total - paid), "total_due": float(total - paid),
            "account_cleared": total - paid <= 0.01, "bill_cleared": total - paid <= 0.01,
            "is_autosave": False, "deleted": False, "version": version,
            "updated_at": "2026-09-01T10:00:00.000Z", "client_uuid": f"cu-{sid}"}


class FakeStore:
    """The parts of server-live a customer receipt goes through."""

    def __init__(self):
        self.sales = {
            101: _sale(101, "SCB10/FY2026-27", "2026-08-01", 500, 0),
            102: _sale(102, "SCB11/FY2026-27", "2026-08-15", 300, 100),
            103: _sale(103, "SCB12/FY2026-27", "2026-09-01", 200, 0),
        }
        self.payments: dict[int, dict] = {}
        self.bundles: list[dict] = []
        self.sale_reads: list[int] = []
        self.clock = 0
        self.after_sales_listed = None

    def now(self) -> str:
        self.clock += 1
        return f"2026-09-14T06:00:{self.clock:02d}.000Z"

    # partyDueCascade.cascadeCustomerAfterLedgerChange
    def cascade(self):
        bills = sorted((s for s in self.sales.values()
                        if s["customer_id"] == CID and not s["deleted"] and not s["is_autosave"]),
                       key=lambda s: (s["bill_date"], s["local_id"]))
        pool = r2(sum(p["amount"] for p in self.payments.values()
                      if p["customer_id"] == CID and not p["deleted"]))
        for b in bills:
            pool = r2(pool + max(0.0, r2(b["amount_paid"] - b["total_amount"])))
        for b in bills:
            unpaid = max(0.0, r2(b["total_amount"] - b["amount_paid"]))
            if unpaid <= 0.01:
                rem, cleared = 0.0, True
            elif pool + 0.01 >= unpaid:
                pool, rem, cleared = r2(pool - unpaid), 0.0, True
            else:
                rem, pool = r2(unpaid - pool), 0.0
                cleared = rem <= 0.01
            if (abs(b["total_due"] - rem) > 0.009 or abs(b["due_amount"] - rem) > 0.009
                    or bool(b["account_cleared"]) != cleared or bool(b["bill_cleared"]) != (rem <= 0.01)):
                b.update(total_due=rem, due_amount=rem, account_cleared=cleared,
                         bill_cleared=rem <= 0.01, version=b["version"] + 1, updated_at=self.now())

    # syncService.upsertCustomerPayment / softDeleteDoc
    def upsert_payment(self, collection, doc):
        assert collection == "customer_payments"
        self.payments[int(doc["id"])] = dict(doc, amount=float(doc["amount"]), deleted=False)
        self.cascade()
        return int(doc["id"])

    def delete(self, collection, local_id):
        assert collection == "customer_payments"
        self.payments[int(local_id)]["deleted"] = True
        self.cascade()

    def push_bundle(self, bundle):
        self.bundles.append(copy.deepcopy(bundle))
        for doc in bundle.get("sales") or []:
            held = self.sales.get(int(doc["id"]))
            hv, dv = int(held["version"]), int(doc.get("version") or 1)
            if dv > hv or (dv == hv and str(doc.get("updated_at")) > str(held["updated_at"])):
                held.update({k: v for k, v in doc.items() if v is not None})
        return {}

    def get_doc(self, collection, local_id):
        if collection == "sales":
            self.sale_reads.append(int(local_id))
            return copy.deepcopy(self.sales.get(int(local_id)))
        if collection == "customer_payments":
            held = self.payments.get(int(local_id))
            return dict(held) if held else None
        return {"id": int(local_id), "local_id": int(local_id), "name": NAME, "phone": "",
                "version": 5, "total_due": 900.0, "total_credit": 0.0}

    def list_sales(self, **kw):
        rows = [copy.deepcopy(s) for s in self.sales.values() if not s["deleted"]]
        rows.sort(key=lambda s: (s["bill_date"], s["id"]), reverse=True)
        hook, self.after_sales_listed = self.after_sales_listed, None
        if hook:
            hook()
        return {"rows": rows, "total": len(rows), "filter_from": "2026-04-01",
                "filter_to": "2026-09-30", "default_fy_applied": False}

    def list_customer_payments(self, **kw):
        return {"rows": [dict(p) for p in self.payments.values() if not p["deleted"]]}


class _Receipts(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="receipt_fifo_")
        self.addCleanup(shutil.rmtree, folder, True)
        self.store = FakeStore()
        self.cache: list[dict] = []
        customer = {"id": CID, "local_id": CID, "name": NAME, "phone": "", "total_due": 900.0,
                    "total_credit": 0.0}
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.upsert_payment_online", side_effect=self.store.upsert_payment),
            mock.patch("core.server_crud.delete_entity", side_effect=self.store.delete),
            mock.patch("core.server_crud.push_bundle", side_effect=self.store.push_bundle),
            mock.patch("core.server_crud.get_doc", side_effect=self.store.get_doc),
            mock.patch("core.server_crud.upsert_contact_online", return_value=CID),
            mock.patch("core.server_crud._device_id", return_value="pc"),
            mock.patch("core.store_query_client.list_sales", side_effect=self.store.list_sales),
            mock.patch("core.store_query_client.list_customer_payments",
                       side_effect=self.store.list_customer_payments),
            mock.patch("core.store_query_client.list_sales_returns", return_value={"rows": []}),
            mock.patch("core.store_query_client.sales_summary", return_value={}),
            mock.patch("core.store_live_refresh.emit", return_value=None),
            mock.patch("core.online_catalog.patch_customer_cache",
                       side_effect=lambda doc: self.cache.append(dict(doc))),
            mock.patch("core.online_catalog.find_customer_by_id", return_value=dict(customer)),
            mock.patch("core.online_catalog.find_customer_by_name", return_value=dict(customer)),
            mock.patch("core.online_catalog.customers", return_value=[dict(customer)]),
            mock.patch("core.online_catalog.invalidate", return_value=None),
            mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_customer_payment_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_sales_return_dicts", return_value=[]),
            mock.patch.object(online_mutation_queue, "_queue_path",
                              return_value=os.path.join(folder, "store.json")),
            mock.patch.object(desktop_settings_service, "get_payments", return_value={}),
            mock.patch.object(desktop_settings_service, "_notify_payments_changed", return_value=None),
            mock.patch.object(pages, "history_filter_choices", return_value={}),
            mock.patch.object(pages, "_party_phone", return_value=""),
            mock.patch.object(pages, "_unfinished_sale_ids", return_value=set()),
        ):
            stack.enter_context(patch)

    def receipt(self, amount, pay_id=21):
        """The customer_payments row the Payments screen enqueues, flushed as the queue does."""
        online_mutation_queue._flush_one({
            "collection": "customer_payments", "op": "upsert", "local_id": pay_id,
            "payload": {"id": pay_id, "local_id": pay_id, "customer_id": CID, "customer_name": NAME,
                        "payment_date": "2026-09-14", "amount": float(amount),
                        "payment_mode": "cash", "cash_amount": float(amount),
                        "online_amount": 0.0, "reference_no": "", "note": "",
                        "_fifo": "customer",
                        "_customers": [{"id": CID, "local_id": CID, "name": NAME,
                                        "total_due": max(0.0, 900.0 - amount),
                                        "total_credit": max(0.0, amount - 900.0)}]},
        })

    def delete_receipt(self, pay_id=21):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        desktop_settings_service.delete_payment(conn, {"kind": "customer", "id": pay_id})

    def sale_pushes(self):
        return [b for b in self.store.bundles if b.get("sales")]

    def stored(self):
        return {sid: (r2(s["due_amount"]), bool(s["account_cleared"]))
                for sid, s in sorted(self.store.sales.items())}

    def history_due(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        out = pages.list_sales_history(conn, from_date="2026-04-01", to_date="2026-09-30")
        self.assertEqual(out.get("server_error"), "", out.get("server_error"))
        self.assertEqual(out["summary"]["bills"], 3)
        return round(float(out["summary"]["due"]), 2)

    def ledger_closing(self):
        led = desktop_settings_service._get_ledger_online("customer", NAME, "", "")
        return round(float(led["summary"]["closing"]), 2)


class AReceiptWritesNoSale(_Receipts):
    def test_no_sale_document_is_pushed(self):
        self.receipt(400)
        self.assertEqual(self.sale_pushes(), [], "a customer receipt pushed sale documents")

    def test_no_sale_is_read_back_to_be_pushed(self):
        self.receipt(400)
        self.assertEqual(self.store.sale_reads, [])

    def test_the_customer_row_is_pushed_with_the_ledger_figure(self):
        self.receipt(400)
        rows = [d for b in self.store.bundles for d in b.get("customers") or []]
        self.assertTrue(rows, "the customer row was not pushed")
        self.assertTrue(self.cache, "the customer's own figure was not refreshed")
        self.assertEqual((r2(self.cache[-1]["total_due"]), r2(self.cache[-1]["total_credit"])),
                         (500.0, 0.0))

    def test_another_devices_sale_edit_that_lands_meanwhile_is_not_overwritten(self):
        def phone_edits_sale_103():
            held = self.store.sales[103]
            held.update(total_amount=250.0, version=held["version"] + 1,
                        updated_at="2026-09-14T05:59:59.000Z", device_id="phone")

        self.store.after_sales_listed = phone_edits_sale_103
        self.receipt(400)
        self.assertEqual(self.store.sales[103]["total_amount"], 250.0,
                         "the receipt put back the stale bill total")
        self.assertEqual(self.store.sales[103]["device_id"], "phone")


class SalesHistoryAndLedgerAfterReceipts(_Receipts):
    def test_a_part_receipt(self):
        self.receipt(400)
        self.assertEqual(self.stored(), {101: (100.0, False), 102: (200.0, False), 103: (200.0, False)})
        self.assertEqual(self.history_due(), 500.0)
        self.assertEqual(self.ledger_closing(), 500.0)

    def test_a_full_receipt(self):
        self.receipt(900)
        self.assertEqual(self.stored(), {101: (0.0, True), 102: (0.0, True), 103: (0.0, True)})
        self.assertEqual(self.history_due(), 0.0)
        self.assertEqual(self.ledger_closing(), 0.0)
        self.assertEqual((r2(self.cache[-1]["total_due"]), r2(self.cache[-1]["total_credit"])),
                         (0.0, 0.0))

    def test_an_over_receipt_keeps_the_credit(self):
        self.receipt(1000)
        self.assertEqual(self.stored(), {101: (0.0, True), 102: (0.0, True), 103: (0.0, True)})
        self.assertEqual(self.history_due(), 0.0)
        self.assertEqual(self.ledger_closing(), -100.0)
        self.assertEqual((r2(self.cache[-1]["total_due"]), r2(self.cache[-1]["total_credit"])),
                         (0.0, 100.0))

    def test_an_edited_receipt(self):
        self.receipt(400)
        self.receipt(600)
        self.assertEqual(self.stored(), {101: (0.0, True), 102: (100.0, False), 103: (200.0, False)})
        self.assertEqual(self.history_due(), 300.0)
        self.assertEqual(self.ledger_closing(), 300.0)
        self.assertEqual(self.sale_pushes(), [])

    def test_a_deleted_receipt(self):
        self.receipt(400)
        self.delete_receipt()
        self.assertEqual(self.stored(), {101: (500.0, False), 102: (200.0, False), 103: (200.0, False)})
        self.assertEqual(self.history_due(), 900.0)
        self.assertEqual(self.ledger_closing(), 900.0)
        self.assertEqual(self.sale_pushes(), [])


if __name__ == "__main__":
    unittest.main()
