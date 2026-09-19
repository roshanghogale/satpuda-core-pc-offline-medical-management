"""A bill whose row waits in the queue is one row in Sales History, however often it is edited.

When the server has a sale's balance and stock but not its bill row, the row waits in the
queue and every change to the bill is queued as an edit of the sale's server id. Each edit
took a fresh client_uuid, so none replaced the one before, and Sales History drew every
queued edit as a bill of its own: one waiting bill and three autosave ticks showed as four
bills with different totals. A shop deleting what looked like a duplicate deleted the only
real bill. The server ended up right; the screen did not.

Now the edits of one waiting bill replace each other in the queue, and Sales History shows
the bill once, under its own queue id, with the latest edit's figures. A bill the shop has
already deleted is not shown again.

Everything talks to the in-memory fake server and a temp queue file from
test_a_partly_saved_bill_is_posted_once; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import billing_service, db_setup, desktop_sales_service  # noqa: E402
from core import desktop_pages_service as pages  # noqa: E402
from core import online_mutation_queue as queue  # noqa: E402
from core import store_query_client as sq  # noqa: E402
from tests.test_a_partly_saved_bill_is_posted_once import LINE, _Counter  # noqa: E402


class _WaitingBill(_Counter):
    def waiting_bill(self) -> int:
        # The first number is taken and the connection drops on the retry: the balance and
        # the stock are on the server, only the bill row waits.
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        bill_no, temp_id = self.save()
        self.assertEqual(bill_no, "PENDING")
        return temp_id

    def edit(self, temp_id, qty, cash):
        billing_service.update_existing_bill(
            None, temp_id, [dict(LINE, qty=qty, amount=10.0 * qty)], 0, 0, cash, 0.0,
            "RAM", "", "", 0, bill_date="2026-09-13",
        )

    def history_rows(self):
        return queue.merge_server_rows([], queue.overlay_sales_dicts(), collection="sales")


class OneWaitingBillIsOneRow(_WaitingBill):
    def test_three_edits_show_as_one_bill_with_the_latest_figures(self):
        temp_id = self.waiting_bill()
        for qty, cash in ((1, 0.0), (2, 0.0), (2, 20.0)):
            self.edit(temp_id, qty, cash)

        rows = self.history_rows()
        self.assertEqual(
            [(r["id"], r["total_amount"], r["amount_paid"]) for r in rows],
            [(temp_id, 20.0, 20.0)],
            "one waiting bill was listed once per queued edit",
        )
        pending = [r for r in self.queue() if r["status"] == "pending"]
        self.assertEqual([(r["op"], r["local_id"] > 0) for r in pending],
                         [("upsert", False), ("upsert", True)],
                         "each edit of the waiting bill stayed in the queue as its own row")

        self.server.network_back()
        self.flush()
        self.assertEqual(list(self.server.sales), [5001])
        self.assertPostedOnce(due=0.0, stock=8.0)
        self.assertEqual(self.history_rows(), [])

    def test_a_deleted_waiting_bill_is_not_shown_again(self):
        temp_id = self.waiting_bill()
        self.edit(temp_id, 1, 0.0)
        with mock.patch.object(desktop_sales_service, "_forget_autosave_session_for"):
            res = desktop_sales_service.delete_saved_sale(None, {"sale_id": temp_id})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.history_rows(), [],
                         "the shop deleted the bill and Sales History still offered it")

    def test_the_sales_history_page_lists_it_once(self):
        temp_id = self.waiting_bill()
        for qty, cash in ((1, 0.0), (2, 0.0), (2, 20.0)):
            self.edit(temp_id, qty, cash)
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        empty = lambda *a, **k: {"rows": [], "total": 0}  # noqa: E731
        for name in dir(sq):
            if (name.startswith("list_") or name.endswith("_summary")) and callable(getattr(sq, name)):
                patch = mock.patch.object(sq, name, side_effect=empty)
                patch.start()
                self.addCleanup(patch.stop)
        patch = mock.patch.object(pages, "history_filter_choices", return_value={})
        patch.start()
        self.addCleanup(patch.stop)
        res = pages.list_sales_history(conn)
        ids = [int(i) for i in (res.get("row_ids") or [])]
        self.assertEqual(ids, [temp_id], res.get("rows"))


if __name__ == "__main__":
    unittest.main()
