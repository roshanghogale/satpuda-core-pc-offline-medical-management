"""A broken link to the store server must never render as a shop with no stock.

Online the engine's connection is sqlite3.connect(":memory:"). Inventory's
online block is wrapped in one try/except, and _online_read handles the reads it
wraps -- but anything raising OUTSIDE one fell to that outer except, which then
carried on into the SQL path below and queried the empty memory database. The
result was a clean, healthy-looking, completely empty Inventory with no
explanation: the "the store cleared itself and all the data is gone" report.
The ledger was on the server the whole time.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages  # noqa: E402


class WhenAnOnlineReadBlowsUpOutsideTheGuard(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            # fetch_parallel sits outside _online_read; a raise here is exactly
            # the case that used to fall through to the empty database.
            mock.patch("core.store_query_client.fetch_parallel",
                       side_effect=RuntimeError("this PC is not linked to any store")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def test_the_screen_is_told_something_broke(self):
        out = pages.list_inventory(self.conn)
        self.assertTrue(
            out.get("server_error"),
            "an empty Inventory was returned with no reason given",
        )

    def test_the_reason_reaches_the_shop(self):
        out = pages.list_inventory(self.conn)
        self.assertIn("not linked to any store", out["server_error"])

    def test_it_does_not_fall_through_to_the_memory_database(self):
        # Something in the throwaway database would be even worse than nothing:
        # it would look like real, wrong stock.
        self.conn.execute(
            "INSERT INTO medicines (id, name, batch_no, stock_qty, mrp, rate) "
            "VALUES (1, 'ZZ GHOST MED', 'G1', 5, 10, 8)"
        )
        self.conn.commit()
        out = pages.list_inventory(self.conn)
        names = [str(r) for r in out.get("rows") or []]
        self.assertFalse(
            any("ZZ GHOST MED" in n for n in names),
            "the empty :memory: database was queried and its rows shown as stock",
        )

    def test_the_screen_still_has_the_shape_it_expects(self):
        out = pages.list_inventory(self.conn)
        for key in ("columns", "rows", "row_ids", "summary", "types", "schedules"):
            self.assertIn(key, out, f"{key} missing -- the page would crash")


if __name__ == "__main__":
    unittest.main()
