"""Online: deleting a sales bill reads the STORE's sales returns and payments (audit, 25 Oct 2026).

The return guard ("Delete the return first") and the payment guard ("A payment was taken
against this bill") read this PC's sqlite -- which Online is an empty shell -- so neither ever
fired at an Online shop. Now Online reads the store's returns to the bill's date and its
customer payments whose allocations name the bill, plus this PC's queued ones; a store that
cannot be read refuses the delete with nothing queued.

No server is reached: the store client and the queue are patched.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import desktop_returns_service as ret
from core import desktop_sales_service as svc
from core import online_mutation_queue as omq


class _Online(unittest.TestCase):
    store_returns: list[dict] = []
    store_payments: list[dict] = []
    pending: list[dict] = []
    returns_error: Exception | None = None
    payments_error: Exception | None = None

    def setUp(self):
        self.queued: list[dict] = []
        self.cancelled: list[dict] = []
        self.payment_calls: list[dict] = []
        self.conn = sqlite3.connect(":memory:")
        stack = ExitStack()
        self.addCleanup(stack.close)

        def _returns(collection, floor=""):
            self.assertEqual(collection, "sales_returns")
            if self.returns_error is not None:
                raise self.returns_error
            return [dict(r) for r in self.store_returns]

        def _payments(**kw):
            self.payment_calls.append(kw)
            if self.payments_error is not None:
                raise self.payments_error
            return {"rows": [dict(r) for r in self.store_payments]}

        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.server_crud.get_doc",
                       return_value={"id": 42, "bill_date": "2026-10-20"}),
            mock.patch("core.desktop_returns_service._list_returns_complete", side_effect=_returns),
            mock.patch("core.store_query_client.list_customer_payments", side_effect=_payments),
            mock.patch.object(omq, "pending_rows",
                              side_effect=lambda collection=None: [
                                  r for r in self.pending
                                  if collection is None or r["collection"] == collection]),
            mock.patch.object(omq, "enqueue",
                              side_effect=lambda **kw: self.queued.append(kw) or {"local_id": kw.get("local_id")}),
            mock.patch.object(omq, "cancel_matching",
                              side_effect=lambda **kw: self.cancelled.append(kw) or 1),
            mock.patch.object(svc, "_forget_autosave_session_for", return_value=None),
            mock.patch.object(omq, "queued_bill_row", return_value=None),
            mock.patch.object(omq, "bill_row_sale_id", return_value=0),
        ):
            stack.enter_context(p)

    def delete(self, sale_id: int = 42) -> dict:
        return svc.delete_saved_sale(self.conn, {"sale_id": sale_id})

    def assertNothingQueued(self):
        self.assertEqual(self.queued, [])
        self.assertEqual(self.cancelled, [])


class AStoreReturn(_Online):
    store_returns = [{"id": 5, "return_no": "SR5", "sale_id": 42, "return_date": "2026-10-21"}]

    def test_refused_with_the_sentence(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "This bill has sales return SR5. Delete the return first, then the bill.")
        self.assertNothingQueued()


class AQueuedReturn(_Online):
    pending = [{"collection": "sales_returns", "op": "upsert", "local_id": 77,
                "payload": {"sale_id": 42, "return_no": "SR77"}}]

    def test_a_return_still_in_the_queue_counts(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertIn("sales return SR77", res["error"])
        self.assertNothingQueued()


class AStorePaymentNamingTheBill(_Online):
    store_payments = [
        {"id": 9, "customer_id": 7, "payment_date": "2026-10-22", "amount": 50.0,
         "allocations": [{"bill_id": 42, "bill_no": "SCB42", "amount": 50.0}], "receipt_no": "R12"},
        {"id": 10, "customer_id": 7, "payment_date": "2026-10-22", "amount": 80.0,
         "allocations": [{"bill_id": 43, "amount": 80.0}]},
    ]

    def test_refused_naming_the_receipt(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "bill_has_payments")
        self.assertIn("receipt R12 of 22-10-2026", res["error"])
        self.assertNotIn("80.00", res["error"])
        self.assertNothingQueued()

    def test_the_payments_are_read_from_the_bill_s_date(self):
        self.delete()
        self.assertEqual(self.payment_calls[0]["from_date"], "2026-09-19")


class AQueuedPaymentNamingTheBill(_Online):
    pending = [{"collection": "customer_payments", "op": "upsert", "local_id": 88,
                "payload": {"payment_date": "2026-10-25", "amount": 30.0,
                            "allocations": [{"bill_id": 42, "amount": 30.0}]}}]

    def test_refused(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "bill_has_payments")


class NothingOnTheBill(_Online):
    store_returns = [
        {"id": 5, "return_no": "SR5", "sale_id": 41, "return_date": "2026-10-21"},
        {"id": 6, "return_no": "SR6", "sale_id": 42, "return_date": "2026-10-21", "deleted": True},
    ]
    store_payments = [{"id": 9, "payment_date": "2026-10-22", "amount": 50.0, "allocations": None,
                       "reference_no": "SCB42"}]

    def test_the_delete_is_queued(self):
        res = self.delete()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual([(q["collection"], q["op"]) for q in self.queued], [("sales", "delete")])


class ABillNotOnTheStoreYet(_Online):
    returns_error = ret.ReturnsUnreadable("would refuse if it were asked")

    def test_a_temporary_id_is_not_looked_up_on_the_store(self):
        res = self.delete(-1000000042)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.payment_calls, [])

    def test_a_queued_bill_row_is_not_looked_up_either(self):
        with mock.patch.object(omq, "queued_bill_row", return_value={"id": "row"}):
            res = self.delete()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.payment_calls, [])


class ReturnsUnreadable(_Online):
    returns_error = ret.ReturnsUnreadable("A store return has no readable date.")

    def test_refused_and_nothing_changes(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertIn("NOT deleted", res["error"])
        self.assertNothingQueued()


class PaymentsUnreadable(_Online):
    payments_error = OSError("no route to host")

    def test_refused_and_nothing_changes(self):
        res = self.delete()
        self.assertFalse(res["ok"])
        self.assertIn("could not be read", res["error"])
        self.assertNothingQueued()


class APaymentListThatNeverEnds(_Online):
    def test_a_full_page_is_never_the_end(self):
        full = [{"id": i, "payment_date": "2026-10-22", "amount": 1.0} for i in range(1, 5001)]
        with mock.patch("core.store_query_client.list_customer_payments",
                        return_value={"rows": full}):
            res = self.delete()
        self.assertFalse(res["ok"])
        self.assertIn("NOT deleted", res["error"])
        self.assertNothingQueued()


if __name__ == "__main__":
    unittest.main()
