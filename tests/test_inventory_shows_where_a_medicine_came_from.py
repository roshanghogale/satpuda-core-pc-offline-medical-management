"""Inventory answers "who did we get this from?" for every medicine, not some.

The Supplier Name column existed, and filled itself by walking back through
purchase_items to the bill that brought each medicine in. That can never speak
for opening stock -- typed on the Opening Stock page or loaded from the phone,
with no purchase behind it at all -- so those rows sat blank and the column
read as broken.

Medicines carry the answer themselves now: a purchase writes it, opening stock
writes it, and the Inventory editor can be used to correct it. The derivation
stays as the fallback for a shop whose rows were filled in before any of that
existed. It remains a note: naming a supplier here creates no supplier record
and moves no due.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup
from core.desktop_pages_service import latest_supplier_by_medicine_id


class TheSupplierNameColumn(unittest.TestCase):

    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        patch = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHIWANI AGENCIES')")
        # 10: opening stock, no purchase, but it knows its supplier.
        # 11: bought on a bill, and the note agrees with the bill.
        # 12: bought on a bill long before the note existed.
        # 13: nothing at all.
        for mid, name, note in (
            (10, "CALGOPHOS", "VETCARE DISTRIBUTORS"),
            (11, "NEODOX FORTE", "SHIWANI AGENCIES"),
            (12, "BROTONE", ""),
            (13, "KAYSOL FORTE", ""),
        ):
            conn.execute(
                "INSERT INTO medicines (id, name, type, unit, stock_qty, supplier_name) "
                "VALUES (?, ?, 'Liquid', '1LTR', 1, ?)",
                (mid, name, note),
            )
        conn.execute(
            "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, bill_number) "
            "VALUES (5, 'P5', 1, '2026-09-17', '3566')"
        )
        for mid in (11, 12):
            conn.execute(
                "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate) "
                "VALUES (5, ?, 1, 10)",
                (mid,),
            )
        conn.commit()

    def names(self):
        return latest_supplier_by_medicine_id(self.conn, [10, 11, 12, 13])

    def test_opening_stock_is_not_blank_just_because_it_had_no_bill(self):
        self.assertEqual(self.names().get(10), "VETCARE DISTRIBUTORS")

    def test_a_purchased_medicine_shows_its_supplier(self):
        self.assertEqual(self.names().get(11), "SHIWANI AGENCIES")

    def test_an_older_row_still_falls_back_to_its_purchase_history(self):
        self.assertEqual(self.names().get(12), "SHIWANI AGENCIES")

    def test_a_medicine_nobody_can_speak_for_stays_empty(self):
        self.assertIn(self.names().get(13, ""), ("", None))

    def test_the_note_wins_over_the_purchase_history(self):
        """A shop that corrected the name in Inventory must not be overruled."""
        self.conn.execute(
            "UPDATE medicines SET supplier_name='CORRECTED PHARMA' WHERE id=11"
        )
        self.conn.commit()
        self.assertEqual(self.names().get(11), "CORRECTED PHARMA")

    def test_the_column_is_offered_in_settings_and_shown_by_default(self):
        from core.column_config import default_column_visibility, desktop_table_columns

        names = [n for n, _db in desktop_table_columns()["inventory"]]
        self.assertIn("Supplier Name", names)
        self.assertIsNot(default_column_visibility()["inventory"]["Supplier Name"], False)


if __name__ == "__main__":
    unittest.main()
