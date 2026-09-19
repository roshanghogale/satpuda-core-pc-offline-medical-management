"""The Returns screen's Write-off tab, on a shop that keeps records on the server.

The history list and the tab badge were built by two helpers that read the
engine's own connection. Online that connection is sqlite3.connect(":memory:"),
so the tab showed "Write-offs (0)" and an empty list on a shop that had written
off stock all year -- no error, just a clean, wrong, empty screen.

stock_disposals has no /api/store route, so the sync pull is the source. When
that fails the shop is not told it has nothing; the code falls back and the
failure is printed.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages  # noqa: E402

DISPOSALS = [
    {"id": 3, "disposal_no": "WO3", "disposal_date": "2026-03-01", "quantity": 5,
     "reason": "Expired", "notes": "batch E1"},
    {"id": 1, "disposal_no": "WO1", "disposal_date": "2026-01-01", "quantity": 2,
     "reason": "Damaged", "notes": ""},
    {"id": 2, "disposal_no": "WO2", "disposal_date": "2026-02-01", "quantity": 9,
     "reason": "Expired", "notes": ""},
]


class _Online(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.store_query_client.list_sales_returns",
                       return_value={"rows": []}),
            mock.patch("core.store_query_client.list_purchase_returns",
                       return_value={"rows": []}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()


class TheWriteOffHistoryComesFromTheServer(_Online):
    def setUp(self):
        super().setUp()
        p = mock.patch("core.online_catalog.stock_disposals", return_value=DISPOSALS)
        p.start()
        self._patches.append(p)

    def test_the_badge_counts_the_shop_s_write_offs(self):
        out = pages.returns_bundle(self.conn)
        self.assertEqual(out["writeoffs"]["count"], 3, "the tab badge said zero")

    def test_the_list_is_not_empty(self):
        out = pages.returns_bundle(self.conn)
        self.assertEqual(len(out["writeoffs"]["rows"]), 3)

    def test_newest_first(self):
        out = pages.returns_bundle(self.conn)
        self.assertEqual([r[0] for r in out["writeoffs"]["rows"]], ["WO3", "WO2", "WO1"])

    def test_the_row_ids_line_up_with_the_rows(self):
        out = pages.returns_bundle(self.conn)
        self.assertEqual(out["writeoffs"]["row_ids"], [3, 2, 1])

    def test_the_columns_are_the_ones_the_screen_expects(self):
        out = pages.returns_bundle(self.conn)
        self.assertEqual(
            out["writeoffs"]["columns"],
            ["Disposal No", "Disposal Date", "Quantity", "Reason", "Notes"],
        )


class WhenTheServerCannotBeReached(_Online):
    def setUp(self):
        super().setUp()
        p = mock.patch("core.online_catalog.stock_disposals",
                       side_effect=OSError("no route to host"))
        p.start()
        self._patches.append(p)

    def test_it_does_not_crash_the_returns_screen(self):
        out = pages.returns_bundle(self.conn)
        self.assertIn("writeoffs", out)
        self.assertEqual(out["writeoffs"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
