"""Inventory's Type and Schedule filters, on a shop that keeps stock on the server.

Online, list_inventory asked the server for one page of the catalogue in the
server's own alphabetical order and then filtered that page in Python. On a shop
with more batches than the page holds -- the page is capped at 10000 and defaults
to 2000 -- everything after the cut simply did not exist as far as the filters
were concerned. Pick "Type: Syrup" on a shop whose syrups sort late in the
alphabet and Inventory says the shop has none.

The Offline branch never had this: type and schedule are in its SQL WHERE, so the
page is drawn from the matching rows. The server has accepted both as query
params for a while; the desktop client just never sent them.

Stock status, expiry status and low-stock deliberately stay in Python -- they use
the shop's own per-type minimums and near-expiry months, and the server's WHERE
knows only a hardcoded 10 units and 90 days.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages  # noqa: E402

PAGE = 50  # stand-in for the real 2000 -- the point is "more rows than a page"

# 60 tablets sorting before the syrups, then the syrups. Alphabetically the
# syrups are all past a 50-row page.
CATALOG = (
    [
        {"id": i, "name": f"AAA TAB {i:03d}", "type": "Tablet", "batch_no": f"T{i}",
         "expiry_date": "2030-01-01", "stock_qty": 10, "unit": "10", "mrp": 10.0,
         "rate": 8.0, "manufacturer": "ZZ MFR", "schedule": "", "location": ""}
        for i in range(60)
    ]
    + [
        {"id": 900 + i, "name": f"ZZZ SYRUP {i:02d}", "type": "Syrup", "batch_no": f"S{i}",
         "expiry_date": "2030-01-01", "stock_qty": 5, "unit": "1", "mrp": 60.0,
         "rate": 50.0, "manufacturer": "ZZ MFR", "schedule": "H", "location": ""}
        for i in range(4)
    ]
)


def fake_server(*, q="", limit=1000, offset=0, include_total=True, hidden="0",
                type_filter="", schedule=""):
    """What the store server does: filter in SQL, THEN take the page."""
    rows = list(CATALOG)
    if type_filter.strip():
        rows = [r for r in rows if r["type"].strip().lower() == type_filter.strip().lower()]
    if schedule.strip():
        if schedule.strip().lower() == "non-scheduled":
            rows = [r for r in rows if not (r["schedule"] or "").strip()]
        else:
            rows = [r for r in rows
                    if (r["schedule"] or "").strip().lower() == schedule.strip().lower()]
    rows.sort(key=lambda r: (r["name"], r["batch_no"]))
    return {"rows": rows[offset:offset + min(int(limit or 1000), PAGE)]}


class _Online(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.spy = mock.Mock(side_effect=fake_server)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.store_query_client.list_inventory", self.spy),
            mock.patch("core.store_query_client.inventory_summary", return_value={}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def _names(self, **kw):
        res = pages.list_inventory(self.conn, limit=PAGE, **kw)
        name_col = res["columns"].index("Name")
        return [r[name_col] for r in res["rows"]]

    def _inventory_call(self):
        """The page request itself. A background medicine-merge also calls
        list_inventory, so the last call is not reliably ours."""
        for c in self.spy.call_args_list:
            if "type_filter" in c.kwargs or "schedule" in c.kwargs:
                return c.kwargs
        self.fail("the inventory page never asked the server")


class TheTypeFilterReachesPastTheFirstPage(_Online):
    def test_syrups_are_found_even_though_they_sort_after_the_page(self):
        names = self._names(type_filter="Syrup")
        self.assertEqual(
            len(names), 4,
            "the syrups sort past the page, so filtering the page found none",
        )
        self.assertTrue(all(n.startswith("ZZZ SYRUP") for n in names), names)

    def test_the_type_is_sent_to_the_server(self):
        self._names(type_filter="Syrup")
        self.assertEqual(self._inventory_call().get("type_filter"), "Syrup")

    def test_an_unfiltered_list_still_asks_for_everything(self):
        self._names()
        kw = self._inventory_call()
        self.assertIn(kw.get("type_filter", ""), ("", None))
        self.assertIn(kw.get("schedule", ""), ("", None))


class TheScheduleFilterReachesPastTheFirstPage(_Online):
    def test_schedule_h_is_found_past_the_page(self):
        names = self._names(schedule="H")
        self.assertEqual(len(names), 4, "schedule H sorts past the page")

    def test_non_scheduled_is_sent_as_itself(self):
        names = self._names(schedule="Non-Scheduled")
        self.assertEqual(self._inventory_call().get("schedule"), "Non-Scheduled")
        self.assertTrue(all(n.startswith("AAA TAB") for n in names), names[:3])


class ThresholdFiltersAreNotHandedToTheServer(_Online):
    """The server's WHERE uses a hardcoded 10 units / 90 days; the shop's own
    per-type minimums live here. Sending these would overrule the shop."""

    def test_stock_status_is_not_sent(self):
        self._names(stock_status="Low Stock")
        self.assertNotIn("stock", self._inventory_call())

    def test_expiry_status_is_not_sent(self):
        self._names(expiry_status="Near Expiry")
        self.assertNotIn("expiry", self._inventory_call())

    def test_low_only_is_not_sent(self):
        self._names(low_only=True)
        self.assertNotIn("low_stock", self._inventory_call())


if __name__ == "__main__":
    unittest.main()
