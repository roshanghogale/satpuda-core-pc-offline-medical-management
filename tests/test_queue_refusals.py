"""A change the server REFUSES is not a change that failed.

A purchase delete sat in the queue for 18 attempts against
"Cannot delete purchase - X has only 0 in stock", while the shop had been told
the bill was gone and went on seeing the supplier due it should have cleared.
Retrying a business-rule refusal can never succeed; it only hides it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _HttpError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}: server said so")


class PermanentRefusalTests(unittest.TestCase):
    def setUp(self):
        import core.online_mutation_queue as q

        self.q = q
        self.tmp = tempfile.mkdtemp()
        self._path = q._queue_path
        self._kick = q.kick_flush
        q._queue_path = lambda: os.path.join(self.tmp, "q.json")
        q.kick_flush = lambda: None

    def tearDown(self):
        self.q._queue_path = self._path
        self.q.kick_flush = self._kick

    def test_a_refusal_is_told_apart_from_a_failure(self):
        for status in (400, 409, 422):
            self.assertTrue(
                self.q._is_permanent_refusal(_HttpError(status)), status
            )
        # 401/403 are an expiring token, not a refusal -- the client re-pairs
        # and retries, so parking those would strand ordinary writes.
        for status in (401, 403, 500, 502, 503):
            self.assertFalse(
                self.q._is_permanent_refusal(_HttpError(status)), status
            )
        self.assertFalse(self.q._is_permanent_refusal(ConnectionError("no network")))

    def _seed(self):
        self.q._save([
            {"id": "a", "status": "pending", "collection": "purchases",
             "op": "delete", "local_id": 103},
            {"id": "b", "status": "pending", "collection": "sales",
             "op": "upsert", "local_id": 7},
        ])

    def test_a_refused_change_stops_being_retried(self):
        self._seed()
        self.q._mark_blocked("a", "HTTP 409: Cannot delete purchase")
        self.assertEqual([r["id"] for r in self.q.pending_rows()], ["b"])

    def test_the_reason_is_kept_for_the_shop_to_read(self):
        self._seed()
        self.q._mark_blocked("a", "HTTP 409: Cannot delete purchase - only 0 in stock")
        blocked = self.q.blocked_rows()
        self.assertEqual(len(blocked), 1)
        self.assertIn("only 0 in stock", blocked[0]["last_error"])

    def test_health_reports_a_refusal_separately_from_a_backlog(self):
        self._seed()
        self.q._mark_blocked("a", "HTTP 409: refused")
        h = self.q.queue_health()
        self.assertEqual(h["pending"], 1)
        self.assertEqual(h["blocked"], 1)
        self.assertTrue(h["warn"])
        self.assertEqual(h["blocked_detail"][0]["collection"], "purchases")
        self.assertEqual(h["blocked_detail"][0]["op"], "delete")

    def test_a_refusal_can_be_retried_once_the_cause_is_fixed(self):
        self._seed()
        self.q._mark_blocked("a", "HTTP 409: refused")
        self.assertEqual(self.q.retry_blocked(), 1)
        self.assertEqual(
            sorted(r["id"] for r in self.q.pending_rows()), ["a", "b"]
        )

    def test_a_refusal_can_be_abandoned(self):
        self._seed()
        self.q._mark_blocked("a", "HTTP 409: refused")
        self.assertEqual(self.q.discard_blocked(), 1)
        self.assertEqual([r["id"] for r in self.q._load()], ["b"])

    def test_abandoning_one_leaves_the_others(self):
        self._seed()
        self.q._save(self.q._load() + [
            {"id": "c", "status": "pending", "collection": "sales",
             "op": "delete", "local_id": 9},
        ])
        self.q._mark_blocked("a", "HTTP 409")
        self.q._mark_blocked("c", "HTTP 409")
        self.assertEqual(self.q.discard_blocked("a"), 1)
        self.assertEqual(len(self.q.blocked_rows()), 1)


class BalanceAfterDeleteTests(unittest.TestCase):
    """Deleting a bill must take it off the party's account.

    The server recomputes a balance when a purchase or payment is upserted, but
    its delete path has no such cascade -- so a deleted bill kept showing as due.
    """

    def _rebuild(self, purchases, payments, returns_):
        # The arithmetic the online rebuild performs, isolated.
        purchased = sum(p["final_amount"] for p in purchases if not p.get("deleted"))
        entry_paid = sum(
            p.get("amount_paid_at_entry", 0) for p in purchases if not p.get("deleted")
        )
        paid = sum(x["amount"] for x in payments if not x.get("deleted"))
        refunds = sum(r["refund_amount"] for r in returns_ if not r.get("deleted"))
        net = round(purchased - entry_paid - paid - refunds, 2)
        return max(0.0, net), max(0.0, -net)

    def test_one_bill_one_supplier(self):
        self.assertEqual(
            self._rebuild([{"final_amount": 1000.0}], [], []), (1000.0, 0.0)
        )

    def test_deleting_the_only_bill_clears_the_due(self):
        self.assertEqual(
            self._rebuild([{"final_amount": 1000.0, "deleted": True}], [], []),
            (0.0, 0.0),
        )

    def test_a_payment_beyond_the_bills_becomes_credit(self):
        self.assertEqual(
            self._rebuild(
                [{"final_amount": 1000.0}], [{"amount": 1200.0}], []
            ),
            (0.0, 200.0),
        )

    def test_a_return_reduces_the_due(self):
        self.assertEqual(
            self._rebuild(
                [{"final_amount": 1000.0}], [], [{"refund_amount": 300.0}]
            ),
            (700.0, 0.0),
        )

    def test_paid_at_entry_is_not_counted_twice(self):
        self.assertEqual(
            self._rebuild(
                [{"final_amount": 1000.0, "amount_paid_at_entry": 400.0}],
                [{"amount": 100.0}],
                [],
            ),
            (500.0, 0.0),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
