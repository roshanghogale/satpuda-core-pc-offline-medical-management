"""A shop whose bills all predate this financial year still sees them on History.

Sales History and Purchase History default to the current financial year. Matoshree's
store was imported on 2026-09-13 with bills that end on 31 Mar 2026, so the default
window held nothing: the page opened empty, while Purchases, Inventory and Customers
all showed their rows. The data was on the server the whole time -- 534 bills, 530 of
them live -- and the shop was told its history was lost.

The owner's rule (2026-09-16): if there is no bill at all in this financial year, show
every bill. So a default window that we applied ourselves and that holds nothing is
widened to all dates once, and ``rows_note`` says why. A window the USER typed is never
widened, and a financial year holding even one bill is left exactly as it was.

In-memory store for Offline; the store query client is patched for Online. Nothing
reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_pages_service as pages  # noqa: E402

OLD_FY = [("2025-06-11", 1), ("2025-12-02", 2), ("2026-03-31", 3)]   # before 1 Apr 2026
THIS_FY = ("2026-09-15", 9)


def _offline_shop(rows):
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
    conn.executemany(
        "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, amount_paid, "
        "cash_paid, due_amount, total_due, bill_cleared, account_cleared) "
        "VALUES (?, ?, 1, ?, 100, 100, 100, 0, 0, 1, 1)",
        [(n, f"SCB{n}/FY2025-26", day) for day, n in rows],
    )
    conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SUP')")
    conn.executemany(
        "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, final_amount) "
        "VALUES (?, ?, 1, ?, 500)",
        [(n, f"{n}/FY2025-26", day) for day, n in rows],
    )
    conn.commit()
    return conn


class _Offline(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch.object(pages, "history_filter_choices", return_value={}),
            mock.patch.object(pages, "_unfinished_sale_ids", return_value=set()),
        ):
            stack.enter_context(patch)

    def shop(self, rows):
        conn = _offline_shop(rows)
        self.addCleanup(conn.close)
        return conn


class AShopWhoseBillsAllPredateThisYear(_Offline):
    def test_sales_history_lists_every_bill_and_says_why(self):
        out = pages.list_sales_history(self.shop(OLD_FY))
        self.assertEqual(len(out["rows"]), 3, "the page opened empty, as Matoshree's did")
        self.assertTrue(out.get("history_scope_widened"))
        self.assertEqual(out["summary"]["bills"], 3)
        self.assertAlmostEqual(out["summary"]["total"], 300.0, places=2)
        note = out.get("rows_note") or ""
        self.assertIn("2026-27", note)
        self.assertIn("every date", note)

    def test_purchase_history_does_the_same(self):
        out = pages.list_purchase_history(self.shop(OLD_FY))
        self.assertEqual(len(out["rows"]), 3)
        self.assertTrue(out.get("history_scope_widened"))
        self.assertIn("every date", out.get("rows_note") or "")

    def test_a_range_the_user_typed_is_left_alone(self):
        out = pages.list_sales_history(
            self.shop(OLD_FY), from_date="2026-04-01", to_date="2026-09-16")
        self.assertEqual(out["rows"], [], "a range the user asked for must answer for itself")
        self.assertFalse(out.get("history_scope_widened"))
        self.assertFalse(out.get("rows_note"))


class AShopThatBilledThisYear(_Offline):
    def test_the_financial_year_default_still_holds(self):
        out = pages.list_sales_history(self.shop([*OLD_FY, THIS_FY]))
        self.assertEqual(len(out["rows"]), 1, "older years must not walk into this year's list")
        self.assertFalse(out.get("history_scope_widened"))
        self.assertEqual(out["filter_from"], "2026-04-01")


class AnOnlineShopWhoseBillsAllPredateThisYear(unittest.TestCase):
    """Online is a different code path: the dates go to the server, not to SQL."""

    def setUp(self):
        self.calls: list[dict] = []
        server = [{
            "id": n, "bill_no": f"SCB{n}/FY2025-26", "bill_date": day, "customer_id": 1,
            "customer_name": "RAM", "total_amount": 100.0, "amount_paid": 100.0,
            "due_amount": 0.0, "cash_paid": 100.0, "online_paid": 0.0, "discount": 0.0,
            "credit_amount": 0.0, "total_due": 0.0, "account_cleared": True,
            "bill_cleared": True, "previous_due": 0.0,
        } for day, n in OLD_FY]

        def list_sales(**kw):
            self.calls.append(dict(kw))
            fd, td = str(kw.get("from_date") or ""), str(kw.get("to_date") or "")
            rows = [r for r in server
                    if (not fd or r["bill_date"] >= fd) and (not td or r["bill_date"] <= td)]
            return {"rows": [dict(r) for r in rows], "total": len(rows),
                    "filter_from": fd, "filter_to": td, "default_fy_applied": False}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.store_query_client.list_sales", side_effect=list_sales),
            mock.patch("core.store_query_client.sales_summary", return_value={
                "total_sales": 300.0, "total_discount": 0.0, "total_profit": 0.0,
                "total_returns": 0.0, "today_revenue": 0.0, "today_cash": 0.0,
                "today_online": 0.0, "month_revenue": 0.0}),
            mock.patch("core.store_query_client.list_customer_payments", return_value={"rows": []}),
            mock.patch("core.store_query_client.list_sales_returns", return_value={"rows": []}),
            mock.patch.object(pages, "history_filter_choices", return_value={}),
        ):
            stack.enter_context(patch)

    def test_the_second_ask_carries_no_dates_and_brings_the_bills_back(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        out = pages.list_sales_history(conn)
        self.assertEqual(len(out["rows"]), 3)
        self.assertTrue(out.get("history_scope_widened"))
        self.assertEqual(str(self.calls[0].get("from_date") or ""), "2026-04-01")
        self.assertEqual(str(self.calls[-1].get("from_date") or ""), "")
        self.assertEqual(str(self.calls[-1].get("to_date") or ""), "")


if __name__ == "__main__":
    unittest.main()
