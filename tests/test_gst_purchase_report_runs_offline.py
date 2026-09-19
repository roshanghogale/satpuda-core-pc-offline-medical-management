"""The GST Purchase Report runs on an offline shop.

Its offline SQL read pi.medicine_name, pi.hsn and pi.gst_percent. purchase_items has
none of them (db_setup: hsn_code, gst_pct, item_amount), so every offline export failed
with "no such column: pi.medicine_name". This runs the report on the schema db_setup
builds, in memory, with a kept purchase, an autosave draft, a deleted bill and a
deleted line.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_export_service as exports  # noqa: E402

COLUMNS = ["Bill No", "Date", "Supplier", "Medicine", "HSN", "GST%", "Qty", "Rate", "Amount"]


class TheGstPurchaseReportRunsOffline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        c = self.conn
        c.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        # The medicine master says 18% / 3004 today; the line was bought at 12% / 30049099.
        c.execute(
            "INSERT INTO medicines (id, name, hsn_code, gst_percent) "
            "VALUES (1, 'PARACETAMOL 500', '3004', 18)"
        )
        c.execute(
            "INSERT INTO medicines (id, name, hsn_code, gst_percent) "
            "VALUES (2, 'ORS LEMON', '2106', 5)"
        )
        c.executemany(
            "INSERT INTO purchases (id, purchase_no, bill_number, purchase_date, "
            "supplier_id, is_autosave, deleted) VALUES (?,?,?,?,1,?,?)",
            [
                (1, "PU1", "INV-77", "2026-09-10", 0, 0),
                (2, "APU1", "DRAFT", "2026-09-11", 1, 0),   # autosave draft
                (3, "PU2", "INV-78", "2026-09-12", 0, 1),   # deleted bill
            ],
        )
        c.executemany(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, hsn_code, "
            "gst_pct, item_amount, deleted) VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 1, 10, 20.0, "30049099", 12, 224.0, 0),
                (1, 2, 5, 18.0, "21069099", 5, 94.5, 1),     # line deleted from the kept bill
                (2, 1, 1, 20.0, "30049099", 12, 22.4, 0),
                (3, 1, 1, 20.0, "30049099", 12, 22.4, 0),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def report(self, fd: str = "", td: str = "") -> dict:
        with mock.patch.object(exports, "_is_online", return_value=False), mock.patch(
            "core.column_config.get_export_column_visibility", return_value={}
        ):
            return exports._purchase_export(self.conn, "gst_purchase", fd, td)

    def test_the_report_lists_the_kept_purchase_lines(self):
        data = self.report()
        self.assertNotIn("error", data)
        self.assertEqual(data["columns"], COLUMNS)
        self.assertEqual(
            data["rows"],
            [["INV-77", "2026-09-10", "SHREE PHARMA", "PARACETAMOL 500", "30049099",
              12.0, 10.0, 20.0, 224.0]],
        )

    def test_the_date_filter_still_applies(self):
        self.assertEqual(len(self.report("2026-09-01", "2026-09-30")["rows"]), 1)
        self.assertEqual(self.report("2026-10-01", "2026-10-31")["rows"], [])


if __name__ == "__main__":
    unittest.main()
