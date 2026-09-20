"""A medicine says who the shop got it from.

The Opening Stock page and the loader app both ask for a supplier, because
opening stock has no bill behind it to carry one. That left the odd result that
a medicine typed in by hand knew its supplier and a medicine the shop actually
bought did not -- the Purchase screen had the name in front of it and wrote
nothing. Nine thousand medicines on the server had an empty column that their
own purchase history could have filled.

So a purchase writes the note too. It is a note and only a note: no supplier
record is created by it, no due moves, and taking stock back does not rewrite
where the stock came from.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, purchase_service

CALC_KEYS = (
    "subtotal", "total_gst", "cgst", "sgst", "total_amount", "overall_discount",
    "rounding", "need_to_pay", "final_amount", "previous_due", "previous_credit",
    "due", "current_credit", "total_due", "bill_cleared", "account_cleared",
)


def calc():
    return {k: 0 for k in CALC_KEYS}


def line(qty=3, pack="1LTR"):
    return {"medicine_id": 20, "name": "CALGOPHOS", "type": "Liquid", "batch": "GK152",
            "expiry": "04/28", "qty": qty, "free_qty": 0, "rate": 518, "mrp": 715,
            "unit": pack, "quantity_value": pack}


class AMedicineBoughtOnABill(unittest.TestCase):

    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHIWANI AGENCIES')")
        conn.execute("INSERT INTO suppliers (id, name) VALUES (2, 'VETCARE DISTRIBUTORS')")
        conn.execute(
            "INSERT INTO medicines (id, name, type, unit, stock_qty, batch_no, expiry_date, "
            "created_at) VALUES (20, 'CALGOPHOS', 'Liquid', '1LTR', 0, 'GK152', '2028-04-01', "
            "'2026-01-01 09:00:00')")
        conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.server_crud.get_doc", return_value=None),
        ):
            stack.enter_context(patch)

    def note(self):
        row = self.conn.execute(
            "SELECT supplier_name FROM medicines WHERE id=20"
        ).fetchone()
        return row[0] if row else None

    def stock(self):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=20").fetchone()[0]

    def test_remembers_the_supplier_the_bill_named(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line()])
        self.assertEqual(self.note(), "SHIWANI AGENCIES")

    def test_and_still_stocks_the_shelf(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line(qty=3)])
        self.assertEqual(self.stock(), 3)

    def test_the_next_bill_from_someone_else_wins(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line()])
        purchase_service.save_purchase(self.conn, 2, "2026-09-18", "9001", calc(), [line()])
        self.assertEqual(self.note(), "VETCARE DISTRIBUTORS")

    def test_a_bill_with_no_supplier_leaves_the_note_alone(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line()])
        purchase_service.save_purchase(self.conn, 0, "2026-09-18", "9002", calc(), [line()])
        self.assertEqual(self.note(), "SHIWANI AGENCIES", "an unnamed bill rubbed out the note")

    def test_writing_the_note_creates_no_supplier_and_no_due(self):
        before = self.conn.execute("SELECT count(*) FROM suppliers").fetchone()[0]
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line()])
        after = self.conn.execute("SELECT count(*) FROM suppliers").fetchone()[0]
        self.assertEqual(before, after)

    def test_the_medicine_keeps_everything_else_it_had(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-17", "3566", calc(), [line()])
        row = self.conn.execute(
            "SELECT name, type, unit, batch_no, expiry_date FROM medicines WHERE id=20"
        ).fetchone()
        self.assertEqual(row, ("CALGOPHOS", "Liquid", "1LTR", "GK152", "2028-04-01"))


if __name__ == "__main__":
    unittest.main()
