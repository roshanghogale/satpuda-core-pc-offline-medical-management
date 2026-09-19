"""A bill number another device took first is replaced, not lost.

The server hands out "highest live number + 1" without holding it, so two devices saving at
the same moment get the same number and the second bill row is refused (the unique index on
bill_no). The rest of that bundle -- the customer's balance and the stock -- commits under its
own savepoint regardless. The direct save showed the counter "Server rejected...", and the
queue replayed the whole sale forever: a fresh number each time and the customer's balance
sent again. Now only the refused bill row goes again, under the next free number, and any
other refusal is parked instead of replayed. Everything is patched; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

from core import billing_service, online_mutation_queue, server_crud  # noqa: E402

TAKEN = {
    "collection": "sales",
    "id": 5001,
    "error": 'duplicate key value violates unique constraint "sales_store_pk_bill_no_key"',
    "constraint": "sales_store_pk_bill_no_key",
}


def _taken(message="Server rejected sales/5001"):
    return server_crud.BundleDocumentRejected(message, [dict(TAKEN)])


class TheRefusalSaysWhatWasRefused(unittest.TestCase):
    def test_a_failed_document_carries_its_collection_and_constraint(self):
        answer = {
            "customers": {"results": [{"id": 7, "status": "upserted"}]},
            "sales": {"results": [{"id": 5001, "status": "failed", "error": TAKEN["error"],
                                   "constraint": TAKEN["constraint"]}]},
        }
        with mock.patch.object(server_crud, "_token", return_value="t"), \
                mock.patch("core.server_api.push_bundle", return_value=answer):
            with self.assertRaises(server_crud.BundleDocumentRejected) as caught:
                server_crud.push_bundle({"sales": [{}], "customers": [{}]})
        exc = caught.exception
        self.assertEqual([f["collection"] for f in exc.failures], ["sales"])
        self.assertTrue(exc.only_number_taken("sales"))
        self.assertFalse(exc.only_number_taken("purchases"))

    def test_anything_else_is_not_a_taken_number(self):
        other = server_crud.BundleDocumentRejected(
            "x", [{"collection": "sales", "id": 1, "error": "value too long", "constraint": None}]
        )
        self.assertFalse(other.only_number_taken("sales"))
        self.assertFalse(server_crud.BundleDocumentRejected("x").only_number_taken("sales"))


class ANewOnlineSaleWhoseNumberWasTaken(unittest.TestCase):
    def save(self, push, allocated=None):
        allocate = mock.Mock(return_value=allocated or {
            "fy_start_year": 2026, "fy_serial": 7, "bill_no": "SCB7/FY2026-27"})
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
                mock.patch.object(server_crud, "_token", return_value="t"),
                mock.patch.object(server_crud, "allocate_ids_map", return_value={"sales": 5001}),
                mock.patch.object(server_crud, "allocate_id", return_value=5001),
                mock.patch.object(server_crud, "get_doc", side_effect=lambda coll, i: (
                    {"id": int(i), "name": "MED", "stock_qty": 10})),
                mock.patch("core.online_catalog.medicine_by_id",
                           side_effect=lambda i: {"id": int(i), "name": "MED", "stock_qty": 10}),
                mock.patch("core.online_catalog.find_customer_by_id",
                           return_value={"id": 7, "name": "RAM", "total_due": 0, "total_credit": 0}),
                mock.patch("core.online_catalog.patch_docs", return_value=None),
                mock.patch.object(server_crud, "push_bundle", side_effect=push),
                mock.patch("core.server_api.allocate_fy", allocate),
            ):
                stack.enter_context(patch)
            result = server_crud.save_new_sale_online(
                customer_id=7,
                medicines=[{"id": 1, "name": "MED", "qty": 1, "rate": 10.0, "amount": 10.0}],
                discount_pct=0, rounding=0, cash_paid=10.0, online_paid=0,
                doctor_name="", doctor_phone="", previous_due=0, bill_date=date(2026, 9, 13),
            )
        return result, allocate

    def test_only_the_bill_row_goes_again_under_the_next_number(self):
        pushed = []

        def push(bundle):
            pushed.append(bundle)
            if len(pushed) == 1:
                raise _taken()
            return {}

        (display, sale_id), allocate = self.save(push)
        self.assertEqual(len(pushed), 2)
        self.assertEqual(set(pushed[1]), {"sales"}, "the customer and the stock went twice")
        sale = pushed[1]["sales"][0]
        # The allocator answered 7 again (it counts live bills only): never retry a number.
        self.assertEqual((sale["bill_no"], sale["fy_serial"], sale["fy_start_year"]),
                         ("SCB8/FY2026-27", 8, 2026))
        self.assertEqual((display, sale_id), ("SCB8", 5001))
        self.assertEqual(sale["client_uuid"], pushed[0]["sales"][0]["client_uuid"])

    def test_any_other_refusal_is_raised_without_a_retry(self):
        pushed = []

        def push(bundle):
            pushed.append(bundle)
            raise server_crud.BundleDocumentRejected(
                "x", [{"collection": "customers", "id": 7, "error": "boom", "constraint": None}])

        with self.assertRaises(server_crud.BundleDocumentRejected):
            self.save(push)
        self.assertEqual(len(pushed), 1)

    def test_it_gives_up_after_three_more_taken_numbers(self):
        pushed = []

        def push(bundle):
            pushed.append(bundle)
            raise _taken()

        with self.assertRaises(server_crud.BundleDocumentRejected):
            self.save(push)
        self.assertEqual(
            [b["sales"][0]["bill_no"] for b in pushed],
            ["SCB7/FY2026-27", "SCB8/FY2026-27", "SCB9/FY2026-27", "SCB10/FY2026-27"],
        )


class AnOnlineEditMovedIntoANumberAnotherDeviceTook(unittest.TestCase):
    EXISTING = {
        "id": 42, "local_id": 42, "client_uuid": "cu-42", "version": 3, "customer_id": 0,
        "bill_no": "SCB150/FY2025-26", "bill_date": "2026-03-31",
        "fy_start_year": 2025, "fy_serial": 150, "items": [],
    }

    def edit(self, push, bill_date="2026-04-02"):
        with mock.patch("core.server_crud.push_bundle", side_effect=push), \
                mock.patch("core.server_crud.get_doc", side_effect=lambda coll, i: (
                    dict(self.EXISTING) if coll == "sales" else {"id": int(i), "stock_qty": 10})), \
                mock.patch("core.online_catalog.medicine_by_id",
                           side_effect=lambda i: {"id": int(i), "name": "MED", "stock_qty": 10}), \
                mock.patch("core.online_catalog.find_customer_by_id", return_value={}), \
                mock.patch("core.online_catalog.patch_docs", return_value=None), \
                mock.patch("core.server_crud._token", return_value="t"), \
                mock.patch("core.server_api.store_token_for_active", return_value="t"), \
                mock.patch("core.server_api.allocate_fy", return_value={
                    "fy_start_year": 2026, "fy_serial": 4, "bill_no": "SCB4/FY2026-27"}):
            billing_service.update_existing_bill_online_now(
                sale_id=42,
                medicines=[{"id": 1, "name": "MED", "qty": 1, "rate": 10.0, "amount": 10.0}],
                discount_pct=0, rounding=0, cash_paid=10.0, online_paid=0,
                customer_name="RAM", customer_phone="", doctor_name="", previous_due=0,
                bill_date=bill_date,
            )

    def test_the_bill_goes_again_alone_under_the_next_number(self):
        pushed = []

        def push(bundle):
            pushed.append(bundle)
            if len(pushed) == 1:
                raise _taken("Server rejected sales/42")
            return {}

        self.edit(push)
        self.assertEqual(len(pushed), 2)
        self.assertEqual(set(pushed[1]), {"sales"})
        sale = pushed[1]["sales"][0]
        self.assertEqual((sale["bill_no"], sale["fy_serial"]), ("SCB5/FY2026-27", 5))

    def test_an_edit_that_kept_its_number_is_not_renumbered_on_a_clash(self):
        pushed = []

        def push(bundle):
            pushed.append(bundle)
            raise _taken("Server rejected sales/42")

        with self.assertRaises(server_crud.BundleDocumentRejected):
            self.edit(push, bill_date="2026-03-30")
        self.assertEqual(len(pushed), 1)


class AnOnlinePurchaseTheServerRenumbered(unittest.TestCase):
    """Purchases: the server itself moves a taken number on insert and says so."""

    def test_the_number_the_server_stored_is_the_one_kept(self):
        answer = {"purchases": {"results": [
            {"id": 77, "status": "upserted", "purchase_no": "13/FY2026-27"}]}}
        patched: list = []
        with mock.patch("core.online_guard.ensure_can_mutate", return_value=None), \
                mock.patch.object(server_crud, "push_bundle", return_value=answer), \
                mock.patch("core.online_catalog.patch_docs",
                           side_effect=lambda coll, docs: patched.append((coll, docs))), \
                mock.patch("core.sync_status.note_collection_change", return_value=None), \
                mock.patch("core.sync_status.note_last_sync", return_value=None), \
                mock.patch("core.sync_v3.data_change_bus.emit", return_value=None), \
                mock.patch("core.store_live_refresh.emit", return_value=None):
            pid = server_crud.save_new_purchase_online({
                "id": 77, "purchase_no": "12/FY2026-27", "fy_serial": 12, "fy_start_year": 2026,
                "purchase_date": "2026-09-13", "items": [],
            })
        self.assertEqual(pid, 77)
        doc = next(docs[0] for coll, docs in patched if coll == "purchases")
        self.assertEqual((doc["purchase_no"], doc["fy_serial"], doc["fy_start_year"]),
                         ("13/FY2026-27", 13, 2026))


class TheQueueParksARefusedDocument(unittest.TestCase):
    def test_a_refused_document_is_parked_not_replayed(self):
        self.assertTrue(online_mutation_queue._is_permanent_refusal(_taken()))
        self.assertFalse(online_mutation_queue._is_permanent_refusal(RuntimeError("timed out")))


if __name__ == "__main__":
    unittest.main()
