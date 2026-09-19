"""Deleting a sales return must take the refund off the customer's account too.

A return settled in CASH writes two rows: the return itself, and a
customer_payments row of -payout recording the money that left the drawer,
tagged with the return number. Delete only the first and the shop's own ledger
re-charges the customer for a refund it already handed over.

Offline that was fixed a while ago. ONLINE it was not, and online is where the
shops actually are: the engine's connection is sqlite3.connect(":memory:"), so
the local lookup the offline branch does finds nothing, and the queue only ever
carried the sales_returns delete. The store server's softDeleteDoc deletes the
one row it is asked for and then recomputes the balance from what is LEFT --
which still includes the negative payment -- so the customer's due came back
inflated by the refund, every time.

The fix asks the server for the paired payment (get_doc for the return's number
and customer, then the customer_payments list around that date) and enqueues
its delete alongside. When the server cannot be asked, the caller is told so
rather than shown a clean result.
"""
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_returns_service as ret  # noqa: E402

TODAY = str(date.today())

RETURN_DOC = {
    "id": 41, "local_id": 41, "return_no": "SR41", "sale_id": 9,
    "customer_id": 7, "customer_name": "ZZ TEST CUSTOMER",
    "return_date": TODAY, "refund_amount": 400.0,
}

PAYMENT_ROWS = [
    # the refund this return paid out -- must go
    {"id": 88, "customer_id": 7, "reference_no": "SR41", "amount": -400.0,
     "payment_date": TODAY},
    # a real payment from the same customer -- must stay
    {"id": 89, "customer_id": 7, "reference_no": "", "amount": 250.0,
     "payment_date": TODAY},
    # another customer's refund with the same-looking number -- must stay
    {"id": 90, "customer_id": 8, "reference_no": "SR41", "amount": -400.0,
     "payment_date": TODAY},
    # this customer's refund for a DIFFERENT return -- must stay
    {"id": 91, "customer_id": 7, "reference_no": "SR40", "amount": -120.0,
     "payment_date": TODAY},
]


class _Base(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.queued = []
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            # The connectivity probe would otherwise refuse before the branch
            # under test is reached; the suite blocks real sockets.
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch(
                "core.online_mutation_queue.enqueue",
                side_effect=lambda **kw: self.queued.append(kw) or {"local_id": kw.get("local_id")},
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def _deletes(self, collection):
        return [
            int(q["payload"]["id"]) for q in self.queued
            if q.get("collection") == collection and q.get("op") == "delete"
        ]


class TheRefundGoesWithTheReturn(_Base):
    def setUp(self):
        super().setUp()
        self._patches += [
            mock.patch("core.server_crud.get_doc", return_value=RETURN_DOC),
            mock.patch(
                "core.store_query_client.list_customer_payments",
                return_value={"rows": list(PAYMENT_ROWS)},
            ),
        ]
        for p in self._patches[-2:]:
            p.start()

    def test_the_return_is_still_deleted(self):
        res = ret.delete_sales_return(self.conn, {"id": 41})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self._deletes("sales_returns"), [41])

    def test_the_paired_refund_payment_is_deleted_as_well(self):
        ret.delete_sales_return(self.conn, {"id": 41})
        self.assertEqual(
            self._deletes("customer_payments"), [88],
            "the refund row was left standing -- the customer is charged twice",
        )

    def test_nothing_else_on_the_ledger_is_touched(self):
        ret.delete_sales_return(self.conn, {"id": 41})
        killed = set(self._deletes("customer_payments"))
        self.assertNotIn(89, killed, "a real payment was deleted")
        self.assertNotIn(90, killed, "another customer's row was deleted")
        self.assertNotIn(91, killed, "a different return's refund was deleted")

    def test_the_result_is_not_flagged_as_unchecked(self):
        res = ret.delete_sales_return(self.conn, {"id": 41})
        self.assertNotIn("warning", res)

    def test_the_lookup_is_bounded_to_the_return_s_own_dates(self):
        with mock.patch(
            "core.store_query_client.list_customer_payments",
            return_value={"rows": list(PAYMENT_ROWS)},
        ) as spy:
            ret.delete_sales_return(self.conn, {"id": 41})
        kw = spy.call_args.kwargs
        day = date.fromisoformat(TODAY)
        self.assertEqual(kw["from_date"], str(day - timedelta(days=3)))
        self.assertEqual(kw["to_date"], str(day + timedelta(days=3)))


class AReturnSettledAsCreditHasNoRefundToDelete(_Base):
    def setUp(self):
        super().setUp()
        credit_only = dict(RETURN_DOC, return_no="SR42")
        self._patches += [
            mock.patch("core.server_crud.get_doc", return_value=credit_only),
            mock.patch(
                "core.store_query_client.list_customer_payments",
                return_value={"rows": list(PAYMENT_ROWS)},
            ),
        ]
        for p in self._patches[-2:]:
            p.start()

    def test_no_payment_delete_and_no_warning(self):
        res = ret.delete_sales_return(self.conn, {"id": 42})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self._deletes("customer_payments"), [])
        self.assertNotIn(
            "warning", res,
            "a credit-settled return has no refund row; that is an answer, not a failure",
        )


class WhenTheServerCannotBeAsked(_Base):
    def setUp(self):
        super().setUp()
        self._patches += [
            mock.patch("core.server_crud.get_doc", side_effect=OSError("no route to host")),
        ]
        self._patches[-1].start()

    def test_the_return_is_deleted_anyway(self):
        res = ret.delete_sales_return(self.conn, {"id": 41})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self._deletes("sales_returns"), [41])

    def test_but_the_shop_is_told_the_refund_was_not_checked(self):
        res = ret.delete_sales_return(self.conn, {"id": 41})
        self.assertIn(
            "warning", res,
            "a silent success here hides a refund still charged to the customer",
        )


if __name__ == "__main__":
    unittest.main()
