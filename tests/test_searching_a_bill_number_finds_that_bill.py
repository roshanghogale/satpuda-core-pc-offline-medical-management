"""Searching "2" in Sales History shows bill 2 -- first (1 Oct 2026).

A sale is stored as "SCB2/FY2026-27". Searched as stored, "2" was in every bill of the year
through its "/FY2026-27": nothing was filtered, and bill 2 -- one of the year's oldest -- sat
at the bottom of the list, so the shop saw every bill but the one it typed. Only the number
on the screen ("SCB2") is searched now, and the bill whose number was typed comes first.

    python -m pytest tests/test_searching_a_bill_number_finds_that_bill.py
"""
import sqlite3
import unittest
from unittest import mock

from core import db_setup
from core import desktop_pages_service as pages


def _shop():
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name, phone) VALUES (1, 'RAMESH', '9822000000')")
    conn.execute("INSERT INTO customers (id, name, phone) VALUES (2, 'SUNITA', '9999911111')")
    for i, (no, date, cust) in enumerate((("SCB2", "2026-04-02", 2), ("SCB3", "2026-04-03", 2),
                                          ("SCB12", "2026-05-01", 2), ("SCB20", "2026-06-01", 2),
                                          ("SCB31", "2026-09-01", 1), ("SCB33", "2026-09-02", 2)), 1):
        conn.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, total_amount, fy_serial) "
                     "VALUES (?, ?, ?, ?, 100, ?)", (i, f"{no}/FY2026-27", date, cust, int(no[3:])))
    conn.commit()
    return conn


class SearchingABillNumber(unittest.TestCase):
    def search(self, q):
        conn = _shop()
        self.addCleanup(conn.close)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            out = pages.list_sales_history(conn, from_date="2026-04-01", to_date="2026-09-30", q=q)
        return [pages.history_search_rank(q, r[1]) and r[1] for r in out["rows"]], out

    def bill_nos(self, out):
        cols = out.get("columns") or out.get("headers") or []
        at = next((i for i, c in enumerate(cols) if "bill" in str(c).lower() and "no" in str(c).lower()), None)
        return [str(r[at] if at is not None else r[0]) for r in out["rows"]]

    def test_two_shows_bill_two_first_and_not_the_whole_year(self):
        _, out = self.search("2")
        nos = self.bill_nos(out)
        self.assertTrue(nos and nos[0].startswith("SCB2") and not nos[0].startswith("SCB20"), nos)
        # SCB3 and SCB33 have no 2 in the number the shop sees, nor in name or phone
        self.assertNotIn("SCB3", nos)
        self.assertNotIn("SCB33", nos)
        self.assertIn("SCB12", " ".join(nos))
        self.assertIn("SCB31", " ".join(nos))      # RAMESH's phone has a 2

    def test_the_rank(self):
        self.assertEqual(pages.history_search_rank("2", "SCB2/FY2026-27"), 2)
        self.assertEqual(pages.history_search_rank("scb2", "SCB2/FY2026-27"), 2)
        self.assertEqual(pages.history_search_rank("2", "SCB12/FY2026-27"), 1)
        self.assertEqual(pages.history_search_rank("2", "SCB3/FY2026-27"), 0)
        self.assertEqual(pages.history_search_rank("2", "SCB3/FY2026-27", "RAM", "9822"), 1)

    def test_online_rows_are_narrowed_the_same_way(self):
        rows = [{"bill_no": f"SCB{n}/FY2026-27", "customer_name": "X", "customer_phone": "9999911111"}
                for n in (40, 33, 12, 3, 2)]
        got = [r["bill_no"].split("/")[0] for r in pages._search_history_dicts(rows, "2")]
        self.assertEqual(got, ["SCB2", "SCB12"])


if __name__ == "__main__":
    unittest.main()
