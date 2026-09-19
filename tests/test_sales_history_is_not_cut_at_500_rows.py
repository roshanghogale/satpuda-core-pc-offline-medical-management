"""Sales History lists every bill in its range, and says so when it cannot.

The page asks /api/sales/history with no row limit, so list_sales_history's default of 500
applied: a range from 2026-03-20 to today showed exactly 500 bills on stores 4 and 127, the
back-dated bills were not among them, and the summary (bills, totals, paid, due) was worked out
on those 500 with nothing on screen to say so.

Now the page reads the range page by page (Online: the server's 5000-row pages; Offline: one
query) up to a ceiling of 10,000 bills. The summary covers the bills listed; the server summary
is asked in parts, because thousands of ids do not fit in one request. When the range holds
more than the ceiling, ``rows_note`` says how many are listed of how many.

Home and Purchase History are not touched. The store query client is patched; nothing reaches
a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_pages_service as pages  # noqa: E402

FROM, TO = "2026-03-20", "2026-09-14"


def _server_sales(n):
    """n bills, newest first, the shape /api/store/sales answers with."""
    rows = []
    for i in range(n):
        day = 1 + (i % 28)
        month = 9 - (i // 2000) % 6
        rows.append({
            "id": 50000 - i, "bill_no": f"SCB{n - i}/FY2026-27",
            "bill_date": f"2026-{month:02d}-{day:02d}", "customer_id": 7,
            "customer_name": "RAM", "total_amount": 10.0, "amount_paid": 10.0,
            "due_amount": 0.0, "cash_paid": 10.0, "online_paid": 0.0, "discount": 0.0,
            "credit_amount": 0.0, "total_due": 0.0, "account_cleared": True,
            "bill_cleared": True, "previous_due": 0.0,
        })
    return rows


class _Online(unittest.TestCase):
    N = 1234

    def setUp(self):
        self.server = _server_sales(self.N)
        self.calls: list[dict] = []
        self.summary_calls: list[int] = []

        def list_sales(**kw):
            self.calls.append(dict(kw))
            limit = min(int(kw.get("limit") or 500), 5000)
            off = int(kw.get("offset") or 0)
            page = self.server[off: off + limit]
            total = len(self.server) if (len(page) >= limit and kw.get("include_total", True)) \
                else off + len(page)
            return {"rows": [dict(r) for r in page], "total": total,
                    "filter_from": FROM, "filter_to": TO, "default_fy_applied": False}

        def sales_summary(**kw):
            ids = list(kw.get("ids") or [])
            if len(ids) > 800:
                raise RuntimeError("HTTP 431 Request Header Fields Too Large")
            self.summary_calls.append(len(ids))
            return {"total_sales": 10.0 * len(ids), "total_discount": 0.0,
                    "total_profit": 1.0 * len(ids), "total_returns": 0.0,
                    "today_revenue": 0.0, "today_cash": 0.0, "today_online": 0.0,
                    "month_revenue": 0.0}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.store_query_client.list_sales", side_effect=list_sales),
            mock.patch("core.store_query_client.sales_summary", side_effect=sales_summary),
            mock.patch("core.store_query_client.list_customer_payments",
                       return_value={"rows": []}),
            mock.patch("core.store_query_client.list_sales_returns", return_value={"rows": []}),
            mock.patch.object(pages, "history_filter_choices", return_value={}),
            mock.patch.object(pages, "_party_phone", return_value=""),
            mock.patch.object(pages, "_unfinished_sale_ids", return_value=set()),
            mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_customer_payment_dicts",
                       return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_sales_return_dicts",
                       return_value=[]),
        ):
            stack.enter_context(patch)

    def history(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        return pages.list_sales_history(conn, from_date=FROM, to_date=TO)


class ARangeOfTwelveHundredBillsOnline(_Online):
    N = 1234

    def test_every_bill_is_listed_and_summed(self):
        out = self.history()
        self.assertEqual(out.get("server_error"), "")
        self.assertEqual(len(out["rows"]), 1234, "the list stopped short of the range")
        self.assertEqual(out["summary"]["bills"], 1234)
        self.assertAlmostEqual(out["summary"]["total"], 12340.0, places=2)
        self.assertAlmostEqual(out["summary"]["paid"], 12340.0, places=2)
        self.assertAlmostEqual(out["summary"]["profit"], 1234.0, places=2)
        self.assertFalse(out.get("rows_note"))

    def test_the_summary_is_asked_in_parts_that_fit_a_request(self):
        self.history()
        self.assertEqual(sum(self.summary_calls), 1234)
        self.assertTrue(all(n <= 800 for n in self.summary_calls), self.summary_calls)


class ARangeLargerThanTheCeilingOnline(_Online):
    N = 12345

    def test_the_page_lists_the_ceiling_and_says_how_many_there_are(self):
        out = self.history()
        self.assertEqual(len(out["rows"]), pages.SALES_HISTORY_ROW_CEILING)
        self.assertEqual(out["summary"]["bills"], pages.SALES_HISTORY_ROW_CEILING)
        note = out.get("rows_note") or ""
        self.assertIn("10,000", note)
        self.assertIn("12,345", note)
        self.assertTrue(all(int(c.get("limit") or 0) <= 5000 for c in self.calls), self.calls)


class ASmallRangeOnlineAsksWhatItAlwaysAsked(_Online):
    N = 3

    def test_one_request_for_the_rows_and_one_for_the_summary(self):
        out = self.history()
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("offset", self.calls[0])
        self.assertEqual(self.summary_calls, [3])


class ARangeOfSixHundredBillsOffline(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
        conn.executemany(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, amount_paid, "
            "cash_paid, due_amount, total_due, bill_cleared, account_cleared) "
            "VALUES (?, ?, 1, ?, 10, 10, 10, 0, 0, 1, 1)",
            [(i, f"SCB{i}/FY2026-27", f"2026-0{4 + i % 5}-{1 + i % 28:02d}")
             for i in range(1, 601)],
        )
        conn.commit()

    def test_every_bill_is_listed_and_summed(self):
        with mock.patch.object(pages, "history_filter_choices", return_value={}), \
                mock.patch.object(pages, "_unfinished_sale_ids", return_value=set()):
            out = pages.list_sales_history(self.conn, from_date="2026-04-01", to_date=TO)
        self.assertEqual(len(out["rows"]), 600)
        self.assertEqual(out["summary"]["bills"], 600)
        self.assertAlmostEqual(out["summary"]["total"], 6000.0, places=2)
        self.assertFalse(out.get("rows_note"))


if __name__ == "__main__":
    unittest.main()
