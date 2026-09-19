"""A reprinted bill carries the GST % the line was sold at, not today's rate.

Offline, bill_output._load_sale_data read COALESCE(m.gst_percent, 0) -- the medicine's
rate NOW -- so after a rate change every reprint printed tax the customer was never
charged. Online, `it.get("gst_percent") or <medicine's rate>` sent a stored 0% to the
medicine's rate. Both now print the sale line's own rate; only a line that never stored
one falls back to the medicine. Everything runs on an in-memory database or a patched
server document.
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from unittest import mock

from core import bill_output, db_setup
from tests._bill_fixtures import PROFILE, isolated_bill_settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AnOfflineReprint(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        c = self.conn
        c.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
        # The medicine master has moved to 12% since these bills were made.
        c.execute(
            "INSERT INTO medicines (id, name, gst_percent, mrp, hsn_code) "
            "VALUES (1, 'CETIRIZINE 10', 12, 105, '3004')"
        )
        c.executemany(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, discount, "
            "amount_paid, cash_paid) VALUES (?, ?, 1, '2026-09-13', 105, 0, 105, 105)",
            [(1, "SCB1"), (2, "SCB2")],
        )
        c.executemany(
            "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount) "
            "VALUES (?, 1, 1, 105, ?, 105)",
            [(1, 5), (2, None)],  # sold at 5%; an old line that never stored a rate
        )

    def tearDown(self):
        self.conn.close()

    def reprint(self, sale_id: int):
        with isolated_bill_settings(), mock.patch(
            "core.pharmacy_profile_io.fetch_pharmacy_profile_row", return_value=PROFILE
        ):
            _, ctx, _ = bill_output._load_sale_data(self.conn, sale_id)
        return ctx

    def test_the_line_keeps_the_rate_it_was_sold_at(self):
        ctx = self.reprint(1)
        self.assertEqual(ctx.items[0].gst_percent, 5.0)
        self.assertEqual(
            (ctx.taxable_amount, ctx.gst_amount, ctx.cgst_amount, ctx.sgst_amount),
            (100.0, 5.0, 2.5, 2.5),
        )

    def test_a_line_that_never_stored_a_rate_uses_the_medicine(self):
        ctx = self.reprint(2)
        self.assertEqual(ctx.items[0].gst_percent, 12.0)
        self.assertEqual((ctx.taxable_amount, ctx.gst_amount), (93.75, 11.25))

    def test_the_classic_preview_reads_the_same_rate(self):
        # widgets/bill_preview.py imports tkinter at module scope, so its query is read as text.
        with open(os.path.join(ROOT, "widgets", "bill_preview.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("COALESCE(si.gst_percent, m.gst_percent, 0)", source)


class AnOnlineReprint(unittest.TestCase):
    """Online the engine's SQLite is empty and the bill comes from the server document."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)

    def tearDown(self):
        self.conn.close()

    def reprint(self, line: dict, medicine: dict):
        doc = {
            "bill_no": "SCB9", "bill_date": "2026-09-13", "customer_id": 0,
            "total_amount": 105.0, "discount": 0, "amount_paid": 105.0, "cash_paid": 105.0,
            "items": [{"medicine_id": 7, "medicine_name": "LINE", "qty": 1, "rate": 105.0,
                       "amount": 105.0, **line}],
        }
        with isolated_bill_settings(), \
                mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.server_crud.get_doc", return_value=doc), \
                mock.patch("core.online_catalog.medicine_by_id", return_value=medicine), \
                mock.patch("core.online_catalog.find_customer_by_id", return_value={}), \
                mock.patch("core.pharmacy_profile_io.fetch_pharmacy_profile_row",
                           return_value=PROFILE):
            _, ctx, _ = bill_output._load_sale_data(self.conn, 42)
        return ctx

    def test_a_line_sold_at_zero_percent_stays_zero(self):
        ctx = self.reprint({"gst_percent": 0}, {"name": "LINE", "gst_percent": 12})
        self.assertEqual(ctx.items[0].gst_percent, 0.0)
        self.assertEqual(ctx.gst_amount, 0.0)

    def test_the_stored_rate_beats_todays_rate(self):
        ctx = self.reprint({"gst_percent": 5}, {"name": "LINE", "gst_percent": 12})
        self.assertEqual((ctx.items[0].gst_percent, ctx.gst_amount), (5.0, 5.0))

    def test_a_line_without_a_rate_uses_the_medicine(self):
        ctx = self.reprint({}, {"name": "LINE", "gst_percent": 12})
        self.assertEqual((ctx.items[0].gst_percent, ctx.gst_amount), (12.0, 11.25))


if __name__ == "__main__":
    unittest.main()
