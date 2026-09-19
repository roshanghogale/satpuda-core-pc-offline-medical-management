"""A sale sent again keeps the number the server stored, and new records say when they were made.

Two findings of the 2026-09-13 store audit:

  * SCB1251/FY2026-27 (store 4) is a hole. Its sale reached the server as SCB1251, the answer
    was lost, and the replay asked for a fresh number and stored the same sale again as
    SCB1252 (the server matched it by client_uuid). A failed send now remembers the id it gave
    the sale, and the replay first asks the server for that very sale (same id, same
    client_uuid). When it is there, the replay keeps that number and sends nothing more:
    the customer's balance and the stock went with it.
  * created_at was empty on every PC-made sale, purchase and payment: the PC never sent it.
    New sales, purchases, payments and returns now carry the time they were made, and a
    queued sale keeps the time it was made, not the time it was finally sent.

Everything talks to the in-memory fake server of test_a_partly_saved_bill_is_posted_once or to
patched clients; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest import mock

from core import desktop_returns_service, desktop_settings_service, purchase_service  # noqa: E402
from core import server_crud  # noqa: E402
from tests.test_a_partly_saved_bill_is_posted_once import _Counter  # noqa: E402

NEXT = {"fy_start_year": 2026, "fy_serial": 8, "bill_no": "SCB8/FY2026-27"}


def _is_time(value) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return bool(value)


class ASaleWhoseAnswerWasLost(_Counter):
    def lose_the_first_answer(self):
        real = self.server.push
        lost: list[bool] = []

        def push(bundle):
            out = real(bundle)
            if not lost:
                lost.append(True)
                raise OSError("timed out after the server had saved it")
            return out

        return mock.patch.object(server_crud, "push_bundle", side_effect=push)

    def test_the_replay_keeps_the_stored_number_and_sends_nothing_again(self):
        with self.lose_the_first_answer():
            bill_no, _temp_id = self.save()
        self.assertEqual(bill_no, "PENDING")
        self.assertEqual(self.server.sales[5001]["bill_no"], "SCB7/FY2026-27")

        self.server.allocated = dict(NEXT)  # SCB7 is live, so the next free number is 8
        self.flush()
        self.assertEqual([s["bill_no"] for s in self.server.sales.values()], ["SCB7/FY2026-27"],
                         "the replay stored the same sale under a second number")
        self.assertEqual(self.server.pushes, [], "a sale already on the server was sent again")
        self.assertPostedOnce()
        self.assertEqual(self.shown(), [])

    def test_a_queued_sale_whose_answer_is_lost_on_its_replay_keeps_its_number_too(self):
        self.server.pushes_down = True
        self.assertEqual(self.save()[0], "PENDING")
        self.server.network_back()
        with self.lose_the_first_answer():
            self.flush()
        self.assertEqual(self.server.sales[5001]["bill_no"], "SCB7/FY2026-27")
        self.server.allocated = dict(NEXT)
        self.flush()
        self.assertEqual([s["bill_no"] for s in self.server.sales.values()], ["SCB7/FY2026-27"])
        self.assertPostedOnce()
        self.assertEqual(self.shown(), [])


class TheNumberTheServerAnswersWith(_Counter):
    def test_is_the_number_the_bill_keeps(self):
        # The allocator says 8, but the server answers that it holds this sale as SCB7 (a
        # retried send of a sale it had stored). The bill is SCB7.
        self.server.allocated = dict(NEXT)
        answer = {"sales": {"results": [
            {"id": 5001, "status": "skipped", "bill_no": "SCB7/FY2026-27",
             "display_bill_no": "SCB7"},
        ]}}
        real = self.server.push
        with mock.patch.object(server_crud, "push_bundle",
                               side_effect=lambda bundle: (real(bundle), answer)[1]):
            bill_no, sale_id = self.save()
        self.assertEqual((bill_no, sale_id), ("SCB7", 5001))


class NewSalesSayWhenTheyWereMade(_Counter):
    def test_a_new_sale(self):
        self.save()
        [sale] = self.server.sales.values()
        self.assertTrue(_is_time(sale.get("created_at")), sale.get("created_at"))

    def test_a_queued_sale_keeps_the_time_it_was_made(self):
        self.server.pushes_down = True
        self.save()
        [row] = self.queue()
        made = row["payload"].get("created_at")
        self.assertTrue(_is_time(made), row["payload"])
        self.server.network_back()
        self.flush()
        [sale] = self.server.sales.values()
        self.assertEqual(sale.get("created_at"), made)


class ANewPurchase(unittest.TestCase):
    def test_carries_the_time_it_was_made(self):
        sent: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.server_api.store_token_for_active", return_value="t"),
                mock.patch("core.server_api.allocate_fy", return_value={
                    "fy_start_year": 2026, "fy_serial": 3, "purchase_no": "3/FY2026-27"}),
                mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                                  return_value=None),
                mock.patch("core.server_crud.allocate_id", return_value=900),
                mock.patch("core.online_catalog.medicine_by_id", return_value={
                    "id": 1, "name": "COUGH SYRUP", "stock_qty": 4, "version": 2}),
                mock.patch("core.server_crud.get_doc", return_value=None),
                mock.patch("core.online_catalog.find_supplier_by_id",
                           return_value={"id": 3, "name": "SHREE PHARMA"}),
                mock.patch("core.server_crud.save_new_purchase_online",
                           side_effect=lambda doc: sent.append(doc) or 900),
                mock.patch("core.server_crud._device_id", return_value="dev"),
                mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                                  return_value=0),
            ):
                stack.enter_context(patch)
            purchase_service.save_purchase_online_now(
                3, "2026-09-13", "INV-9",
                {"cash_paid": 0, "online_paid": 0, "amount_paid": 0, "total_amount": 20,
                 "final_amount": 20},
                [{"medicine_id": 1, "name": "COUGH SYRUP", "type": "Syrup", "qty": 2,
                  "free_qty": 0, "rate": 10, "batch": "S1", "expiry": "12/27", "unit": "100ML"}],
            )
        [doc] = sent
        self.assertTrue(_is_time(doc.get("created_at")), doc.get("created_at"))


class NewPayments(unittest.TestCase):
    def pay(self, data) -> dict:
        queued: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch("core.online_catalog.find_customer_by_name",
                           return_value={"id": 7, "name": "RAM", "total_due": 50}),
                mock.patch("core.online_catalog.find_supplier_by_name",
                           return_value={"id": 3, "name": "SHREE", "total_due": 500}),
                mock.patch("core.server_crud.allocate_id", return_value=77),
                mock.patch("core.server_crud.get_doc",
                           return_value={"id": 77, "amount": 20, "payment_no": "SP77"}),
                mock.patch("core.online_mutation_queue.enqueue",
                           side_effect=lambda **kw: queued.append(kw) or {"local_id": 77}),
                mock.patch("core.online_mutation_queue.flush_now", return_value=True),
                mock.patch("core.online_catalog.patch_customer_cache", return_value=None),
                mock.patch("core.online_catalog.patch_supplier_cache", return_value=None),
                mock.patch.object(desktop_settings_service, "_notify_payments_changed",
                                  return_value=None),
                mock.patch.object(desktop_settings_service, "get_payments", return_value={}),
                mock.patch.object(desktop_settings_service, "repair_supplier_dues_online",
                                  return_value=None),
            ):
                stack.enter_context(patch)
            desktop_settings_service.save_payment(None, data)
        return queued[0]["payload"]

    def test_a_new_customer_receipt(self):
        payload = self.pay({"kind": "customer", "party": "RAM", "cash": 20, "date": "2026-09-13"})
        self.assertTrue(_is_time(payload.get("created_at")), payload)

    def test_a_new_supplier_payment(self):
        payload = self.pay({"kind": "supplier", "party": "SHREE", "amount": 100, "mode": "Cash",
                            "date": "2026-09-13"})
        self.assertTrue(_is_time(payload.get("created_at")), payload)

    def test_an_edited_receipt_keeps_its_first_time(self):
        payload = self.pay({"kind": "customer", "party": "RAM", "cash": 30, "id": 77,
                            "date": "2026-09-13"})
        self.assertNotIn("created_at", payload)


class ANewSalesReturn(unittest.TestCase):
    def test_carries_the_time_it_was_made(self):
        queued: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch.object(desktop_returns_service, "_sales_return_request_error",
                                  return_value=""),
                mock.patch.object(desktop_returns_service, "_return_id_for", return_value=(-5, True)),
                mock.patch.object(desktop_returns_service, "_sales_return_stock_docs", return_value=[]),
                mock.patch.object(desktop_returns_service, "_notify_return_saved", return_value=None),
                mock.patch("core.online_catalog.find_customer_by_id",
                           return_value={"id": 7, "name": "RAM", "total_due": 0}),
                mock.patch("core.online_catalog.patch_docs", return_value=None),
                mock.patch("core.online_catalog.patch_customer_cache", return_value=None),
                mock.patch("core.online_mutation_queue.enqueue",
                           side_effect=lambda **kw: queued.append(kw) or {"local_id": -5}),
            ):
                stack.enter_context(patch)
            res = desktop_returns_service.save_sales_return(None, {
                "sale_id": 5001, "customer_id": 7, "discount": 0,
                "items": [{"medicine_id": 1, "name": "MED", "qty": 1, "rate": 10}],
            })
        self.assertTrue(res.get("ok"), res)
        [ret] = [q for q in queued if q["collection"] == "sales_returns"]
        self.assertTrue(_is_time(ret["payload"].get("created_at")), ret["payload"])


if __name__ == "__main__":
    unittest.main()
